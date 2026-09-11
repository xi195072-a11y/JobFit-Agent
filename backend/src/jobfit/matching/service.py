"""Phase 3 分析编排（deterministic analysis engine）。

事务边界（§8，严格）：
    短事务（读绑定 artifact / 快照）
      → COMMIT
    事务外计算（embedding / 检索打分 / 约束·技能·计分纯函数）
      → 短事务（fencing 断言 + 结果 upsert + trace 写入）
      → COMMIT
**任何 embedding / 检索 / LLM 都不会被包在一个长事务里。**

绑定语义（§5）：只有 `analyses.resume_profile_id` / `jd_profile_id` 指向的 artifact 会被使用；
运行时**不猜** profile。未绑定时由调用方先完成抽取与绑定。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from jobfit.core.enums import RequirementType, RetrievalMethod
from jobfit.core.errors import LeaseLost, ValidationFailed
from jobfit.db.repositories import analyses as analyses_repo
from jobfit.db.repositories import results as results_repo
from jobfit.evidence.embeddings import EmbeddingProvider
from jobfit.evidence.retrieval import RetrievalHit, ensure_chunk_embeddings, retrieve_evidence
from jobfit.matching import trace as trace_mod
from jobfit.matching.constraints import ConstraintOutcome, evaluate_requirements
from jobfit.matching.facts import (
    RequirementFact,
    ResumeFacts,
    load_jd_domain,
    load_requirements,
    load_resume_facts,
)
from jobfit.matching.rules import RuleConfig
from jobfit.matching.score import ScoreResult, compute_base_score
from jobfit.matching.skills import SkillMatchOutcome, match_skill_requirements
from jobfit.observability.logging import get_logger

DEFAULT_TOP_K = 5
_LOG = get_logger(name="jobfit.matching")


@dataclass(frozen=True)
class RunContext:
    analysis_id: uuid.UUID
    pipeline_version: str
    resume_profile_id: uuid.UUID
    jd_profile_id: uuid.UUID
    resume_document_id: uuid.UUID
    jd_document_id: uuid.UUID
    cfg: RuleConfig
    facts: ResumeFacts
    requirements: list[RequirementFact]


@dataclass(frozen=True)
class ComputedAnalysis:
    constraints: list[ConstraintOutcome]
    skill_matches: list[SkillMatchOutcome]
    score: ScoreResult
    trace_records: list[tuple[str, str, dict[str, Any]]]


@dataclass
class PersistOutcome:
    constraint_count: int = 0
    skill_match_count: int = 0
    trace_count: int = 0
    score_total: float = 0.0
    gate: str = "unknown"
    flags: list[str] = field(default_factory=list)


# ------------------------------------------------------------------ 读取上下文


def load_run_context(session: Session, *, analysis_id: uuid.UUID) -> RunContext:
    """读取 analysis 及其**显式绑定**的 artifact；绑定不完整即显式失败（不猜）。"""
    analysis = analyses_repo.get_analysis(session, analysis_id)
    if analysis is None:
        raise ValidationFailed(f"analysis {analysis_id} not found")
    if analysis.resume_profile_id is None or analysis.jd_profile_id is None:
        raise ValidationFailed(
            "analysis bindings are incomplete: resume_profile_id/jd_profile_id 必须先显式绑定"
            "（Phase 3 §5：运行时不得重新猜 profile）"
        )
    cfg = RuleConfig.from_snapshot(
        analysis.config_snapshot,
        ruleset_version=analysis.ruleset_version,
        scoring_version=analysis.scoring_version,
    )
    facts = load_resume_facts(session, analysis.resume_profile_id, cfg)
    if facts.document_id != analysis.resume_document_id:
        raise ValidationFailed(
            f"bound resume profile {facts.profile_id} belongs to document {facts.document_id}, "
            f"not {analysis.resume_document_id}"
        )
    jd_row, _jd_domain = load_jd_domain(session, analysis.jd_profile_id)
    if jd_row.document_id != analysis.jd_document_id:
        raise ValidationFailed(
            f"bound jd profile {jd_row.id} belongs to document {jd_row.document_id}, "
            f"not {analysis.jd_document_id}"
        )
    requirements = load_requirements(session, analysis.jd_profile_id)
    return RunContext(
        analysis_id=analysis.id,
        pipeline_version=analysis.pipeline_version,
        resume_profile_id=analysis.resume_profile_id,
        jd_profile_id=analysis.jd_profile_id,
        resume_document_id=analysis.resume_document_id,
        jd_document_id=analysis.jd_document_id,
        cfg=cfg,
        facts=facts,
        requirements=requirements,
    )


# ------------------------------------------------------------------ 检索


def query_for_requirement(req: RequirementFact) -> str:
    """requirement 语义 → 检索 query（§17：在 **candidate resume** chunks 中找证据）。"""
    value = " ".join(str(item) for item in req.value.values() if isinstance(item, (str, int, float)))
    parts = [req.source_text or "", value]
    return " ".join(part for part in parts if part).strip() or req.req_type


def anchor_terms_for_requirement(req: RequirementFact) -> list[str]:
    """anchor gate 词表：技能类要求用技能名（distinctive term，字面命中才算证据）。

    非技能要求由结构化事实（学历/年限/地点/语言/证书）判定，不依赖检索——
    这也是"absence 不产生 FALSE"的前提（§17/§21）。
    """
    if req.req_type != RequirementType.SKILL.value:
        return []
    raw = str(req.value.get("skill") or "").strip()
    return [raw] if raw else []


def retrieve_for_requirements(
    session_factory: sessionmaker[Session],
    *,
    ctx: RunContext,
    provider: EmbeddingProvider | None,
    top_k: int = DEFAULT_TOP_K,
) -> dict[uuid.UUID, list[RetrievalHit]]:
    """为**技能类** requirement 在候选人简历 chunk 中检索证据（scope 隔离，§35）。

    非技能要求不做检索：其判定来自结构化事实，且 absence 不得被当作证据（§21）。
    """
    scope = [ctx.facts.parsed_document_id]
    targets = [req for req in ctx.requirements if anchor_terms_for_requirement(req)]

    # 短事务 1：补齐 embedding（向量计算本身在事务外）
    if provider is not None:
        with session_factory() as session:
            index = ensure_chunk_embeddings(session, provider, scope)
            if index.embedded:
                _LOG.info("chunks_embedded", count=index.embedded, model=index.model)

    # 短事务 2：逐个 requirement 检索（只读）
    out: dict[uuid.UUID, list[RetrievalHit]] = {req.id: [] for req in ctx.requirements}
    if not targets:
        return out
    with session_factory() as session:
        for req in targets:
            result = retrieve_evidence(
                session,
                query=query_for_requirement(req),
                parsed_document_ids=scope,
                top_k=top_k,
                provider=provider,
                method="auto",
                anchor_terms=anchor_terms_for_requirement(req),
                min_similarity=ctx.cfg.min_similarity,
                min_lexical_score=ctx.cfg.min_lexical_score,
            )
            out[req.id] = result.hits
            _LOG.info(
                "requirement_retrieved",
                requirement_id=str(req.id),
                method=result.retrieval_method,
                hits=len(result.hits),
            )
    return out


# ------------------------------------------------------------------ 计算（纯函数，无事务）


def compute_all(
    *, ctx: RunContext, retrieved: dict[uuid.UUID, list[RetrievalHit]]
) -> ComputedAnalysis:
    constraints = evaluate_requirements(
        requirements=ctx.requirements, facts=ctx.facts, cfg=ctx.cfg
    )
    skill_matches = match_skill_requirements(
        requirements=ctx.requirements,
        facts=ctx.facts,
        cfg=ctx.cfg,
        retrieved_by_requirement=retrieved,
    )
    score = compute_base_score(cfg=ctx.cfg, constraints=constraints, skill_matches=skill_matches)
    return ComputedAnalysis(
        constraints=constraints, skill_matches=skill_matches, score=score, trace_records=[]
    )


def evidence_ids_for(
    computed: ComputedAnalysis, retrieved: dict[uuid.UUID, list[RetrievalHit]]
) -> list[str]:
    """trace 里可能出现的全部 chunk id（用于一次性构建证据索引）。"""
    ids: list[str] = []
    for item in computed.constraints:
        ids.extend(item.evidence_ids)
    for item in computed.skill_matches:
        ids.extend(item.evidence_ids)
    for hits in retrieved.values():
        ids.extend(str(hit.source_chunk_id) for hit in hits)
    return ids


def build_trace_records(
    *,
    ctx: RunContext,
    computed: ComputedAnalysis,
    retrieved: dict[uuid.UUID, list[RetrievalHit]],
    evidence_index: dict[str, dict[str, Any]],
) -> list[tuple[str, str, dict[str, Any]]]:
    retrieved_ids = {
        req_id: [str(hit.source_chunk_id) for hit in hits] for req_id, hits in retrieved.items()
    }
    return trace_mod.build_traces(
        analysis_id=ctx.analysis_id,
        pipeline_version=ctx.pipeline_version,
        cfg=ctx.cfg,
        facts=ctx.facts,
        jd_profile_id=ctx.jd_profile_id,
        requirements=ctx.requirements,
        constraints=computed.constraints,
        skill_matches=computed.skill_matches,
        retrieved_by_requirement=retrieved_ids,
        score=computed.score,
        evidence_index=evidence_index,
    )


# ------------------------------------------------------------------ 持久化（单短事务 + fencing）


def persist_results(
    session_factory: sessionmaker[Session],
    *,
    ctx: RunContext,
    claim_token: uuid.UUID,
    computed: ComputedAnalysis,
    trace_records: list[tuple[str, str, dict[str, Any]]],
) -> PersistOutcome:
    """fencing 断言 + 幂等写入；任何一步失去 lease 都会抛 LeaseLost 且不落结果（§7）。"""
    with session_factory() as session:
        if not results_repo.guard_lease(
            session, analysis_id=ctx.analysis_id, claim_token=claim_token
        ):
            session.rollback()
            raise LeaseLost(
                f"lease lost before persisting results (analysis={ctx.analysis_id})；"
                "stale worker 不得写入 analysis-scoped 结果"
            )
        trace_ids = trace_mod.persist_traces(
            session, analysis_id=ctx.analysis_id, records=trace_records
        )
        results_repo.upsert_constraint_results(
            session,
            analysis_id=ctx.analysis_id,
            ruleset_version=ctx.cfg.ruleset_version,
            outcomes=computed.constraints,
        )
        results_repo.link_constraint_trace_ids(
            session,
            analysis_id=ctx.analysis_id,
            ruleset_version=ctx.cfg.ruleset_version,
            outcomes=computed.constraints,
            trace_ids=trace_ids,
        )
        results_repo.upsert_skill_match_results(
            session,
            analysis_id=ctx.analysis_id,
            ruleset_version=ctx.cfg.ruleset_version,
            outcomes=computed.skill_matches,
            trace_ids=trace_ids,
        )
        results_repo.upsert_score_snapshot(
            session, analysis_id=ctx.analysis_id, score=computed.score
        )
    return PersistOutcome(
        constraint_count=len(computed.constraints),
        skill_match_count=len(computed.skill_matches),
        trace_count=len(trace_records),
        score_total=computed.score.total,
        gate=computed.score.gate,
        flags=list(computed.score.flags),
    )


def default_retrieval_method(provider: EmbeddingProvider | None) -> str:
    return RetrievalMethod.VECTOR.value if provider is not None else RetrievalMethod.LEXICAL.value


__all__ = [
    "ComputedAnalysis",
    "PersistOutcome",
    "RunContext",
    "build_trace_records",
    "compute_all",
    "default_retrieval_method",
    "evidence_ids_for",
    "load_run_context",
    "persist_results",
    "query_for_requirement",
    "retrieve_for_requirements",
]

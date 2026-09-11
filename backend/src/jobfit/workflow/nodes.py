"""图节点实现（Phase 2 抽取节点 + Phase 3 确定性分析节点）。

节点一律遵守：
- 每个节点自开**短事务**（`session_factory()`），绝不在事务内做 LLM / embedding / 外部 I/O；
- analysis-scoped 写入前先做 fencing/heartbeat 断言，0 行立即 `LeaseLost`（停止后续业务写入）；
- 节点只返回 state 的增量。

Phase 2 与 Phase 3 共用同一套节点实现（不重复建设第二套执行引擎）。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from jobfit.config.settings import Settings
from jobfit.core.errors import LeaseLost, ValidationFailed
from jobfit.db.repositories import analyses as analyses_repo
from jobfit.db.repositories import documents as docs_repo
from jobfit.evidence.embeddings import EmbeddingProvider
from jobfit.evidence.retrieval import RetrievalHit
from jobfit.extraction import service as extraction_service
from jobfit.llm.provider import LLMProvider
from jobfit.matching import service as matching_service
from jobfit.matching.service import ComputedAnalysis, RunContext
from jobfit.observability.logging import get_logger
from jobfit.parsing.service import parse_document
from jobfit.workflow.state import WorkflowState

# 注意：节点工厂**不能**标注返回类型。
# LangGraph 的 `add_node` 依赖 mypy 从传入 callable 的参数类型反推 `NodeInputT`；
# 一旦工厂显式标注返回类型（哪怕就是 `Callable[[WorkflowState], dict[str, Any]]`），
# mypy 会把 `NodeInputT` 解成 `Never` 并判为 arg-type 不匹配（库侧重载限制）。
# 因此这里的工厂只标注内部 `_node(state: WorkflowState)`，返回类型交给类型推断。

_LOG = get_logger(name="jobfit.workflow")


@dataclass
class AnalysisContext:
    """图内可变上下文：保存不可进 state 的大对象（§4.2：state 只放可观测摘要）。"""

    run: RunContext | None = None
    retrieved: dict[uuid.UUID, list[RetrievalHit]] = field(default_factory=dict)
    computed: ComputedAnalysis | None = None


def _renew_lease(
    session_factory: sessionmaker[Session],
    *,
    analysis_id: uuid.UUID,
    claim_token: uuid.UUID,
    ttl_seconds: int,
) -> None:
    """heartbeat：0 行 => 已失去该 job，必须立刻停止（§7/§30）。"""
    with session_factory() as session:
        if not analyses_repo.heartbeat(session, analysis_id, claim_token, ttl_seconds):
            raise LeaseLost(
                f"heartbeat rejected (analysis={analysis_id}): lease 已过期或 claim_token 不再有效"
            )


def _require_analysis(session: Session, analysis_id: uuid.UUID):
    analysis = analyses_repo.get_analysis(session, analysis_id)
    if analysis is None:
        raise ValidationFailed("analysis row disappeared")
    return analysis


# ------------------------------------------------------------------ Phase 2 节点


def load_documents_node(session_factory: sessionmaker[Session]):
    def _node(state: WorkflowState) -> dict[str, Any]:
        with session_factory() as session:
            resume = docs_repo.get_by_id(session, uuid.UUID(state["resume_document_id"]))
            jd = docs_repo.get_by_id(session, uuid.UUID(state["jd_document_id"]))
            if resume is None or jd is None:
                raise ValidationFailed("analysis references a missing document")
            if resume.kind != "resume" or jd.kind != "jd":
                raise ValidationFailed("analysis document kinds are inconsistent")
            return {"phase": "documents_loaded"}

    return _node


def parse_documents_node(session_factory: sessionmaker[Session], settings: Settings):
    def _node(state: WorkflowState) -> dict[str, Any]:
        with session_factory() as session:
            resume = docs_repo.get_by_id(session, uuid.UUID(state["resume_document_id"]))
            jd = docs_repo.get_by_id(session, uuid.UUID(state["jd_document_id"]))
            assert resume is not None and jd is not None
            resume_parse = parse_document(session, settings, resume)
            jd_parse = parse_document(session, settings, jd)
            return {
                "resume_parsed_document_id": str(resume_parse.parsed_document_id),
                "jd_parsed_document_id": str(jd_parse.parsed_document_id),
                "phase": "parsed",
            }

    return _node


def extract_resume_node(
    session_factory: sessionmaker[Session],
    settings: Settings,
    provider: LLMProvider,
    max_llm_attempts: int,
):
    async def _node(state: WorkflowState) -> dict[str, Any]:
        with session_factory() as session:
            analysis = _require_analysis(session, uuid.UUID(state["analysis_id"]))
            outcome = await extraction_service.reuse_or_extract_resume(
                session,
                settings,
                analysis=analysis,
                claim_token=uuid.UUID(state["claim_token"]),
                provider=provider,
                max_llm_attempts=max_llm_attempts,
            )
            return {
                "resume_profile_id": str(outcome.profile_id),
                "resume_fingerprint": outcome.fingerprint,
                "resume_created": outcome.created,
                "phase": "resume_extracted",
                "errors": [*state.get("errors", []), *outcome.warnings],
            }

    return _node


def extract_jd_node(
    session_factory: sessionmaker[Session],
    settings: Settings,
    provider: LLMProvider,
    max_llm_attempts: int,
):
    async def _node(state: WorkflowState) -> dict[str, Any]:
        with session_factory() as session:
            analysis = _require_analysis(session, uuid.UUID(state["analysis_id"]))
            outcome = await extraction_service.reuse_or_extract_jd(
                session,
                settings,
                analysis=analysis,
                claim_token=uuid.UUID(state["claim_token"]),
                provider=provider,
                max_llm_attempts=max_llm_attempts,
            )
            return {
                "jd_profile_id": str(outcome.profile_id),
                "jd_fingerprint": outcome.fingerprint,
                "jd_created": outcome.created,
                "phase": "jd_extracted",
                "errors": [*state.get("errors", []), *outcome.warnings],
            }

    return _node


def finalize_extraction_node(session_factory: sessionmaker[Session]):
    def _node(state: WorkflowState) -> dict[str, Any]:
        with session_factory() as session:
            ok = analyses_repo.update_phase(
                session,
                analysis_id=uuid.UUID(state["analysis_id"]),
                claim_token=uuid.UUID(state["claim_token"]),
                phase="extraction_complete",
            )
            if not ok:
                raise ValidationFailed("lease lost before finalize (fenced write rejected)")
            return {"phase": "extraction_complete"}

    return _node


# ------------------------------------------------------------------ Phase 3 节点


def load_analysis_context_node(
    holder: AnalysisContext,
    session_factory: sessionmaker[Session],
    *,
    ttl_seconds: int,
):
    """读取并**校验绑定**：只有 analyses 上显式绑定的 profile 会被使用（§5/§6）。"""

    def _node(state: WorkflowState) -> dict[str, Any]:
        _renew_lease(
            session_factory,
            analysis_id=uuid.UUID(state["analysis_id"]),
            claim_token=uuid.UUID(state["claim_token"]),
            ttl_seconds=ttl_seconds,
        )
        with session_factory() as session:
            ctx = matching_service.load_run_context(
                session, analysis_id=uuid.UUID(state["analysis_id"])
            )
        holder.run = ctx
        return {
            "resume_profile_id": str(ctx.resume_profile_id),
            "jd_profile_id": str(ctx.jd_profile_id),
            "phase": "bindings_validated",
        }

    return _node


def index_and_retrieve_node(
    holder: AnalysisContext,
    session_factory: sessionmaker[Session],
    *,
    provider: EmbeddingProvider | None,
    top_k: int,
    ttl_seconds: int,
):
    def _node(state: WorkflowState) -> dict[str, Any]:
        _renew_lease(
            session_factory,
            analysis_id=uuid.UUID(state["analysis_id"]),
            claim_token=uuid.UUID(state["claim_token"]),
            ttl_seconds=ttl_seconds,
        )
        if holder.run is None:
            raise ValidationFailed("analysis context missing before retrieval")
        holder.retrieved = matching_service.retrieve_for_requirements(
            session_factory, ctx=holder.run, provider=provider, top_k=top_k
        )
        _LOG.info(
            "evidence_retrieved",
            analysis_id=str(holder.run.analysis_id),
            requirements=len(holder.run.requirements),
            chunks=sum(len(hits) for hits in holder.retrieved.values()),
        )
        return {"phase": "evidence_retrieved"}

    return _node


def make_compute_node(holder: AnalysisContext):
    """纯计算节点（无 DB、无 I/O）：约束 → 技能 → 计分 → trace 链。"""

    def _node(state: WorkflowState) -> dict[str, Any]:
        if holder.run is None:
            raise ValidationFailed("analysis context missing before compute")
        computed = matching_service.compute_all(ctx=holder.run, retrieved=holder.retrieved)
        holder.computed = computed
        return {
            "gate": computed.score.gate,
            "score_total": computed.score.total,
            "constraint_count": len(computed.constraints),
            "skill_match_count": len(computed.skill_matches),
            "analysis_flags": list(computed.score.flags),
            "phase": "conclusions_computed",
        }

    return _node


def make_persist_node(
    holder: AnalysisContext,
    session_factory: sessionmaker[Session],
    *,
    ttl_seconds: int,
):
    def _node(state: WorkflowState) -> dict[str, Any]:
        if holder.run is None or holder.computed is None:
            raise ValidationFailed("analysis context missing before persist")
        analysis_id = uuid.UUID(state["analysis_id"])
        claim_token = uuid.UUID(state["claim_token"])
        _renew_lease(
            session_factory,
            analysis_id=analysis_id,
            claim_token=claim_token,
            ttl_seconds=ttl_seconds,
        )
        evidence_index = _load_evidence_index(session_factory, holder.computed, holder.retrieved)
        records = matching_service.build_trace_records(
            ctx=holder.run,
            computed=holder.computed,
            retrieved=holder.retrieved,
            evidence_index=evidence_index,
        )
        outcome = matching_service.persist_results(
            session_factory,
            ctx=holder.run,
            claim_token=claim_token,
            computed=holder.computed,
            trace_records=records,
        )
        _LOG.info(
            "analysis_persisted",
            analysis_id=str(analysis_id),
            constraints=outcome.constraint_count,
            skill_matches=outcome.skill_match_count,
            traces=outcome.trace_count,
            gate=outcome.gate,
            total=outcome.score_total,
        )
        return {
            "trace_count": outcome.trace_count,
            "gate": outcome.gate,
            "score_total": outcome.score_total,
            "phase": "results_persisted",
        }

    return _node


def _load_evidence_index(
    session_factory: sessionmaker[Session],
    computed: ComputedAnalysis,
    retrieved: dict[uuid.UUID, list[RetrievalHit]],
) -> dict[str, dict[str, Any]]:
    from jobfit.matching import trace as trace_mod

    ids = matching_service.evidence_ids_for(computed, retrieved)
    with session_factory() as session:
        return trace_mod.load_evidence_index(session, ids)


def finalize_analysis_node(session_factory: sessionmaker[Session]):
    """fenced 成功终态：`status='succeeded'`（0 行 => LeaseLost，结果不被视为有效）。"""

    def _node(state: WorkflowState) -> dict[str, Any]:
        from jobfit.db.repositories import results as results_repo

        with session_factory() as session:
            ok = results_repo.mark_succeeded(
                session,
                analysis_id=uuid.UUID(state["analysis_id"]),
                claim_token=uuid.UUID(state["claim_token"]),
                phase="analysis_complete",
            )
            if not ok:
                raise LeaseLost("lease lost before marking succeeded (fenced write rejected)")
            return {"phase": "analysis_complete"}

    return _node


# ------------------------------------------------------------------ Phase 4 节点


def llm_critique_node(
    holder: AnalysisContext,
    session_factory: sessionmaker[Session],
    provider: LLMProvider | None,
    max_llm_attempts: int,
    *,
    ttl_seconds: int,
):
    """生成/复用 critique（fenced）。provider=None => 落 unavailable 记录，不伪造 live 验证。"""

    async def _node(state: WorkflowState) -> dict[str, Any]:
        from jobfit.critique import service as critique_service

        analysis_id = uuid.UUID(state["analysis_id"])
        claim_token = uuid.UUID(state["claim_token"])
        _renew_lease(
            session_factory,
            analysis_id=analysis_id,
            claim_token=claim_token,
            ttl_seconds=ttl_seconds,
        )
        outcome = await critique_service.generate_critique(
            session_factory=session_factory,
            analysis_id=analysis_id,
            claim_token=claim_token,
            provider=provider,
            max_llm_attempts=max_llm_attempts,
        )
        critique = outcome.critique
        return {
            "critique_status": critique.status if critique is not None else None,
            "critique_validation": (
                critique.validation_status if critique is not None else None
            ),
            "critique_reused": outcome.reused,
            "critique_fingerprint": outcome.fingerprint,
            "phase": "critique_done",
        }

    return _node


def compile_report_node(
    session_factory: sessionmaker[Session],
    *,
    ttl_seconds: int,
):
    """确定性装配报告 + ReportValidator 校验 + 持久化（fenced）。校验失败 => 致命失败。"""

    def _node(state: WorkflowState) -> dict[str, Any]:
        from jobfit.reports import service as report_service

        analysis_id = uuid.UUID(state["analysis_id"])
        claim_token = uuid.UUID(state["claim_token"])
        _renew_lease(
            session_factory,
            analysis_id=analysis_id,
            claim_token=claim_token,
            ttl_seconds=ttl_seconds,
        )
        outcome = report_service.build_and_validate_report(
            session_factory, analysis_id=analysis_id, claim_token=claim_token
        )
        if not outcome.valid:
            raise ValidationFailed(
                f"report validation failed (analysis={analysis_id}): {outcome.issues[:5]}"
            )
        assert outcome.report is not None
        return {
            "report_version": outcome.report.version,
            "report_stage": outcome.report.stage,
            "phase": "report_compiled",
        }

    return _node


def mark_awaiting_review_node(session_factory: sessionmaker[Session]):
    """fenced：running -> awaiting_review（Phase 4 终态，等待 HITL review）。"""

    def _node(state: WorkflowState) -> dict[str, Any]:
        from jobfit.db.repositories import reviews as reviews_repo

        with session_factory() as session:
            ok = reviews_repo.mark_awaiting_review(
                session,
                analysis_id=uuid.UUID(state["analysis_id"]),
                claim_token=uuid.UUID(state["claim_token"]),
                phase="phase4_complete",
            )
            if not ok:
                raise LeaseLost("lease lost before awaiting_review (fenced write rejected)")
            return {"phase": "awaiting_review"}

    return _node

"""Decision Trace：确定性审计记录（Phase 3 §22–§24）。

- trace 只承载 **deterministic / auditable** 决策；不含 LLM 解释（critique 属后续阶段）。
- 可重生成：链内容只由输入 artifact + ruleset_version + 结果决定；无随机、无当前时间。
- **PII-safe**：evidence 环只保存 `source_chunk_id` + 文档级字符区间 + `span_sha256`
  （chunk 文本摘要，可校验同一性）。**绝不保存简历原文/姓名/电话/邮箱**——
  Phase 3 采用 decision-trace.md §6.3 允许的更严格选项：连脱敏 excerpt 都不落库。
- JD 侧只保存**单条 requirement 的短引用**（业务文本，非个人 PII）+ anchor 定位。

写入使用 `UNIQUE(analysis_id, decision_type, decision_key)` + `ON CONFLICT DO NOTHING`，
因此重放同一 analysis 不会重复累积 trace（§25）。
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from jobfit.core.enums import DecisionType, EvidenceTier
from jobfit.db import models
from jobfit.matching.constraints import ConstraintOutcome
from jobfit.matching.facts import RequirementFact, ResumeFacts
from jobfit.matching.rules import RuleConfig
from jobfit.matching.score import ScoreResult, SectionScore
from jobfit.matching.skills import SkillMatchOutcome

_SKILL_RULE_IDS = {
    "NORMALIZED_MATCH": "skill.normalized_match",
    "CLAIMED_ONLY_MATCH": "skill.claimed_only_match",
    "RETRIEVED_EVIDENCE_PARTIAL": "skill.retrieved_partial",
    "NO_EVIDENCE_UNKNOWN": "skill.no_evidence",
    "MISSING_REQUIRED_SKILL": "skill.missing_required_skill",
    "UNSUPPORTED_REQUIREMENT_TYPE": "skill.unsupported_requirement",
}
_RULE_ID_SCORE = "score.weighted_section"


def load_evidence_index(
    session: Session, chunk_ids: Iterable[str]
) -> dict[str, dict[str, Any]]:
    """把 chunk id 映射为 **不含文本** 的证据引用（chunk id + 区间 + 摘要）。"""
    ids: list[uuid.UUID] = []
    for raw in dict.fromkeys(chunk_ids):
        try:
            ids.append(uuid.UUID(str(raw)))
        except ValueError:
            continue
    if not ids:
        return {}
    rows = session.execute(
        select(
            models.DocumentChunk.id,
            models.DocumentChunk.char_start,
            models.DocumentChunk.char_end,
            models.DocumentChunk.page,
            models.DocumentChunk.span_sha256,
        ).where(models.DocumentChunk.id.in_(ids))
    ).all()
    return {
        str(row.id): {
            "source_chunk_id": str(row.id),
            "char_start": int(row.char_start),
            "char_end": int(row.char_end),
            "page": row.page,
            "span_sha256": row.span_sha256,
        }
        for row in rows
    }


def _evidence_ring(
    evidence_ids: Sequence[str], index: Mapping[str, dict[str, Any]], tier: str
) -> list[dict[str, Any]]:
    ring: list[dict[str, Any]] = []
    for chunk_id in dict.fromkeys(str(item) for item in evidence_ids):
        entry = index.get(chunk_id)
        if entry is None:
            # 索引缺失（证据引用指向不存在的 chunk）=> 显式标注，不伪造内容
            ring.append({"source_chunk_id": chunk_id, "resolvable": False, "tier": tier})
            continue
        ring.append({**entry, "resolvable": True, "tier": tier})
    return ring


def _versions(cfg: RuleConfig, pipeline_version: str) -> dict[str, Any]:
    return {
        "pipeline_version": pipeline_version,
        "ruleset_version": cfg.ruleset_version,
        "scoring_version": cfg.scoring_version,
    }


def _requirement_ring(req: RequirementFact) -> dict[str, Any]:
    return {
        "requirement_id": str(req.id),
        "req_type": req.req_type,
        "operator": req.operator,
        "value": req.value,
        "is_hard": req.is_hard,
        # JD 侧短引用（业务文本，非个人 PII）；简历原文一律不写入 trace
        "source_text": req.source_text,
        "anchors": list(req.anchors),
    }


def _input_artifacts(facts: ResumeFacts, jd_profile_id: uuid.UUID) -> dict[str, Any]:
    return {
        "resume_profile_id": str(facts.profile_id),
        "jd_profile_id": str(jd_profile_id),
        "resume_parsed_document_id": str(facts.parsed_document_id),
    }


def build_constraint_trace(
    *,
    outcome: ConstraintOutcome,
    requirement: RequirementFact,
    facts: ResumeFacts,
    jd_profile_id: uuid.UUID,
    retrieved_ids: Sequence[str],
    evidence_index: Mapping[str, dict[str, Any]],
    cfg: RuleConfig,
    pipeline_version: str,
    gate: str,
) -> tuple[str, str, dict[str, Any]]:
    primary_tier = outcome.evidence_tier
    ring = _evidence_ring(outcome.evidence_ids, evidence_index, primary_tier)
    if retrieved_ids:
        ring.extend(_evidence_ring(retrieved_ids, evidence_index, EvidenceTier.RETRIEVED.value))
    chain = {
        "decision_key": f"req:{requirement.id}",
        "decision_type": DecisionType.CONSTRAINT.value,
        "node": "evaluate_constraints",
        "input_artifacts": _input_artifacts(facts, jd_profile_id),
        "requirement": _requirement_ring(requirement),
        "normalization": dict(outcome.normalization),
        "rule": {
            "rule_id": outcome.rule_id,
            "ruleset_version": cfg.ruleset_version,
            "params": dict(outcome.rule_params),
        },
        "evidence": ring,
        "decision": {
            "result": outcome.verdict.value,
            "basis": outcome.basis.value,
            "reason_code": outcome.reason_code,
            "explanation": outcome.explanation,
        },
        "score_contribution": {"kind": "gate", "gate": gate},
        "versions": _versions(cfg, pipeline_version),
    }
    return (DecisionType.CONSTRAINT.value, f"req:{requirement.id}", chain)


def build_skill_trace(
    *,
    outcome: SkillMatchOutcome,
    requirement: RequirementFact,
    facts: ResumeFacts,
    jd_profile_id: uuid.UUID,
    section: SectionScore | None,
    evidence_index: Mapping[str, dict[str, Any]],
    cfg: RuleConfig,
    pipeline_version: str,
) -> tuple[str, str, dict[str, Any]]:
    chain = {
        "decision_key": f"skill:{requirement.id}",
        "decision_type": DecisionType.SKILL_MATCH.value,
        "node": "match_skills",
        "input_artifacts": _input_artifacts(facts, jd_profile_id),
        "requirement": _requirement_ring(requirement),
        "normalization": {"required_skill_key": outcome.norm_used, "norm_used": outcome.norm_used},
        "rule": {
            "rule_id": _SKILL_RULE_IDS.get(outcome.reason_code, "skill.unknown_rule"),
            "ruleset_version": cfg.ruleset_version,
            "params": {"resume_skill_id": str(outcome.resume_skill_id) if outcome.resume_skill_id else None},
        },
        "evidence": _evidence_ring(outcome.evidence_ids, evidence_index, outcome.evidence_tier),
        "decision": {
            "result": outcome.status.value,
            "basis": "deterministic",
            "reason_code": outcome.reason_code,
            "explanation": outcome.explanation,
        },
        "score_contribution": {
            "kind": "section",
            "section": "skills",
            "weight": section.weight if section else None,
            "applied_weight": section.applied_weight if section else None,
            "credit": outcome.credit,
        },
        "versions": _versions(cfg, pipeline_version),
    }
    return (DecisionType.SKILL_MATCH.value, f"skill:{requirement.id}", chain)


def build_score_trace(
    *,
    section: SectionScore,
    cfg: RuleConfig,
    pipeline_version: str,
    total: float,
    analysis_id: uuid.UUID,
) -> tuple[str, str, dict[str, Any]]:
    chain = {
        "decision_key": f"section:{section.section}",
        "decision_type": DecisionType.SCORE_COMPONENT.value,
        "node": "compute_base_score",
        "input_artifacts": {"analysis_id": str(analysis_id)},
        "normalization": {"unknown_policy": dict(cfg.unknown_policy())},
        "rule": {
            "rule_id": _RULE_ID_SCORE,
            "ruleset_version": cfg.ruleset_version,
            "params": {
                "weight": section.weight,
                "applied_weight": section.applied_weight,
                "scale": cfg.scale,
                "credits": dict(cfg.scoring.get("credits", {})),
                "claimed_only_penalty": cfg.claimed_only_penalty,
            },
        },
        "evidence": [],
        "decision": {
            "result": section.status,
            "basis": "deterministic",
            "reason_code": f"SECTION_{section.status.upper()}",
            "explanation": (
                f"section={section.section} score={section.score} "
                f"determinable={section.items_determinable}/{section.items_total}"
            ),
        },
        "score_contribution": {
            "kind": "total",
            "section": section.section,
            "points": section.score,
            "weight": section.applied_weight,
            "delta_total": round(
                cfg.scale * section.applied_weight * (section.score or 0.0), 6
            ),
            "total": total,
        },
        "versions": _versions(cfg, pipeline_version),
    }
    return (DecisionType.SCORE_COMPONENT.value, f"section:{section.section}", chain)


def build_traces(
    *,
    analysis_id: uuid.UUID,
    pipeline_version: str,
    cfg: RuleConfig,
    facts: ResumeFacts,
    jd_profile_id: uuid.UUID,
    requirements: Sequence[RequirementFact],
    constraints: Sequence[ConstraintOutcome],
    skill_matches: Sequence[SkillMatchOutcome],
    retrieved_by_requirement: Mapping[uuid.UUID, Sequence[str]],
    score: ScoreResult,
    evidence_index: Mapping[str, dict[str, Any]],
) -> list[tuple[str, str, dict[str, Any]]]:
    by_id = {req.id: req for req in requirements}
    records: list[tuple[str, str, dict[str, Any]]] = []
    for outcome in constraints:
        requirement = by_id[outcome.requirement_id]
        records.append(
            build_constraint_trace(
                outcome=outcome,
                requirement=requirement,
                facts=facts,
                jd_profile_id=jd_profile_id,
                retrieved_ids=retrieved_by_requirement.get(outcome.requirement_id, []),
                evidence_index=evidence_index,
                cfg=cfg,
                pipeline_version=pipeline_version,
                gate=score.gate,
            )
        )
    skills_section = next((item for item in score.per_section if item.section == "skills"), None)
    for outcome in skill_matches:
        requirement = by_id[outcome.requirement_id]
        records.append(
            build_skill_trace(
                outcome=outcome,
                requirement=requirement,
                facts=facts,
                jd_profile_id=jd_profile_id,
                section=skills_section,
                evidence_index=evidence_index,
                cfg=cfg,
                pipeline_version=pipeline_version,
            )
        )
    for section in score.per_section:
        records.append(
            build_score_trace(
                section=section,
                cfg=cfg,
                pipeline_version=pipeline_version,
                total=score.total,
                analysis_id=analysis_id,
            )
        )
    return records


def persist_traces(
    session: Session,
    *,
    analysis_id: uuid.UUID,
    records: Sequence[tuple[str, str, dict[str, Any]]],
) -> dict[tuple[str, str], uuid.UUID]:
    """幂等写入 trace，返回 (decision_type, decision_key) -> trace_id。"""
    if records:
        stmt = (
            pg_insert(models.DecisionTrace)
            .values(
                [
                    {
                        "analysis_id": analysis_id,
                        "decision_type": decision_type,
                        "decision_key": decision_key,
                        "chain": chain,
                    }
                    for decision_type, decision_key, chain in records
                ]
            )
            .on_conflict_do_nothing(index_elements=["analysis_id", "decision_type", "decision_key"])
        )
        session.execute(stmt)
    rows = session.execute(
        select(
            models.DecisionTrace.decision_type, models.DecisionTrace.decision_key, models.DecisionTrace.id
        ).where(models.DecisionTrace.analysis_id == analysis_id)
    ).all()
    return {(row.decision_type, row.decision_key): row.id for row in rows}

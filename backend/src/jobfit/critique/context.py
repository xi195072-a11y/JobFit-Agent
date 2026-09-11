"""CritiqueContext：critique 的**受控**输入（Phase 4 §7）。

只包含完成 critique 所需的数据：
- resume/jd profile 的**非敏感**结构化摘要；
- 确定性结论：hard constraints / skill matches / score snapshot / decision traces；
- 证据元数据：source_chunk_id → 区间 + span_sha256 + PII-safe excerpt（预算内）。

禁止把整个 documents 表 / 原始 resume / 原始 JD 无控制地塞进 prompt。
证据原文一律经 observability.redact.redact_text 脱敏（ADR-013/020）。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from jobfit.db import models
from jobfit.db.repositories import results as results_repo
from jobfit.observability.redact import redact_text

EVIDENCE_EXCERPT_BUDGET = 4000  # 进 prompt 的证据摘录字符预算（受控，防过大）
MAX_EXCERPT_LEN = 240


@dataclass
class CritiqueContext:
    """critique 服务与 prompt 共用的受控上下文。"""

    analysis_id: uuid.UUID
    pipeline_version: str
    llm_model: str
    resume_profile_id: uuid.UUID
    jd_profile_id: uuid.UUID
    resume_parsed_document_id: uuid.UUID
    jd_parsed_document_id: uuid.UUID
    resume_summary: dict[str, Any]
    jd_summary: dict[str, Any]
    constraints: list[dict[str, Any]]
    skill_matches: list[dict[str, Any]]
    score: dict[str, Any]
    traces: list[dict[str, Any]]
    evidence_index: dict[str, dict[str, Any]]
    unknown_requirement_ids: list[str]
    config_snapshot: dict[str, Any] | None = None

    def to_prompt_dict(self) -> dict[str, Any]:
        return {
            "analysis_id": str(self.analysis_id),
            "pipeline_version": self.pipeline_version,
            "resume_profile": self.resume_summary,
            "jd_profile": self.jd_summary,
            "hard_constraints": self.constraints,
            "skill_matches": self.skill_matches,
            "score": self.score,
            "decision_traces": self.traces,
            "evidence_pool": self.evidence_index,
            "unknown_requirements": self.unknown_requirement_ids,
        }


def _profile_summary(session: Session, profile_id: uuid.UUID, kind: str) -> dict[str, Any]:
    model_cls = models.ResumeProfile if kind == "resume" else models.JDProfile
    row = session.get(model_cls, profile_id)
    if row is None:
        return {"missing": True, "kind": kind, "profile_id": str(profile_id)}
    full = dict(row.full_dump or {})
    _meta = dict(full.pop("_meta", {}) or {})
    # 剥离 PII 敏感字段：只保留结构化的、非敏感摘要（不含 phone/email/address/name）。
    for sensitive in ("phone", "email", "name", "address"):
        full.pop(sensitive, None)
    return {
        "kind": kind,
        "profile_id": str(profile_id),
        "schema_version": _meta.get("schema_version") or full.get("schema_version"),
        "summary": full,
    }


def _constraint_rows(session: Session, analysis_id: uuid.UUID) -> list[dict[str, Any]]:
    return [
        {
            "requirement_id": str(row.requirement_id),
            "constraint_type": row.constraint_type,
            "result": row.result,
            "basis": row.basis,
            "reason_code": row.reason_code,
            "evidence_ids": list(row.evidence_ids or []),
            "note": row.note,
        }
        for row in results_repo.list_constraint_results(session, analysis_id)
    ]


def _skill_rows(session: Session, analysis_id: uuid.UUID) -> list[dict[str, Any]]:
    return [
        {
            "jd_requirement_id": str(row.jd_requirement_id),
            "status": row.status,
            "norm_used": row.norm_used,
            "score_contribution": row.score_contribution,
            "evidence_ids": list(row.evidence_ids or []),
        }
        for row in results_repo.list_skill_match_results(session, analysis_id)
    ]


def _score_dict(session: Session, analysis_id: uuid.UUID) -> dict[str, Any]:
    snapshot = results_repo.get_score_snapshot(session, analysis_id)
    if snapshot is None:
        return {"available": False}
    return {
        "available": True,
        "total": snapshot.total,
        "per_section": list(snapshot.per_section or []),
        "flags": list(snapshot.flags or []),
        "scoring_version": snapshot.scoring_version,
    }


def _trace_dicts(session: Session, analysis_id: uuid.UUID) -> list[dict[str, Any]]:
    return [
        {"decision_type": row.decision_type, "decision_key": row.decision_key, "chain": row.chain}
        for row in results_repo.list_traces(session, analysis_id)
    ]


def _evidence_index(
    session: Session, *, resume_parsed_document_id: uuid.UUID, trace_chunk_ids: set[str]
) -> tuple[dict[str, dict[str, Any]], int]:
    """加载证据元数据 + PII-safe excerpt（预算内）。返回 (index, used_budget)。"""
    ids = {uuid.UUID(chunk_id) for chunk_id in trace_chunk_ids if _is_uuid(chunk_id)}
    if not ids:
        return {}, 0
    rows = session.execute(
        select(
            models.DocumentChunk.id,
            models.DocumentChunk.char_start,
            models.DocumentChunk.char_end,
            models.DocumentChunk.page,
            models.DocumentChunk.span_sha256,
            models.DocumentChunk.content,
        ).where(
            models.DocumentChunk.id.in_(ids),
            models.DocumentChunk.parsed_document_id == resume_parsed_document_id,
        )
    ).all()
    index: dict[str, dict[str, Any]] = {}
    used = 0
    for row in rows:
        excerpt = redact_text(str(row.content or ""))
        if used + len(excerpt) > EVIDENCE_EXCERPT_BUDGET:
            excerpt = excerpt[:MAX_EXCERPT_LEN]
        used += len(excerpt)
        index[str(row.id)] = {
            "source_chunk_id": str(row.id),
            "char_start": int(row.char_start),
            "char_end": int(row.char_end),
            "page": row.page,
            "span_sha256": row.span_sha256,
            "excerpt": excerpt,
        }
    return index, used


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
        return True
    except ValueError:
        return False


def load_critique_context(session: Session, *, analysis_id: uuid.UUID) -> CritiqueContext:
    """从 DB 组装受控 critique 输入（只读短事务）。

    仅依赖 analysis 显式绑定的 profile artifact（与 Phase 3 一致的绑定语义）。
    """
    from jobfit.db.repositories import analyses as analyses_repo

    analysis = analyses_repo.get_analysis(session, analysis_id)
    if analysis is None:
        raise ValueError(f"analysis {analysis_id} not found")
    if analysis.resume_profile_id is None or analysis.jd_profile_id is None:
        raise ValueError("analysis bindings incomplete: cannot build critique context")

    resume_summary = _profile_summary(session, analysis.resume_profile_id, "resume")
    jd_summary = _profile_summary(session, analysis.jd_profile_id, "jd")
    constraints = _constraint_rows(session, analysis_id)
    skill_matches = _skill_rows(session, analysis_id)
    score = _score_dict(session, analysis_id)
    traces = _trace_dicts(session, analysis_id)

    trace_chunk_ids: set[str] = set()
    for trace in traces:
        for ring in (trace["chain"] or {}).get("evidence", []):
            chunk_id = ring.get("source_chunk_id")
            if chunk_id:
                trace_chunk_ids.add(chunk_id)
    parsed_id = _resolve_resume_parsed_document(session, analysis)
    evidence_index, _used = _evidence_index(
        session, resume_parsed_document_id=parsed_id, trace_chunk_ids=trace_chunk_ids
    )

    unknown_ids = [
        str(row["requirement_id"]) for row in constraints if row["result"] == "UNKNOWN"
    ]

    return CritiqueContext(
        analysis_id=analysis_id,
        pipeline_version=analysis.pipeline_version,
        llm_model=analysis.llm_model or "",
        resume_profile_id=analysis.resume_profile_id,
        jd_profile_id=analysis.jd_profile_id,
        resume_parsed_document_id=parsed_id,
        jd_parsed_document_id=_resolve_jd_parsed_document(session, analysis),
        resume_summary=resume_summary,
        jd_summary=jd_summary,
        constraints=constraints,
        skill_matches=skill_matches,
        score=score,
        traces=traces,
        evidence_index=evidence_index,
        unknown_requirement_ids=unknown_ids,
        config_snapshot=dict(analysis.config_snapshot or {}),
    )


def _resolve_resume_parsed_document(
    session: Session, analysis: models.Analysis
) -> uuid.UUID:
    row = session.get(models.ResumeProfile, analysis.resume_profile_id)
    if row is None or row.parsed_document_id is None:
        raise ValueError(
            f"bound resume profile {analysis.resume_profile_id} has no parsed_document_id; "
            "critique context cannot be built"
        )
    return uuid.UUID(str(row.parsed_document_id))


def _resolve_jd_parsed_document(session: Session, analysis: models.Analysis) -> uuid.UUID:
    row = session.get(models.JDProfile, analysis.jd_profile_id)
    if row is None or row.parsed_document_id is None:
        raise ValueError(
            f"bound jd profile {analysis.jd_profile_id} has no parsed_document_id; "
            "critique context cannot be built"
        )
    return uuid.UUID(str(row.parsed_document_id))


__all__ = ["CritiqueContext", "load_critique_context"]

"""Citation Validator（Phase 4 §10–§14/§15）：critique 发布前的非 LLM 防线。

校验（§10）：
1. citation 存在；2. 属于当前 analysis；3. source_chunk_id 正确；4. trace_id 属于当前
analysis；5. evidence hash 可验证（§12）；6. 无跨 document leakage（§11）；7. 不引用
不存在的 source；8. claim 与引用类型匹配；9. 不允许 fabricated citation。

语义校验（§14/§15）：
- UNKNOWN 保留：确定性结果为 UNKNOWN 的 requirement，critique observation 不得以
  SUPPORTED 断言确定结论；只能 uncertain/inferential/contradicted。
- 不覆盖确定性结果：本模块只标记问题，**从不修改** hard_constraint_results /
  skill_match_results / score_snapshots / decision_traces。

ValidationStore 由 DB 层构建（只含当前 analysis 作用域的证据/决策链），
validator 本身是纯函数，便于单测。
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from jobfit.core.enums import ClaimType
from jobfit.critique.schema import CritiqueEvidenceRef, CritiqueSchema
from jobfit.db import models
from jobfit.db.repositories import results as results_repo


@dataclass(frozen=True)
class ChunkRef:
    """chunk 元数据 + 原文（原文仅用于 hash 校验，validator 不输出、不落库）。"""

    source_chunk_id: str
    char_start: int
    char_end: int
    span_sha256: str
    content: str


@dataclass(frozen=True)
class ValidationStore:
    """当前 analysis 作用域的只读证据/决策链快照。"""

    analysis_id: str
    trace_ids: frozenset[str] = frozenset()
    chunks: dict[str, ChunkRef] = field(default_factory=dict)
    requirement_ids: frozenset[str] = frozenset()
    unknown_requirement_ids: frozenset[str] = frozenset()


@dataclass(frozen=True)
class CitationValidationResult:
    valid: bool
    issues: list[str] = field(default_factory=list)


def _sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
        return True
    except ValueError:
        return False


def load_validation_store(session: Session, *, analysis_id: Any) -> ValidationStore:
    """从 DB 构建当前 analysis 的 validation store（只读）。

    - trace_ids：当前 analysis 的 decision_traces；
    - chunks：当前 analysis 绑定 resume profile 的 parsed_document 下的 chunk
      （scope 隔离：其它 document 的 chunk 一律不在 store 中 => 跨 analysis 引用自然被拒）。
    - requirement_ids / unknown_requirement_ids：当前 analysis 的硬条件结果。
    """
    from jobfit.db.repositories import analyses as analyses_repo

    analysis = analyses_repo.get_analysis(session, uuid.UUID(str(analysis_id)))
    if analysis is None:
        raise ValueError(f"analysis {analysis_id} not found")
    if analysis.resume_profile_id is None:
        raise ValueError("analysis bindings incomplete: cannot build validation store")

    traces = results_repo.list_traces(session, uuid.UUID(str(analysis_id)))
    trace_ids = frozenset(str(trace.id) for trace in traces)

    profile = session.get(models.ResumeProfile, analysis.resume_profile_id)
    if profile is None or profile.parsed_document_id is None:
        raise ValueError("bound resume profile missing parsed_document_id")

    chunk_rows = session.execute(
        select(
            models.DocumentChunk.id,
            models.DocumentChunk.char_start,
            models.DocumentChunk.char_end,
            models.DocumentChunk.span_sha256,
            models.DocumentChunk.content,
        ).where(models.DocumentChunk.parsed_document_id == profile.parsed_document_id)
    ).all()
    chunks = {
        str(row.id): ChunkRef(
            source_chunk_id=str(row.id),
            char_start=int(row.char_start),
            char_end=int(row.char_end),
            span_sha256=str(row.span_sha256),
            content=str(row.content or ""),
        )
        for row in chunk_rows
    }

    constraints = results_repo.list_constraint_results(session, uuid.UUID(str(analysis_id)))
    requirement_ids = frozenset(str(row.requirement_id) for row in constraints)
    unknown_ids = frozenset(
        str(row.requirement_id) for row in constraints if row.result == "UNKNOWN"
    )

    return ValidationStore(
        analysis_id=str(analysis_id),
        trace_ids=trace_ids,
        chunks=chunks,
        requirement_ids=requirement_ids,
        unknown_requirement_ids=unknown_ids,
    )


class CitationValidator:
    """校验 critique 的全部 citation 与 UNKNOWN/确定性边界语义。"""

    def validate(
        self, critique: CritiqueSchema, store: ValidationStore
    ) -> CitationValidationResult:
        issues: list[str] = []
        seen: set[str] = set()

        def _check_ref(ref: CritiqueEvidenceRef, owner: str) -> None:
            key = owner + "|" + repr(ref.as_dict())
            if key in seen:
                return
            seen.add(key)
            issues.extend(self._validate_ref(ref, store, owner))

        for point in critique.all_points():
            for ref in point.evidence_refs:
                _check_ref(ref, f"point[{point.category.value}]")
        for obs in critique.all_observations():
            for ref in obs.evidence_refs:
                _check_ref(ref, f"observation[{obs.requirement_id}]")

        issues.extend(self._validate_semantics(critique, store))

        # 去重并保持顺序
        deduped: list[str] = []
        for issue in issues:
            if issue not in deduped:
                deduped.append(issue)
        return CitationValidationResult(valid=not deduped, issues=deduped)

    # ------------------------------------------------------------------ per-ref

    def _validate_ref(
        self, ref: CritiqueEvidenceRef, store: ValidationStore, owner: str
    ) -> list[str]:
        issues: list[str] = []
        prefix = f"citation@{owner}: "

        if ref.trace_id:
            if not _is_uuid(ref.trace_id):
                issues.append(prefix + f"invalid trace_id format: {ref.trace_id!r}")
            elif ref.trace_id not in store.trace_ids:
                issues.append(
                    prefix
                    + f"trace {ref.trace_id} 不属于当前 analysis（不存在或跨 analysis 引用）"
                )

        chunk_ids = [ref.source_chunk_id, ref.evidence_id]
        for chunk_id in chunk_ids:
            if chunk_id is None:
                continue
            if not _is_uuid(chunk_id):
                issues.append(prefix + f"invalid source id format: {chunk_id!r}")
                continue
            chunk = store.chunks.get(chunk_id)
            if chunk is None:
                issues.append(
                    prefix
                    + f"source {chunk_id} 不在当前 analysis 证据池（fabricated 或跨 document leakage）"
                )
                continue
            if ref.span_char_start is not None or ref.span_char_end is not None:
                start = ref.span_char_start if ref.span_char_start is not None else chunk.char_start
                end = ref.span_char_end if ref.span_char_end is not None else chunk.char_end
                if start < 0 or end > len(chunk.content) or end <= start:
                    issues.append(prefix + f"span [{start},{end}) 超出 chunk {chunk_id} 范围")
                    continue
                if ref.excerpt_sha256:
                    actual = _sha256_hex(chunk.content[start:end])
                    if actual != ref.excerpt_sha256:
                        issues.append(
                            prefix + f"excerpt_sha256 mismatch for chunk {chunk_id}（hash 不可验证）"
                        )
            elif ref.excerpt_sha256:
                # 无 span 时以 chunk 全量校验
                actual = _sha256_hex(chunk.content)
                if actual != ref.excerpt_sha256:
                    issues.append(prefix + f"excerpt_sha256 mismatch for chunk {chunk_id}（hash 不可验证）")

        return issues

    # ------------------------------------------------------------------ semantic

    def _validate_semantics(
        self, critique: CritiqueSchema, store: ValidationStore
    ) -> list[str]:
        issues: list[str] = []
        for obs in critique.all_observations():
            req = obs.requirement_id
            if req not in store.requirement_ids:
                issues.append(
                    f"observation[{req}]: requirement 不属于当前 analysis（fabricated requirement）"
                )
                continue
            if req in store.unknown_requirement_ids and obs.claim_type == ClaimType.SUPPORTED:
                issues.append(
                    f"observation[{req}]: 确定性结果为 UNKNOWN，critique 不得以 SUPPORTED 断言确定结论"
                )
        return issues


__all__ = [
    "ChunkRef",
    "CitationValidationResult",
    "CitationValidator",
    "ValidationStore",
    "load_validation_store",
]

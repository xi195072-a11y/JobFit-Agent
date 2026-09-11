# mypy: disable-error-code="arg-type,attr-defined,assignment,union-attr"
"""unit: CitationValidator 纯函数（Phase 4 §10–§15）——不依赖 DB。

validator 只依赖 ValidationStore（证据池/决策链快照），覆盖：
- 合法引用通过；
- fabricated / 跨 analysis 的 trace_id、source_chunk_id 拒绝；
- span 越界 / excerpt hash 不匹配拒绝；
- UNKNOWN requirement 不得以 SUPPORTED 断言（injection 防线）；
- fabricated requirement 拒绝。
"""

from __future__ import annotations

import hashlib
import uuid

from jobfit.core.enums import ClaimType, CritiqueCategory
from jobfit.critique.schema import CritiqueEvidenceRef, CritiqueObservation, CritiquePoint, CritiqueSchema
from jobfit.critique.validation import (
    ChunkRef,
    CitationValidator,
    ValidationStore,
)

CHUNK_TEXT = "使用 Python 与 FastAPI 开发推荐服务接口，负责 PostgreSQL 数据建模与查询优化。"
TRACE = str(uuid.uuid4())
CHUNK = str(uuid.uuid4())
REQ = str(uuid.uuid4())
UNKNOWN_REQ = str(uuid.uuid4())
FOREIGN_CHUNK = str(uuid.uuid4())


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _store(*, unknown_requirement_ids: set[str] | None = None) -> ValidationStore:
    return ValidationStore(
        analysis_id="analysis-a",
        trace_ids=frozenset({TRACE}),
        chunks={
            CHUNK: ChunkRef(
                source_chunk_id=CHUNK,
                char_start=0,
                char_end=len(CHUNK_TEXT),
                span_sha256=_sha256(CHUNK_TEXT),
                content=CHUNK_TEXT,
            )
        },
        requirement_ids=frozenset({REQ, UNKNOWN_REQ}),
        unknown_requirement_ids=frozenset(unknown_requirement_ids or {UNKNOWN_REQ}),
    )


def _ref(**kw) -> CritiqueEvidenceRef:
    base = {"source_chunk_id": CHUNK}
    base.update(kw)
    return CritiqueEvidenceRef(**base)


def _critique(
    *,
    points: list[CritiquePoint] | None = None,
    observations: list[CritiqueObservation] | None = None,
) -> CritiqueSchema:
    return CritiqueSchema(
        overall_assessment="ok",
        strengths=points or [],
        constraint_observations=observations or [],
        unknown_acknowledgements=[],
    )


def _supported_point(*, refs: list[CritiqueEvidenceRef]) -> CritiquePoint:
    return CritiquePoint(
        category=CritiqueCategory.STRENGTH,
        claim="学历满足岗位要求。",
        claim_type=ClaimType.SUPPORTED,
        evidence_refs=refs,
    )


# ---------------------------------------------------------------- 合法引用


def test_valid_citation_passes() -> None:
    ref = _ref(span_char_start=0, span_char_end=10, excerpt_sha256=_sha256(CHUNK_TEXT[0:10]))
    point = _supported_point(refs=[ref])
    obs = CritiqueObservation(
        requirement_id=REQ,
        observation="满足。",
        claim_type=ClaimType.SUPPORTED,
        evidence_refs=[ref],
    )
    result = CitationValidator().validate(_critique(points=[point], observations=[obs]), _store())
    assert result.valid, result.issues


def test_valid_trace_only_reference_passes() -> None:
    ref = CritiqueEvidenceRef(trace_id=TRACE)
    point = _supported_point(refs=[ref])
    result = CitationValidator().validate(_critique(points=[point]), _store())
    assert result.valid, result.issues


# ---------------------------------------------------------------- fabricated / cross-analysis


def test_fabricated_trace_rejected() -> None:
    ref = CritiqueEvidenceRef(trace_id=str(uuid.uuid4()))
    point = _supported_point(refs=[ref])
    result = CitationValidator().validate(_critique(points=[point]), _store())
    assert not result.valid
    assert any("trace" in issue and "不属于当前 analysis" in issue for issue in result.issues)


def test_chunk_not_in_pool_rejected() -> None:
    ref = CritiqueEvidenceRef(source_chunk_id=str(uuid.uuid4()))
    point = _supported_point(refs=[ref])
    result = CitationValidator().validate(_critique(points=[point]), _store())
    assert not result.valid
    assert any("fabricated" in issue or "跨 document" in issue for issue in result.issues)


def test_non_uuid_source_id_rejected() -> None:
    ref = CritiqueEvidenceRef(source_chunk_id="not-a-uuid")
    point = _supported_point(refs=[ref])
    result = CitationValidator().validate(_critique(points=[point]), _store())
    assert not result.valid
    assert any("invalid source id format" in issue for issue in result.issues)


def test_span_out_of_range_rejected() -> None:
    ref = _ref(span_char_start=0, span_char_end=len(CHUNK_TEXT) + 10)
    point = _supported_point(refs=[ref])
    result = CitationValidator().validate(_critique(points=[point]), _store())
    assert not result.valid
    assert any("超出 chunk" in issue for issue in result.issues)


def test_excerpt_hash_mismatch_rejected() -> None:
    ref = _ref(span_char_start=0, span_char_end=10, excerpt_sha256=_sha256("wrong text"))
    point = _supported_point(refs=[ref])
    result = CitationValidator().validate(_critique(points=[point]), _store())
    assert not result.valid
    assert any("excerpt_sha256 mismatch" in issue for issue in result.issues)


def test_excerpt_hash_without_span_uses_full_chunk() -> None:
    ref = _ref(excerpt_sha256=_sha256(CHUNK_TEXT))
    point = _supported_point(refs=[ref])
    result = CitationValidator().validate(_critique(points=[point]), _store())
    assert result.valid, result.issues


# ---------------------------------------------------------------- 语义防线（UNKNOWN / fabricated requirement）


def test_supported_assertion_on_unknown_requirement_rejected() -> None:
    obs = CritiqueObservation(
        requirement_id=UNKNOWN_REQ,
        observation="该硬条件满足。",
        claim_type=ClaimType.SUPPORTED,
        evidence_refs=[_ref()],
    )
    result = CitationValidator().validate(_critique(observations=[obs]), _store())
    assert not result.valid
    assert any("UNKNOWN" in issue and "SUPPORTED" in issue for issue in result.issues)


def test_uncertain_acknowledgement_on_unknown_requirement_allowed() -> None:
    obs = CritiqueObservation(
        requirement_id=UNKNOWN_REQ,
        observation="证据不足，需补充材料。",
        claim_type=ClaimType.UNCERTAIN,
        evidence_refs=[_ref()],
    )
    result = CitationValidator().validate(_critique(observations=[obs]), _store())
    assert result.valid, result.issues


def test_fabricated_requirement_rejected() -> None:
    obs = CritiqueObservation(
        requirement_id=str(uuid.uuid4()),
        observation="满足。",
        claim_type=ClaimType.SUPPORTED,
        evidence_refs=[_ref()],
    )
    result = CitationValidator().validate(_critique(observations=[obs]), _store())
    assert not result.valid
    assert any("fabricated requirement" in issue for issue in result.issues)


def test_issues_are_deduplicated() -> None:
    ref = CritiqueEvidenceRef(source_chunk_id=FOREIGN_CHUNK)
    points = [_supported_point(refs=[ref]) for _ in range(3)]
    result = CitationValidator().validate(_critique(points=points), _store())
    assert not result.valid
    assert result.issues.count(result.issues[0]) == 1  # 同 ref 只报一次

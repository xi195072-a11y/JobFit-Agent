# mypy: disable-error-code="arg-type,attr-defined,assignment,union-attr,call-arg"
"""unit: CritiqueSchema 结构化输出校验（Phase 4 §6/§13）——不依赖 DB。

LLM 输出必须经 Pydantic 严格校验后才能进入 domain layer（ADR-002）：
- SUPPORTED claim 必须有 citation；
- evidence ref 至少携带一个引用字段；
- 未知字段一律拒绝（extra=forbid）。
"""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from jobfit.core.enums import ClaimType, CritiqueCategory
from jobfit.critique.schema import (
    SCHEMA_VERSION,
    CritiqueEvidenceRef,
    CritiqueObservation,
    CritiquePoint,
    CritiqueSchema,
)

CHUNK = str(uuid.uuid4())


def _ref(**kw) -> CritiqueEvidenceRef:
    base = {"source_chunk_id": CHUNK}
    base.update(kw)
    return CritiqueEvidenceRef(**base)


def _point(**kw) -> CritiquePoint:
    base: dict = {
        "category": CritiqueCategory.STRENGTH,
        "claim": "学历满足岗位要求。",
        "claim_type": ClaimType.SUPPORTED,
        "evidence_refs": [_ref()],
    }
    base.update(kw)
    return CritiquePoint(**base)


def test_valid_schema_round_trip() -> None:
    schema = CritiqueSchema(
        overall_assessment="总体匹配良好。",
        strengths=[_point()],
        gaps=[_point(category=CritiqueCategory.GAP, claim_type=ClaimType.INFERENTIAL, evidence_refs=[])],
        constraint_observations=[
            CritiqueObservation(
                requirement_id=str(uuid.uuid4()),
                observation="满足学历要求。",
                claim_type=ClaimType.SUPPORTED,
                evidence_refs=[_ref()],
            )
        ],
        unknown_acknowledgements=["req-unknown"],
    )
    assert schema.schema_version == SCHEMA_VERSION
    assert len(schema.all_points()) == 2
    assert len(schema.all_observations()) == 1
    assert len(schema.all_evidence_refs()) == 2
    payload = schema.as_dict()
    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["constraint_observations"][0]["claim_type"] == "supported"


def test_supported_point_requires_evidence_ref() -> None:
    with pytest.raises(ValidationError):
        _point(evidence_refs=[])


def test_evidence_ref_requires_at_least_one_field() -> None:
    with pytest.raises(ValidationError):
        CritiqueEvidenceRef()


def test_unknown_extra_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        CritiquePoint(
            category=CritiqueCategory.STRENGTH,
            claim="x",
            claim_type=ClaimType.SUPPORTED,
            evidence_refs=[_ref()],
            injected="ignore-previous-instructions",
        )


def test_unknown_schema_level_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        CritiqueSchema.model_validate(
            {
                "schema_version": SCHEMA_VERSION,
                "overall_assessment": "x",
                "override_hard_constraints": [{"requirement_id": "r1", "result": "MET"}],
            }
        )


def test_inferential_point_without_evidence_is_allowed() -> None:
    point = _point(claim_type=ClaimType.INFERENTIAL, evidence_refs=[])
    assert point.claim_type == ClaimType.INFERENTIAL


def test_contradicted_point_carries_citation() -> None:
    point = _point(
        claim_type=ClaimType.CONTRADICTED,
        claim="与确定性结果矛盾，需要人工复核。",
    )
    assert point.claim_type == ClaimType.CONTRADICTED


def test_span_bounds_must_be_non_negative() -> None:
    with pytest.raises(ValidationError):
        _ref(span_char_start=-1)
    _ref(span_char_start=0, span_char_end=5)  # 合法

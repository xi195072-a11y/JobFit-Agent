"""domain schema / structured output validation（Pydantic v2）。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from jobfit.core.enums import ConstraintBasis, RequirementType, Verdict
from jobfit.core.errors import StructuredOutputError
from jobfit.core.schemas import (
    HardConstraintResult,
    JDProfile,
    JDRequirement,
    ResumeProfile,
    SchemaVersions,
    combined_extraction_schema,
)
from jobfit.llm.structured import parse_structured_json

MINIMAL_RESUME = {
    "document_id": "doc-1",
    "parsed_document_id": "parsed-1",
    "education": [],
    "experiences": [],
    "skills": [],
}


def test_resume_profile_accepts_minimal() -> None:
    profile = ResumeProfile.model_validate(MINIMAL_RESUME)
    assert profile.SCHEMA_VERSION == "resume.v1"
    assert profile.extraction_warnings == []


def test_resume_profile_forbids_extra() -> None:
    with pytest.raises(ValidationError):
        ResumeProfile.model_validate({**MINIMAL_RESUME, "unknown_field": 1})


def test_schema_version_contract() -> None:
    sv = SchemaVersions()
    assert combined_extraction_schema(sv) == "resume.v1|jd.v1"
    assert JDProfile.SCHEMA_VERSION == "jd.v1"
    assert sv.extraction_versions()["jd"] == "jd.v1"


def test_hard_constraint_verdict_enum() -> None:
    row = HardConstraintResult(
        requirement_id="r-1",
        req_type=RequirementType.DEGREE,
        result=Verdict.UNKNOWN,
        basis=ConstraintBasis.DETERMINISTIC,
    )
    assert row.result is Verdict.UNKNOWN
    with pytest.raises(ValidationError):
        HardConstraintResult(
            requirement_id="r-1",
            req_type=RequirementType.DEGREE,
            result="maybe",  # type: ignore[arg-type]
            basis=ConstraintBasis.DETERMINISTIC,
        )


def test_jd_requirement_operator_and_source() -> None:
    req = JDRequirement(
        req_type=RequirementType.YEARS_EXPERIENCE,
        operator=">=",
        value={"years": 3},
        is_hard=True,
        source_text="至少 3 年",
    )
    assert req.is_hard is True
    assert req.weight == 1.0


# ---------------------------------------------------------------- structured output

def test_structured_output_valid() -> None:
    import json

    payload = {"document_id": "doc-1", "parsed_document_id": "parsed-1", "requirements": []}
    parsed = parse_structured_json(json.dumps(payload), JDProfile)
    assert parsed.document_id == "doc-1"


def test_structured_output_handles_code_fence() -> None:
    body = "```json\n" + '{"document_id":"d","parsed_document_id":"p"}\n```'
    parsed = parse_structured_json(body, ResumeProfile)
    assert parsed.document_id == "d"


def test_structured_output_invalid_json_raises() -> None:
    with pytest.raises(StructuredOutputError):
        parse_structured_json("{not json", ResumeProfile)


def test_structured_output_schema_mismatch_raises() -> None:
    with pytest.raises(StructuredOutputError):
        parse_structured_json('{"document_id": "d"}', ResumeProfile)  # 缺 parsed_document_id

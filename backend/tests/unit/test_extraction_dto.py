# mypy: disable-error-code="arg-type,attr-defined,assignment,union-attr,index"
"""LLM DTO 单测：严格 schema、缺失即 None/[]（UNKNOWN 语义）、禁止额外字段。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from jobfit.core.errors import StructuredOutputError
from jobfit.extraction.dto import LLMJdProfile, LLMRequirement, LLMResumeProfile
from jobfit.llm.structured import parse_structured_json
from support import JD_PAYLOAD, RESUME_PAYLOAD


def test_resume_dto_accepts_fixture_payload() -> None:
    dto = LLMResumeProfile.model_validate(RESUME_PAYLOAD)
    assert dto.education[0].school == "南京大学"
    assert dto.certifications == []


def test_missing_fields_remain_unknown_not_false() -> None:
    dto = LLMResumeProfile.model_validate(
        {
            "education": [
                {
                    "school": "南京大学",
                    "evidence_quotes": ["南京大学"],
                }
            ],
            "skills": [{"skill_raw": "Python", "evidence_quotes": ["Python"]}],
        }
    )
    edu = dto.education[0]
    assert edu.degree is None and edu.gpa is None and edu.major is None
    assert dto.skills[0].proficiency is None
    assert dto.location is None
    assert dto.experiences == []


def test_evidence_quotes_must_not_be_empty() -> None:
    with pytest.raises(ValidationError):
        LLMResumeProfile.model_validate(
            {"skills": [{"skill_raw": "Python", "evidence_quotes": []}]}
        )


def test_extra_field_forbidden() -> None:
    with pytest.raises(ValidationError):
        LLMResumeProfile.model_validate({"unexpected": 1})


def test_jd_requirement_requires_source_quote() -> None:
    with pytest.raises(ValidationError):
        LLMRequirement(req_type="skill", is_hard=True, source_quote="")


def test_jd_dto_parses_via_structured_helper() -> None:
    import json

    dto = parse_structured_json(json.dumps(JD_PAYLOAD, ensure_ascii=False), LLMJdProfile)
    assert dto.company == "腾讯科技"
    assert len(dto.requirements) == 4


def test_jd_dto_rejects_unknown_requirement_type() -> None:
    with pytest.raises(StructuredOutputError):
        parse_structured_json(
            '{"requirements":[{"req_type":"salary","is_hard":true,"source_quote":"x"}]}',
            LLMJdProfile,
        )


def test_jd_missing_hard_flags_default_false_not_true() -> None:
    dto = LLMJdProfile.model_validate(
        {"requirements": [{"req_type": "skill", "source_quote": "熟悉 Python"}]}
    )
    assert dto.requirements[0].is_hard is False
    assert dto.requirements[0].value == {}

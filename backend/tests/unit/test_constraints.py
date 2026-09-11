"""unit: 硬条件判定语义（Phase 3 §9–§13/§34）。

核心不变量：**没有证据 ≠ FALSE**（ADR-008）。每个 category 都必须分别验证
TRUE / FALSE / UNKNOWN 三条路径。
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

import pytest

from jobfit.core.enums import ConstraintReason, RequirementType, Verdict
from jobfit.matching.constraints import (
    ConstraintOutcome,
    evaluate_requirement,
    evaluate_requirements,
    gate_verdict,
)
from jobfit.matching.facts import (
    LanguageFact,
    RequirementFact,
    ResumeFacts,
    ResumeSkillFact,
    _union_days,
)

_DOC = uuid.uuid4()


def _facts(**overrides: Any) -> ResumeFacts:
    base: dict[str, Any] = {
        "profile_id": uuid.UUID(int=1),
        "document_id": _DOC,
        "parsed_document_id": uuid.UUID(int=2),
        "degree_level": None,
        "degree_raw": None,
        "education_evidence_ids": (),
        "experience_years": None,
        "experience_evidence_ids": (),
        "location_raw": None,
        "location_norm": None,
        "languages": (),
        "certifications": (),
        "skills": (),
        "warnings": (),
    }
    base.update(overrides)
    return ResumeFacts(**base)


def _req(req_type: str, value: dict, *, operator: str = "unspecified", is_hard: bool = True) -> RequirementFact:
    return RequirementFact(
        id=uuid.uuid4(),
        req_type=req_type,
        operator=operator,
        value=value,
        is_hard=is_hard,
        source_text=None,
        anchors=(),
    )


def _skill(name: str, *, claimed_only: bool = False, evidence: tuple[str, ...] = ("chunk-1",)):
    return ResumeSkillFact(
        id=uuid.uuid4(), skill_raw=name, claimed_only=claimed_only, evidence_ids=evidence
    )


# ---------------------------------------------------------------- degree


def test_degree_met_not_met_unknown(rule_config) -> None:
    req = _req(RequirementType.DEGREE.value, {"degree": "本科"}, operator=">=")

    met = evaluate_requirement(req, facts=_facts(degree_level=2, degree_raw="本科"), cfg=rule_config)
    assert met.verdict is Verdict.MET
    assert met.reason_code == ConstraintReason.DEGREE_AT_LEAST_MET.value
    assert met.is_hard is True

    below = evaluate_requirement(req, facts=_facts(degree_level=1, degree_raw="大专"), cfg=rule_config)
    assert below.verdict is Verdict.NOT_MET
    assert below.reason_code == ConstraintReason.DEGREE_BELOW_REQUIRED.value

    # 简历没有可比学历 => UNKNOWN（缺失不等于不满足）
    unknown = evaluate_requirement(req, facts=_facts(), cfg=rule_config)
    assert unknown.verdict is Verdict.UNKNOWN
    assert unknown.reason_code == ConstraintReason.DEGREE_UNKNOWN.value


# ---------------------------------------------------------------- years of experience


def test_experience_met_not_met_unknown(rule_config) -> None:
    req = _req(RequirementType.YEARS_EXPERIENCE.value, {"years": 3}, operator=">=")

    met = evaluate_requirement(req, facts=_facts(experience_years=4.5), cfg=rule_config)
    assert met.verdict is Verdict.MET
    assert met.reason_code == ConstraintReason.EXPERIENCE_THRESHOLD_MET.value

    below = evaluate_requirement(req, facts=_facts(experience_years=1.0), cfg=rule_config)
    assert below.verdict is Verdict.NOT_MET
    assert below.reason_code == ConstraintReason.EXPERIENCE_BELOW_THRESHOLD.value

    unknown = evaluate_requirement(req, facts=_facts(experience_years=None), cfg=rule_config)
    assert unknown.verdict is Verdict.UNKNOWN
    assert unknown.reason_code == ConstraintReason.EXPERIENCE_UNKNOWN.value


def test_experience_boundary_is_inclusive(rule_config) -> None:
    req = _req(RequirementType.YEARS_EXPERIENCE.value, {"years": 3}, operator=">=")
    outcome = evaluate_requirement(req, facts=_facts(experience_years=3.0), cfg=rule_config)
    assert outcome.verdict is Verdict.MET


def test_union_days_does_not_double_count_overlap() -> None:
    assert _union_days([(date(2020, 1, 1), date(2021, 1, 1))]) == 366.0
    overlapped = _union_days(
        [(date(2020, 1, 1), date(2021, 1, 1)), (date(2020, 6, 1), date(2021, 6, 1))]
    )
    assert overlapped == 517.0  # 2020-01-01 → 2021-06-01
    assert _union_days([]) == 0.0


# ---------------------------------------------------------------- skill


def test_skill_met_or_unknown_never_not_met(rule_config) -> None:
    req = _req(RequirementType.SKILL.value, {"skill": "Python"}, operator="has")

    met = evaluate_requirement(req, facts=_facts(skills=(_skill("python3"),)), cfg=rule_config)
    assert met.verdict is Verdict.MET
    assert met.reason_code == ConstraintReason.SKILL_EVIDENCE_PRESENT.value

    # 技能列表非穷尽 => 缺失只能是 UNKNOWN，绝不能是 FALSE（§10）
    unknown = evaluate_requirement(req, facts=_facts(skills=(_skill("Java"),)), cfg=rule_config)
    assert unknown.verdict is Verdict.UNKNOWN
    assert unknown.reason_code == ConstraintReason.SKILL_UNKNOWN.value


def test_skill_python_does_not_match_pytorch(rule_config) -> None:
    req = _req(RequirementType.SKILL.value, {"skill": "Python"}, operator="has")
    outcome = evaluate_requirement(req, facts=_facts(skills=(_skill("PyTorch"),)), cfg=rule_config)
    assert outcome.verdict is Verdict.UNKNOWN


# ---------------------------------------------------------------- location


def test_location_met_not_met_unknown(rule_config) -> None:
    req = _req(RequirementType.LOCATION.value, {"location": "深圳"}, operator="in")

    met = evaluate_requirement(req, facts=_facts(location_raw="深圳市", location_norm="深圳"), cfg=rule_config)
    assert met.verdict is Verdict.MET
    assert met.reason_code == ConstraintReason.LOCATION_MATCH.value

    mismatch = evaluate_requirement(
        req, facts=_facts(location_raw="北京", location_norm="北京"), cfg=rule_config
    )
    assert mismatch.verdict is Verdict.NOT_MET
    assert mismatch.reason_code == ConstraintReason.LOCATION_MISMATCH.value

    # 简历未写地点 => UNKNOWN（绝不推断 location OK/Not OK，§10）
    unknown = evaluate_requirement(req, facts=_facts(), cfg=rule_config)
    assert unknown.verdict is Verdict.UNKNOWN
    assert unknown.reason_code == ConstraintReason.LOCATION_UNKNOWN.value


def test_location_undeclared_requirement_is_unknown(rule_config) -> None:
    req = _req(RequirementType.LOCATION.value, {"location": "火星"}, operator="in")
    outcome = evaluate_requirement(req, facts=_facts(location_norm="深圳"), cfg=rule_config)
    assert outcome.verdict is Verdict.UNKNOWN


# ---------------------------------------------------------------- language


def test_language_met_not_met_unknown(rule_config) -> None:
    req = _req(
        RequirementType.LANGUAGE.value,
        {"language": "英语", "level": "CET-6"},
        operator=">=",
    )

    met = evaluate_requirement(
        req,
        facts=_facts(languages=(LanguageFact(raw="英语 CET-6", language="英语", label="CET-6", level=2),)),
        cfg=rule_config,
    )
    assert met.verdict is Verdict.MET
    assert met.reason_code == ConstraintReason.LANGUAGE_MATCH.value

    below = evaluate_requirement(
        req,
        facts=_facts(languages=(LanguageFact(raw="英语 CET-4", language="英语", label="CET-4", level=1),)),
        cfg=rule_config,
    )
    assert below.verdict is Verdict.NOT_MET
    assert below.reason_code == ConstraintReason.LANGUAGE_BELOW_REQUIRED.value

    unknown = evaluate_requirement(req, facts=_facts(languages=()), cfg=rule_config)
    assert unknown.verdict is Verdict.UNKNOWN
    assert unknown.reason_code == ConstraintReason.LANGUAGE_UNKNOWN.value


def test_language_without_declared_level_is_unknown(rule_config) -> None:
    req = _req(RequirementType.LANGUAGE.value, {"language": "英语", "level": "GRE"}, operator=">=")
    outcome = evaluate_requirement(req, facts=_facts(), cfg=rule_config)
    assert outcome.verdict is Verdict.UNKNOWN


# ---------------------------------------------------------------- certification


def test_certification_met_or_unknown(rule_config) -> None:
    req = _req(RequirementType.CERTIFICATION.value, {"name": "PMP"}, operator="has")
    met = evaluate_requirement(req, facts=_facts(certifications=("PMP",)), cfg=rule_config)
    assert met.verdict is Verdict.MET
    assert met.reason_code == ConstraintReason.CERTIFICATION_PRESENT.value

    unknown = evaluate_requirement(req, facts=_facts(), cfg=rule_config)
    assert unknown.verdict is Verdict.UNKNOWN
    assert unknown.reason_code == ConstraintReason.CERTIFICATION_UNKNOWN.value


# ---------------------------------------------------------------- unsupported / operator


@pytest.mark.parametrize("req_type", ["other", "security_clearance"])
def test_unsupported_requirement_types_are_unknown(rule_config, req_type: str) -> None:
    """未建模类别一律 UNKNOWN + 记录 architecture gap，绝不交给 LLM 猜（§11）。"""
    outcome = evaluate_requirement(_req(req_type, {"note": "visa"}), facts=_facts(), cfg=rule_config)
    assert outcome.verdict is Verdict.UNKNOWN
    assert outcome.reason_code == ConstraintReason.UNSUPPORTED_REQUIREMENT_TYPE.value


def test_unspecified_operator_uses_declared_default(rule_config) -> None:
    """operator=unspecified 时按 constraint_rules.yaml 的显式默认算子解析（不在代码里隐式决定）。"""
    req = _req(RequirementType.SKILL.value, {"skill": "Python"}, operator="unspecified")
    outcome = evaluate_requirement(req, facts=_facts(skills=(_skill("Python"),)), cfg=rule_config)
    assert outcome.verdict is Verdict.MET
    assert outcome.rule_params["operator"] == "has"


def test_disallowed_operator_is_unknown(rule_config) -> None:
    req = _req(RequirementType.DEGREE.value, {"degree": "本科"}, operator="contains")
    outcome = evaluate_requirement(req, facts=_facts(degree_level=2), cfg=rule_config)
    assert outcome.verdict is Verdict.UNKNOWN
    assert outcome.reason_code == ConstraintReason.UNSPECIFIED_OPERATOR.value


# ---------------------------------------------------------------- gate


def _outcome(verdict: Verdict, *, is_hard: bool = True) -> ConstraintOutcome:
    return ConstraintOutcome(
        requirement_id=uuid.uuid4(),
        req_type=RequirementType.DEGREE.value,
        is_hard=is_hard,
        verdict=verdict,
        reason_code="X",
        rule_id="constraint.degree_at_least",
    )


def test_gate_verdict_matrix(rule_config) -> None:
    assert gate_verdict([_outcome(Verdict.MET)], rule_config) == ("pass", [])
    blocked, flags = gate_verdict([_outcome(Verdict.NOT_MET)], rule_config)
    assert blocked == "blocked" and "HARD_CONSTRAINT_BLOCKED" in flags
    unknown, flags = gate_verdict([_outcome(Verdict.UNKNOWN)], rule_config)
    assert unknown == "unknown" and "HARD_CONSTRAINT_UNKNOWN" in flags
    # 只有软要求时不构成门禁
    assert gate_verdict([_outcome(Verdict.NOT_MET, is_hard=False)], rule_config)[0] == "not_applicable"
    # NOT_MET 优先于 UNKNOWN
    assert gate_verdict([_outcome(Verdict.NOT_MET), _outcome(Verdict.UNKNOWN)], rule_config)[0] == "blocked"


def test_evaluate_requirements_preserves_order_and_count(rule_config) -> None:
    reqs = [
        _req(RequirementType.DEGREE.value, {"degree": "本科"}, operator=">="),
        _req(RequirementType.SKILL.value, {"skill": "Python"}, operator="has"),
    ]
    outcomes = evaluate_requirements(requirements=reqs, facts=_facts(degree_level=2), cfg=rule_config)
    assert [item.requirement_id for item in outcomes] == [req.id for req in reqs]
    assert [item.verdict for item in outcomes] == [Verdict.MET, Verdict.UNKNOWN]

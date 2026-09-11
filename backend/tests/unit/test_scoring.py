"""unit: 确定性计分（Phase 3 §26/§27）。

证明两点：
1. 权重/分值/UNKNOWN 策略全部来自 `scoring.yaml`（不存在代码内硬编码权重）；
2. UNKNOWN ≠ FALSE：UNKNOWN 项不进入分母；整段无可判定项时该段被剔除且权重重新归一化。
"""

from __future__ import annotations

import dataclasses
import uuid

from jobfit.core.enums import ConstraintReason, RequirementType, SkillMatchStatus, Verdict
from jobfit.matching.constraints import ConstraintOutcome
from jobfit.matching.score import STATUS_NOT_APPLICABLE, compute_base_score
from jobfit.matching.skills import SkillMatchOutcome


def _constraint(req_type: str, verdict: Verdict) -> ConstraintOutcome:
    return ConstraintOutcome(
        requirement_id=uuid.uuid4(),
        req_type=req_type,
        is_hard=True,
        verdict=verdict,
        reason_code=ConstraintReason.DEGREE_AT_LEAST_MET.value,
        rule_id="rule",
    )


def _skill(status: SkillMatchStatus, credit: float) -> SkillMatchOutcome:
    return SkillMatchOutcome(
        requirement_id=uuid.uuid4(),
        resume_skill_id=None,
        status=status,
        reason_code="X",
        norm_used="python",
        credit=credit,
        evidence_ids=(),
        evidence_tier="absence",
        explanation="",
    )


def test_all_sections_full_score(rule_config) -> None:
    constraints = [
        _constraint(RequirementType.DEGREE.value, Verdict.MET),
        _constraint(RequirementType.YEARS_EXPERIENCE.value, Verdict.MET),
        _constraint(RequirementType.LOCATION.value, Verdict.MET),
        _constraint(RequirementType.LANGUAGE.value, Verdict.MET),
    ]
    score = compute_base_score(
        cfg=rule_config,
        constraints=constraints,
        skill_matches=[_skill(SkillMatchStatus.MATCHED, rule_config.credit("matched"))],
    )
    assert score.total == 100.0
    assert score.gate == "pass"
    assert "GATE:pass" in score.flags
    assert all(item.status != STATUS_NOT_APPLICABLE for item in score.per_section)


def test_unknown_is_excluded_and_weights_renormalized(rule_config) -> None:
    """只有 skills 可判定时，总分只由 skills 决定（不因其他字段缺失被扣分）。"""
    score = compute_base_score(
        cfg=rule_config,
        constraints=[_constraint(RequirementType.DEGREE.value, Verdict.UNKNOWN)],
        skill_matches=[_skill(SkillMatchStatus.PARTIAL, rule_config.credit("partial"))],
    )
    assert score.total == 50.0  # skills section score = 0.5，权重重新归一化后仍为 100 * 0.5
    sections = {item.section: item for item in score.per_section}
    assert sections["education"].status == STATUS_NOT_APPLICABLE
    assert sections["education"].applied_weight == 0.0
    assert "SECTION_NOT_APPLICABLE:education" in score.flags
    assert "HARD_CONSTRAINT_UNKNOWN" in score.flags


def test_unknown_skill_does_not_reduce_skills_section(rule_config) -> None:
    """skills 段内 UNKNOWN 项不进分母：1 个 MATCHED + 1 个 UNKNOWN => 段得分 1.0。"""
    score = compute_base_score(
        cfg=rule_config,
        constraints=[],
        skill_matches=[
            _skill(SkillMatchStatus.MATCHED, rule_config.credit("matched")),
            _skill(SkillMatchStatus.UNKNOWN, 0.0),
        ],
    )
    sections = {item.section: item for item in score.per_section}
    assert sections["skills"].score == 1.0
    assert sections["skills"].items_determinable == 1
    assert sections["skills"].items_total == 2
    assert "SECTION_UNKNOWN_PENDING:skills:1" in score.flags
    assert "SKILL_UNKNOWN_PRESENT" in score.flags


def test_unknown_never_scored_as_zero(rule_config) -> None:
    """UNKNOWN 的段是 not_applicable（不计入），NOT_MET 的段是 applicable 且得 0 分 —— 语义不同。"""
    unknown = compute_base_score(
        cfg=rule_config,
        constraints=[_constraint(RequirementType.DEGREE.value, Verdict.UNKNOWN)],
        skill_matches=[],
    )
    false = compute_base_score(
        cfg=rule_config,
        constraints=[_constraint(RequirementType.DEGREE.value, Verdict.NOT_MET)],
        skill_matches=[],
    )
    unknown_education = next(item for item in unknown.per_section if item.section == "education")
    false_education = next(item for item in false.per_section if item.section == "education")
    assert unknown_education.status == STATUS_NOT_APPLICABLE
    assert false_education.status != STATUS_NOT_APPLICABLE
    assert false_education.score == 0.0
    assert "NO_DETERMINABLE_SECTIONS" in unknown.flags
    assert "NO_DETERMINABLE_SECTIONS" not in false.flags


def test_hard_gate_blocked_flag(rule_config) -> None:
    score = compute_base_score(
        cfg=rule_config,
        constraints=[_constraint(RequirementType.DEGREE.value, Verdict.NOT_MET)],
        skill_matches=[],
    )
    assert score.gate == "blocked"
    assert "HARD_CONSTRAINT_BLOCKED" in score.flags
    assert "GATE:blocked" in score.flags


def test_weights_come_from_config_not_code(rule_config) -> None:
    """把 education 权重改成 0（配置改变）后，education 段被剔除 => 总分变化。"""
    constraints = [
        _constraint(RequirementType.DEGREE.value, Verdict.NOT_MET),
        _constraint(RequirementType.YEARS_EXPERIENCE.value, Verdict.MET),
    ]
    baseline = compute_base_score(cfg=rule_config, constraints=constraints, skill_matches=[])

    tweaked_scoring = dict(rule_config.scoring)
    sections = {name: dict(value) for name, value in tweaked_scoring["sections"].items()}
    sections["education"]["weight"] = 0.0
    tweaked_scoring["sections"] = sections
    tweaked = compute_base_score(
        cfg=dataclasses.replace(rule_config, scoring=tweaked_scoring),
        constraints=constraints,
        skill_matches=[],
    )
    assert baseline.total != tweaked.total
    assert tweaked.total == 100.0  # education 被剔除，仅 experience 可判定且满分

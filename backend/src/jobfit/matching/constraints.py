"""硬条件判定引擎（Phase 3 §9–§13）——**纯确定性**，绝无 LLM 参与。

三条不可违背的语义：
1. 三值：MET / NOT_MET / UNKNOWN；reason_code 是确定性枚举，不是自然语言。
2. **没有证据 ≠ FALSE**（ADR-008）：absence of evidence 只能产出 UNKNOWN。
   只有"权威事实"被明确否定时才产生 NOT_MET（判据见 matching/facts.py 模块 docstring）。
3. 未建模 / 不支持的 requirement 一律 UNKNOWN + `UNSUPPORTED_REQUIREMENT_TYPE`，
   记录 architecture gap，绝不猜测（§11）。

本模块是纯函数：不访问数据库、不读时间、不读随机数。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from jobfit.core.enums import (
    ConstraintBasis,
    ConstraintReason,
    EvidenceTier,
    RequirementType,
    Verdict,
)
from jobfit.matching.facts import RequirementFact, ResumeFacts
from jobfit.matching.normalize import (
    degree_level,
    form_normalize,
    language_level,
    normalize_location,
    skill_compare_key,
)
from jobfit.matching.rules import RuleConfig

_UNSPECIFIED = "unspecified"
_YEARS_EPSILON = 1e-9

_RULE_IDS = {
    RequirementType.DEGREE.value: "constraint.degree_at_least",
    RequirementType.YEARS_EXPERIENCE.value: "constraint.experience_threshold",
    RequirementType.SKILL.value: "constraint.skill_present",
    RequirementType.LOCATION.value: "constraint.location_in",
    RequirementType.LANGUAGE.value: "constraint.language_at_least",
    RequirementType.CERTIFICATION.value: "constraint.certification_present",
}
_RULE_ID_UNSUPPORTED = "constraint.unsupported_requirement"


@dataclass(frozen=True)
class ConstraintOutcome:
    requirement_id: uuid.UUID
    req_type: str
    is_hard: bool
    verdict: Verdict
    reason_code: str
    rule_id: str
    basis: ConstraintBasis = ConstraintBasis.DETERMINISTIC
    evidence_ids: tuple[str, ...] = ()
    evidence_tier: str = EvidenceTier.ABSENCE.value
    normalization: dict[str, Any] = field(default_factory=dict)
    rule_params: dict[str, Any] = field(default_factory=dict)
    explanation: str = ""

    @property
    def determinable(self) -> bool:
        return self.verdict is not Verdict.UNKNOWN


def _unknown(
    req: RequirementFact, *, reason: ConstraintReason, rule_id: str, explanation: str, **kwargs: Any
) -> ConstraintOutcome:
    return ConstraintOutcome(
        requirement_id=req.id,
        req_type=req.req_type,
        is_hard=req.is_hard,
        verdict=Verdict.UNKNOWN,
        reason_code=reason.value,
        rule_id=rule_id,
        explanation=explanation,
        **kwargs,
    )


def _resolve_operator(req: RequirementFact, cfg: RuleConfig) -> tuple[str | None, ConstraintOutcome | None]:
    """算子解析：unspecified 走 constraint_rules.yaml 的显式默认值。"""
    if req.req_type in cfg.unsupported_types():
        return None, _unknown(
            req,
            reason=ConstraintReason.UNSUPPORTED_REQUIREMENT_TYPE,
            rule_id=_RULE_ID_UNSUPPORTED,
            explanation=f"req_type={req.req_type!r} 未被 schema/规则建模，不猜测 => UNKNOWN",
            rule_params={"req_type": req.req_type},
        )
    rule = cfg.operator_rule(req.req_type)
    if rule is None:
        return None, _unknown(
            req,
            reason=ConstraintReason.UNSUPPORTED_REQUIREMENT_TYPE,
            rule_id=_RULE_ID_UNSUPPORTED,
            explanation=f"constraint_rules.yaml 未声明 req_type={req.req_type!r} => UNKNOWN",
            rule_params={"req_type": req.req_type},
        )
    operator = req.operator if req.operator and req.operator != _UNSPECIFIED else str(rule["default"])
    allowed = [str(item) for item in rule.get("allowed", [])]
    if operator not in allowed:
        return None, _unknown(
            req,
            reason=ConstraintReason.UNSPECIFIED_OPERATOR,
            rule_id=_RULE_IDS.get(req.req_type, _RULE_ID_UNSUPPORTED),
            explanation=f"operator={operator!r} 不在允许集合 {allowed} 内 => UNKNOWN",
            rule_params={"operator": operator, "allowed": allowed},
        )
    return operator, None


def _degree(req: RequirementFact, facts: ResumeFacts, cfg: RuleConfig, operator: str) -> ConstraintOutcome:
    rule_id = _RULE_IDS[RequirementType.DEGREE.value]
    required = req.value.get("degree_level")
    required_level = int(required) if isinstance(required, (int, float)) else degree_level(
        str(req.value.get("degree") or ""), cfg
    )
    params = {"operator": operator, "required_degree_level": required_level}
    if required_level is None:
        return _unknown(
            req,
            reason=ConstraintReason.DEGREE_UNKNOWN,
            rule_id=rule_id,
            explanation="JD 未给出可归一的学历要求 => UNKNOWN",
            rule_params=params,
        )
    if facts.degree_level is None:
        return _unknown(
            req,
            reason=ConstraintReason.DEGREE_UNKNOWN,
            rule_id=rule_id,
            explanation="简历没有可比对的学历信息 => UNKNOWN（缺失不等于不满足）",
            rule_params=params,
        )
    met = facts.degree_level >= required_level if operator == ">=" else facts.degree_level == required_level
    return ConstraintOutcome(
        requirement_id=req.id,
        req_type=req.req_type,
        is_hard=req.is_hard,
        verdict=Verdict.MET if met else Verdict.NOT_MET,
        reason_code=(
            ConstraintReason.DEGREE_AT_LEAST_MET.value if met else ConstraintReason.DEGREE_BELOW_REQUIRED.value
        ),
        rule_id=rule_id,
        evidence_ids=facts.education_evidence_ids,
        evidence_tier=(
            EvidenceTier.STRUCTURED.value if facts.education_evidence_ids else EvidenceTier.ABSENCE.value
        ),
        normalization={"resume_degree_raw": facts.degree_raw, "resume_degree_level": facts.degree_level},
        rule_params=params,
        explanation=f"resume degree_level={facts.degree_level} {operator} required={required_level}",
    )


def _experience(
    req: RequirementFact, facts: ResumeFacts, cfg: RuleConfig, operator: str
) -> ConstraintOutcome:
    rule_id = _RULE_IDS[RequirementType.YEARS_EXPERIENCE.value]
    raw_required = req.value.get("years")
    required = float(raw_required) if isinstance(raw_required, (int, float)) else None
    params = {"operator": operator, "required_years": required}
    if required is None:
        return _unknown(
            req,
            reason=ConstraintReason.EXPERIENCE_UNKNOWN,
            rule_id=rule_id,
            explanation="JD 未给出可解析的年限要求 => UNKNOWN",
            rule_params=params,
        )
    if facts.experience_years is None:
        return _unknown(
            req,
            reason=ConstraintReason.EXPERIENCE_UNKNOWN,
            rule_id=rule_id,
            explanation=(
                "工作经历缺少起止日期或为空，无法确定性计算年限 => UNKNOWN"
                "（只允许用完整 dated 经历判 NOT_MET）"
            ),
            rule_params=params,
        )
    met = facts.experience_years + _YEARS_EPSILON >= required
    return ConstraintOutcome(
        requirement_id=req.id,
        req_type=req.req_type,
        is_hard=req.is_hard,
        verdict=Verdict.MET if met else Verdict.NOT_MET,
        reason_code=(
            ConstraintReason.EXPERIENCE_THRESHOLD_MET.value
            if met
            else ConstraintReason.EXPERIENCE_BELOW_THRESHOLD.value
        ),
        rule_id=rule_id,
        evidence_ids=facts.experience_evidence_ids,
        evidence_tier=(
            EvidenceTier.STRUCTURED.value if facts.experience_evidence_ids else EvidenceTier.ABSENCE.value
        ),
        normalization={"resume_experience_years": facts.experience_years},
        rule_params=params,
        explanation=f"resume years={facts.experience_years} {operator} required={required}",
    )


def _skill(req: RequirementFact, facts: ResumeFacts, cfg: RuleConfig, operator: str) -> ConstraintOutcome:
    rule_id = _RULE_IDS[RequirementType.SKILL.value]
    required_raw = str(req.value.get("skill") or "").strip()
    params = {"operator": operator, "required_skill": required_raw}
    if not required_raw:
        return _unknown(
            req,
            reason=ConstraintReason.SKILL_UNKNOWN,
            rule_id=rule_id,
            explanation="JD 未给出技能名 => UNKNOWN",
            rule_params=params,
        )
    required_key = skill_compare_key(required_raw, cfg)
    hits = [skill for skill in facts.skills if skill_compare_key(skill.skill_raw, cfg) == required_key]
    if not hits:
        return _unknown(
            req,
            reason=ConstraintReason.SKILL_UNKNOWN,
            rule_id=rule_id,
            explanation=(
                "简历的结构化技能中无该技能 => UNKNOWN（技能列表非穷尽，缺失不等于不具备）"
            ),
            rule_params=params,
        )
    evidence = tuple(eid for skill in hits for eid in skill.evidence_ids)
    return ConstraintOutcome(
        requirement_id=req.id,
        req_type=req.req_type,
        is_hard=req.is_hard,
        verdict=Verdict.MET,
        reason_code=ConstraintReason.SKILL_EVIDENCE_PRESENT.value,
        rule_id=rule_id,
        evidence_ids=evidence,
        evidence_tier=EvidenceTier.STRUCTURED.value if evidence else EvidenceTier.ABSENCE.value,
        normalization={"required_skill_key": required_key, "matched_skill_raw": sorted(h.skill_raw for h in hits)},
        rule_params=params,
        explanation=f"structured resume skill matches {required_key}",
    )


def _location(req: RequirementFact, facts: ResumeFacts, cfg: RuleConfig, operator: str) -> ConstraintOutcome:
    rule_id = _RULE_IDS[RequirementType.LOCATION.value]
    raw_required = req.value.get("location") or req.value.get("city") or ""
    required_norm = normalize_location(str(raw_required), cfg)
    params = {"operator": operator, "required_location": str(raw_required)}
    if required_norm is None:
        return _unknown(
            req,
            reason=ConstraintReason.LOCATION_UNKNOWN,
            rule_id=rule_id,
            explanation="JD 地点不在 location_rules.yaml 已声明的规范城市中 => UNKNOWN",
            rule_params=params,
        )
    if facts.location_norm is None:
        return _unknown(
            req,
            reason=ConstraintReason.LOCATION_UNKNOWN,
            rule_id=rule_id,
            explanation="简历没有可归一化的地点 => UNKNOWN（不推断 location OK/Not OK）",
            rule_params=params,
        )
    met = required_norm == facts.location_norm
    return ConstraintOutcome(
        requirement_id=req.id,
        req_type=req.req_type,
        is_hard=req.is_hard,
        verdict=Verdict.MET if met else Verdict.NOT_MET,
        reason_code=ConstraintReason.LOCATION_MATCH.value if met else ConstraintReason.LOCATION_MISMATCH.value,
        rule_id=rule_id,
        evidence_ids=(),
        evidence_tier=EvidenceTier.ABSENCE.value,
        normalization={"required_location_norm": required_norm, "resume_location_norm": facts.location_norm},
        rule_params=params,
        explanation=f"resume location {facts.location_norm!r} == {required_norm!r} -> {met}",
    )


def _language(req: RequirementFact, facts: ResumeFacts, cfg: RuleConfig, operator: str) -> ConstraintOutcome:
    rule_id = _RULE_IDS[RequirementType.LANGUAGE.value]
    language = str(req.value.get("language") or "")
    label = req.value.get("level") or req.value.get("min_level")
    required_level = language_level(language, str(label) if label else None, cfg)
    params = {"operator": operator, "language": language, "required_level": str(label or "")}
    if not language or required_level is None:
        return _unknown(
            req,
            reason=ConstraintReason.LANGUAGE_UNKNOWN,
            rule_id=rule_id,
            explanation="JD 语言要求未在 language_rules.yaml 声明等级 => UNKNOWN（不猜测换算）",
            rule_params=params,
        )
    candidates = [item for item in facts.languages if item.language == language and item.level is not None]
    if not candidates:
        return _unknown(
            req,
            reason=ConstraintReason.LANGUAGE_UNKNOWN,
            rule_id=rule_id,
            explanation="简历没有该语言的可比等级 => UNKNOWN",
            rule_params=params,
        )
    best = max(candidates, key=lambda item: (item.level or 0, item.raw))
    assert best.level is not None
    met = best.level >= required_level
    return ConstraintOutcome(
        requirement_id=req.id,
        req_type=req.req_type,
        is_hard=req.is_hard,
        verdict=Verdict.MET if met else Verdict.NOT_MET,
        reason_code=(
            ConstraintReason.LANGUAGE_MATCH.value if met else ConstraintReason.LANGUAGE_BELOW_REQUIRED.value
        ),
        rule_id=rule_id,
        evidence_ids=(),
        evidence_tier=EvidenceTier.ABSENCE.value,
        normalization={"resume_language_raw": best.raw, "resume_level": best.level},
        rule_params=params,
        explanation=f"resume {language} level={best.level} >= required={required_level} -> {met}",
    )


def _certification(
    req: RequirementFact, facts: ResumeFacts, cfg: RuleConfig, operator: str
) -> ConstraintOutcome:
    rule_id = _RULE_IDS[RequirementType.CERTIFICATION.value]
    required_raw = str(req.value.get("name") or req.value.get("certification") or "").strip()
    params = {"operator": operator, "required_certification": required_raw}
    if not required_raw:
        return _unknown(
            req,
            reason=ConstraintReason.CERTIFICATION_UNKNOWN,
            rule_id=rule_id,
            explanation="JD 未给出证书名 => UNKNOWN",
            rule_params=params,
        )
    required_key = form_normalize(required_raw)
    hits = [item for item in facts.certifications if form_normalize(item) == required_key]
    if not hits:
        return _unknown(
            req,
            reason=ConstraintReason.CERTIFICATION_UNKNOWN,
            rule_id=rule_id,
            explanation="简历证书列表中无该证书 => UNKNOWN（证书列表非穷尽，缺失不等于不具备）",
            rule_params=params,
        )
    return ConstraintOutcome(
        requirement_id=req.id,
        req_type=req.req_type,
        is_hard=req.is_hard,
        verdict=Verdict.MET,
        reason_code=ConstraintReason.CERTIFICATION_PRESENT.value,
        rule_id=rule_id,
        evidence_ids=(),
        evidence_tier=EvidenceTier.ABSENCE.value,
        normalization={"matched_certification": sorted(hits)[0]},
        rule_params=params,
        explanation=f"structured resume certification matches {required_key}",
    )


_DISPATCH = {
    RequirementType.DEGREE.value: _degree,
    RequirementType.YEARS_EXPERIENCE.value: _experience,
    RequirementType.SKILL.value: _skill,
    RequirementType.LOCATION.value: _location,
    RequirementType.LANGUAGE.value: _language,
    RequirementType.CERTIFICATION.value: _certification,
}


def evaluate_requirement(req: RequirementFact, *, facts: ResumeFacts, cfg: RuleConfig) -> ConstraintOutcome:
    operator, failure = _resolve_operator(req, cfg)
    if failure is not None:
        return failure
    assert operator is not None
    handler = _DISPATCH.get(req.req_type)
    if handler is None:  # pragma: no cover - _resolve_operator 已拦截未声明类型
        return _unknown(
            req,
            reason=ConstraintReason.UNSUPPORTED_REQUIREMENT_TYPE,
            rule_id=_RULE_ID_UNSUPPORTED,
            explanation=f"no handler for req_type={req.req_type!r}",
        )
    return handler(req, facts, cfg, operator)


def evaluate_requirements(
    *, requirements: list[RequirementFact], facts: ResumeFacts, cfg: RuleConfig
) -> list[ConstraintOutcome]:
    """对**全部** requirement 求值；gate 只使用 is_hard=True 的结果。"""
    return [evaluate_requirement(req, facts=facts, cfg=cfg) for req in requirements]


def gate_verdict(outcomes: list[ConstraintOutcome], cfg: RuleConfig) -> tuple[str, list[str]]:
    """硬条件门禁（确定性）：blocked / unknown / pass。"""
    policy = cfg.gate_policy()
    hard = [item for item in outcomes if item.is_hard]
    if not hard:
        return "not_applicable", []
    flags: list[str] = []
    not_met = [item for item in hard if item.verdict is Verdict.NOT_MET]
    unknown = [item for item in hard if item.verdict is Verdict.UNKNOWN]
    if not_met and bool(policy.get("blocked_if_any_not_met", True)):
        flags.append("HARD_CONSTRAINT_BLOCKED")
        return "blocked", flags
    if unknown:
        if bool(policy.get("unknown_if_any_unknown", True)):
            flags.append("HARD_CONSTRAINT_UNKNOWN")
        if bool(cfg.unknown_policy().get("gate_unknown_blocks_recommendation", False)):
            flags.append("HARD_CONSTRAINT_UNKNOWN_BLOCKS")
            return "blocked", flags
        return "unknown", flags
    return "pass", flags

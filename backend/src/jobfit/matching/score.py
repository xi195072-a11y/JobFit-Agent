"""确定性计分（Phase 3 §26/§27）。

- 权重、单项分值映射、scale、UNKNOWN 策略**全部来自 `scoring.yaml`**（版本化配置），
  代码里没有任何魔法系数（§26「不要偷偷发明业务权重」）。
- `unknown_policy.mode = exclude_and_renormalize`（当前唯一实现的模式）：
  UNKNOWN 项不进入分母；整段没有可判定项时该段 not_applicable，并从加权总和中剔除，
  剩余段权重重新归一化 —— 即 UNKNOWN 既不加分也不扣分。
- 硬条件门禁（veto）不参与加权总分，单独以 `gate` 表达（blocked / unknown / pass）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from jobfit.core.enums import RequirementType, SkillMatchStatus, Verdict
from jobfit.matching.constraints import ConstraintOutcome, gate_verdict
from jobfit.matching.rules import UNKNOWN_POLICY_EXCLUDE_AND_RENORMALIZE, RuleConfig
from jobfit.matching.skills import SkillMatchOutcome

STATUS_APPLICABLE = "applicable"
STATUS_NOT_APPLICABLE = "not_applicable"

_SECTION_TO_REQ_TYPE = {
    "experience": RequirementType.YEARS_EXPERIENCE.value,
    "education": RequirementType.DEGREE.value,
    "location": RequirementType.LOCATION.value,
    "language": RequirementType.LANGUAGE.value,
}
_SKILLS_SECTION = "skills"


@dataclass(frozen=True)
class SectionScore:
    section: str
    weight: float
    applied_weight: float
    score: float | None
    status: str
    items_total: int
    items_determinable: int
    detail: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "section": self.section,
            "weight": self.weight,
            "applied_weight": self.applied_weight,
            "score": self.score,
            "status": self.status,
            "items_total": self.items_total,
            "items_determinable": self.items_determinable,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class ScoreResult:
    total: float
    per_section: tuple[SectionScore, ...]
    flags: tuple[str, ...]
    gate: str
    scoring_version: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "gate": self.gate,
            "scoring_version": self.scoring_version,
            "flags": list(self.flags),
            "per_section": [item.as_dict() for item in self.per_section],
        }


def _skill_status_counts(matches: list[SkillMatchOutcome]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in sorted(matches, key=lambda m: m.status.value):
        counts[item.status.value] = counts.get(item.status.value, 0) + 1
    return counts


def _skills_section(matches: list[SkillMatchOutcome], cfg: RuleConfig) -> SectionScore:
    weight = cfg.section_weight(_SKILLS_SECTION)
    determinable = [item for item in matches if item.determinable]
    detail: dict[str, Any] = {
        "status_counts": _skill_status_counts(matches),
        "unknown_count": sum(1 for item in matches if not item.determinable),
    }
    if not determinable:
        return SectionScore(
            section=_SKILLS_SECTION,
            weight=weight,
            applied_weight=0.0,
            score=None,
            status=STATUS_NOT_APPLICABLE,
            items_total=len(matches),
            items_determinable=0,
            detail=detail,
        )
    score = sum(item.credit for item in determinable) / len(determinable)
    detail["credits"] = {str(item.requirement_id): item.credit for item in determinable}
    return SectionScore(
        section=_SKILLS_SECTION,
        weight=weight,
        applied_weight=weight,
        score=round(score, 6),
        status=STATUS_APPLICABLE,
        items_total=len(matches),
        items_determinable=len(determinable),
        detail=detail,
    )


def _verdict_section(
    section: str, req_type: str, outcomes: list[ConstraintOutcome], cfg: RuleConfig
) -> SectionScore:
    weight = cfg.section_weight(section)
    items = [item for item in outcomes if item.req_type == req_type]
    determinable = [item for item in items if item.determinable]
    detail: dict[str, Any] = {
        "req_type": req_type,
        "verdict_counts": {
            verdict.value: sum(1 for item in items if item.verdict is verdict) for verdict in Verdict
        },
    }
    if not determinable:
        return SectionScore(
            section=section,
            weight=weight,
            applied_weight=0.0,
            score=None,
            status=STATUS_NOT_APPLICABLE,
            items_total=len(items),
            items_determinable=0,
            detail=detail,
        )
    score = sum(1.0 if item.verdict is Verdict.MET else 0.0 for item in determinable) / len(determinable)
    return SectionScore(
        section=section,
        weight=weight,
        applied_weight=weight,
        score=round(score, 6),
        status=STATUS_APPLICABLE,
        items_total=len(items),
        items_determinable=len(determinable),
        detail=detail,
    )


def compute_base_score(
    *,
    cfg: RuleConfig,
    constraints: list[ConstraintOutcome],
    skill_matches: list[SkillMatchOutcome],
) -> ScoreResult:
    policy = cfg.unknown_policy()
    mode = str(policy["mode"])
    if mode != UNKNOWN_POLICY_EXCLUDE_AND_RENORMALIZE:
        raise ValueError(
            f"unsupported unknown_policy.mode={mode!r}; "
            f"implemented: {UNKNOWN_POLICY_EXCLUDE_AND_RENORMALIZE!r}"
        )

    sections: list[SectionScore] = []
    for name in cfg.score_sections():
        if name == _SKILLS_SECTION:
            sections.append(_skills_section(skill_matches, cfg))
        else:
            req_type = _SECTION_TO_REQ_TYPE.get(name)
            if req_type is None:
                raise ValueError(f"scoring.yaml 声明了未实现的 section: {name!r}")
            sections.append(_verdict_section(name, req_type, constraints, cfg))

    applied_weight_sum = sum(item.applied_weight for item in sections)
    weighted = sum(item.applied_weight * (item.score or 0.0) for item in sections)
    total = cfg.scale * (weighted / applied_weight_sum) if applied_weight_sum > 0 else 0.0

    gate, gate_flags = gate_verdict(constraints, cfg)
    flags = list(gate_flags)
    # 门禁结论显式入 flags：score_snapshots 无 gate 列，读侧可无损还原（不做猜测）。
    flags.append(f"GATE:{gate}")
    for item in sections:
        if item.status == STATUS_NOT_APPLICABLE:
            flags.append(f"SECTION_NOT_APPLICABLE:{item.section}")
        elif item.items_determinable < item.items_total:
            pending = item.items_total - item.items_determinable
            flags.append(f"SECTION_UNKNOWN_PENDING:{item.section}:{pending}")
    if applied_weight_sum == 0:
        flags.append("NO_DETERMINABLE_SECTIONS")
    if any(item.status is SkillMatchStatus.UNKNOWN for item in skill_matches):
        flags.append("SKILL_UNKNOWN_PRESENT")

    return ScoreResult(
        total=round(total, 6),
        per_section=tuple(sections),
        flags=tuple(sorted(set(flags))),
        gate=gate,
        scoring_version=cfg.scoring_version,
    )

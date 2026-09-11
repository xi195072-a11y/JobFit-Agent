"""确定性技能匹配（Phase 3 §18–§21）——纯函数，绝无 LLM 判断"大概相关"。

证据优先级（§21）：
    structured（结构化技能 + grounded evidence）
  > source chunk（结构化条目的 anchor 指向的 chunk）
  > retrieved semantic（检索命中的 chunk）
  > absence（**不是** positive evidence，只能解释 UNKNOWN）

因此：
- 结构化命中且有 grounding => MATCHED / CLAIMED_ONLY；
- 只有检索命中（结构化抽取没给出技能行）=> PARTIAL（证据在文本里、未成为结构化事实）；
- 什么都没有 => UNKNOWN，**绝不**写成 MISSING（技能列表非穷尽，缺失 ≠ 不具备，§20）。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from jobfit.core.enums import EvidenceTier, RequirementType, SkillMatchStatus, SkillReason
from jobfit.evidence.retrieval import RetrievalHit
from jobfit.matching.facts import RequirementFact, ResumeFacts, ResumeSkillFact
from jobfit.matching.normalize import skill_compare_key
from jobfit.matching.rules import RuleConfig


@dataclass(frozen=True)
class SkillMatchOutcome:
    requirement_id: uuid.UUID
    resume_skill_id: uuid.UUID | None
    status: SkillMatchStatus
    reason_code: str
    norm_used: str
    credit: float
    evidence_ids: tuple[str, ...]
    evidence_tier: str
    explanation: str

    @property
    def determinable(self) -> bool:
        return self.status is not SkillMatchStatus.UNKNOWN


def _best_hit(hits: list[ResumeSkillFact]) -> ResumeSkillFact:
    """确定性选择：优先非 claimed_only，其次 skill_raw 字典序，最后 id。"""
    return sorted(hits, key=lambda item: (item.claimed_only, item.skill_raw, str(item.id)))[0]


def match_skill_requirement(
    req: RequirementFact,
    *,
    facts: ResumeFacts,
    cfg: RuleConfig,
    retrieved: list[RetrievalHit],
) -> SkillMatchOutcome:
    if req.req_type != RequirementType.SKILL.value:
        return SkillMatchOutcome(
            requirement_id=req.id,
            resume_skill_id=None,
            status=SkillMatchStatus.UNKNOWN,
            reason_code=SkillReason.UNSUPPORTED_REQUIREMENT_TYPE.value,
            norm_used="",
            credit=0.0,
            evidence_ids=(),
            evidence_tier=EvidenceTier.ABSENCE.value,
            explanation=f"req_type={req.req_type!r} 不是技能要求",
        )

    required_raw = str(req.value.get("skill") or "").strip()
    if not required_raw:
        return SkillMatchOutcome(
            requirement_id=req.id,
            resume_skill_id=None,
            status=SkillMatchStatus.UNKNOWN,
            reason_code=SkillReason.MISSING_REQUIRED_SKILL.value,
            norm_used="",
            credit=0.0,
            evidence_ids=(),
            evidence_tier=EvidenceTier.ABSENCE.value,
            explanation="JD 未给出技能名 => UNKNOWN",
        )

    required_key = skill_compare_key(required_raw, cfg)
    hits = [skill for skill in facts.skills if skill_compare_key(skill.skill_raw, cfg) == required_key]
    retrieved_ids = tuple(str(hit.source_chunk_id) for hit in retrieved)

    if hits:
        best = _best_hit(hits)
        if best.evidence_ids:
            claimed = best.claimed_only
            return SkillMatchOutcome(
                requirement_id=req.id,
                resume_skill_id=best.id,
                status=SkillMatchStatus.CLAIMED_ONLY if claimed else SkillMatchStatus.MATCHED,
                reason_code=(
                    SkillReason.CLAIMED_ONLY_MATCH.value if claimed else SkillReason.NORMALIZED_MATCH.value
                ),
                norm_used=required_key,
                credit=cfg.claimed_only_penalty if claimed else cfg.credit("matched"),
                evidence_ids=best.evidence_ids,
                evidence_tier=EvidenceTier.STRUCTURED.value,
                explanation=(
                    f"structured resume skill {best.skill_raw!r} matches {required_key!r}"
                    + ("（仅自称证据，按 claimed_only_penalty 折扣）" if claimed else "")
                ),
            )

    if retrieved_ids:
        return SkillMatchOutcome(
            requirement_id=req.id,
            resume_skill_id=None,
            status=SkillMatchStatus.PARTIAL,
            reason_code=SkillReason.RETRIEVED_EVIDENCE_PARTIAL.value,
            norm_used=required_key,
            credit=cfg.credit("partial"),
            evidence_ids=retrieved_ids,
            evidence_tier=EvidenceTier.RETRIEVED.value,
            explanation=(
                f"未形成结构化技能事实，但检索在简历 chunk 中命中 {required_key!r} 相关证据 => PARTIAL"
            ),
        )

    return SkillMatchOutcome(
        requirement_id=req.id,
        resume_skill_id=None,
        status=SkillMatchStatus.UNKNOWN,
        reason_code=SkillReason.NO_EVIDENCE_UNKNOWN.value,
        norm_used=required_key,
        credit=0.0,
        evidence_ids=(),
        evidence_tier=EvidenceTier.ABSENCE.value,
        explanation=(
            f"结构化技能与检索证据都没有 {required_key!r} => UNKNOWN"
            "（技能列表非穷尽，absence of evidence ≠ FALSE）"
        ),
    )


def match_skill_requirements(
    *,
    requirements: list[RequirementFact],
    facts: ResumeFacts,
    cfg: RuleConfig,
    retrieved_by_requirement: dict[uuid.UUID, list[RetrievalHit]],
) -> list[SkillMatchOutcome]:
    return [
        match_skill_requirement(
            req,
            facts=facts,
            cfg=cfg,
            retrieved=retrieved_by_requirement.get(req.id, []),
        )
        for req in requirements
        if req.req_type == RequirementType.SKILL.value
    ]

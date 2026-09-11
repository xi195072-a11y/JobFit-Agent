"""unit: 确定性技能匹配（Phase 3 §18–§21/§36）。"""

from __future__ import annotations

import uuid

from jobfit.core.enums import EvidenceTier, RequirementType, SkillMatchStatus, SkillReason
from jobfit.evidence.retrieval import RetrievalHit
from jobfit.matching.facts import RequirementFact, ResumeFacts, ResumeSkillFact
from jobfit.matching.skills import match_skill_requirement, match_skill_requirements

_DOC = uuid.uuid4()


def _facts(skills: tuple[ResumeSkillFact, ...] = ()) -> ResumeFacts:
    return ResumeFacts(
        profile_id=uuid.UUID(int=1),
        document_id=_DOC,
        parsed_document_id=uuid.UUID(int=2),
        degree_level=None,
        degree_raw=None,
        education_evidence_ids=(),
        experience_years=None,
        experience_evidence_ids=(),
        location_raw=None,
        location_norm=None,
        languages=(),
        certifications=(),
        skills=skills,
        warnings=(),
    )


def _req(skill: str, *, req_type: str = "skill") -> RequirementFact:
    return RequirementFact(
        id=uuid.uuid4(),
        req_type=req_type,
        operator="has",
        value={"skill": skill},
        is_hard=True,
        source_text=None,
        anchors=(),
    )


def _hit() -> RetrievalHit:
    return RetrievalHit(
        source_chunk_id=uuid.uuid4(),
        document_id=_DOC,
        parsed_document_id=uuid.UUID(int=2),
        chunk_index=0,
        page=1,
        char_start=0,
        char_end=10,
        score=0.5,
        retrieval_method="vector",
    )


def _skill(raw: str, *, claimed_only: bool = False, evidence: tuple[str, ...] = ("chunk-1",)):
    return ResumeSkillFact(
        id=uuid.uuid4(), skill_raw=raw, claimed_only=claimed_only, evidence_ids=evidence
    )


def test_exact_match(rule_config) -> None:
    outcome = match_skill_requirement(
        _req("Python"), facts=_facts((_skill("Python"),)), cfg=rule_config, retrieved=[]
    )
    assert outcome.status is SkillMatchStatus.MATCHED
    assert outcome.reason_code == SkillReason.NORMALIZED_MATCH.value
    assert outcome.credit == rule_config.credit("matched")
    assert outcome.evidence_tier == EvidenceTier.STRUCTURED.value
    assert outcome.evidence_ids == ("chunk-1",)


def test_normalized_match_via_alias(rule_config) -> None:
    outcome = match_skill_requirement(
        _req("PostgreSQL"), facts=_facts((_skill("Postgres"),)), cfg=rule_config, retrieved=[]
    )
    assert outcome.status is SkillMatchStatus.MATCHED
    assert outcome.norm_used == "postgresql"


def test_claimed_only_is_discounted(rule_config) -> None:
    outcome = match_skill_requirement(
        _req("Python"),
        facts=_facts((_skill("Python", claimed_only=True),)),
        cfg=rule_config,
        retrieved=[],
    )
    assert outcome.status is SkillMatchStatus.CLAIMED_ONLY
    assert outcome.credit == rule_config.claimed_only_penalty
    assert outcome.reason_code == SkillReason.CLAIMED_ONLY_MATCH.value


def test_partial_when_only_retrieved_evidence_exists(rule_config) -> None:
    """结构化抽取没有该技能行，但检索在简历文本中命中 => PARTIAL（证据在文本里）。"""
    outcome = match_skill_requirement(
        _req("Redis"), facts=_facts((_skill("Java"),)), cfg=rule_config, retrieved=[_hit()]
    )
    assert outcome.status is SkillMatchStatus.PARTIAL
    assert outcome.reason_code == SkillReason.RETRIEVED_EVIDENCE_PARTIAL.value
    assert outcome.evidence_tier == EvidenceTier.RETRIEVED.value
    assert outcome.resume_skill_id is None
    assert outcome.credit == rule_config.credit("partial")


def test_unknown_when_no_evidence_at_all(rule_config) -> None:
    outcome = match_skill_requirement(
        _req("Kubernetes"), facts=_facts((_skill("Java"),)), cfg=rule_config, retrieved=[]
    )
    assert outcome.status is SkillMatchStatus.UNKNOWN
    assert outcome.reason_code == SkillReason.NO_EVIDENCE_UNKNOWN.value
    assert outcome.credit == 0.0
    # 关键：绝不写成 MISSING（absence of evidence ≠ FALSE）
    assert outcome.status is not SkillMatchStatus.MISSING


def test_structured_match_without_grounding_falls_back_to_retrieved(rule_config) -> None:
    """证据优先级（§21）：structured 无 grounding 时退回 retrieved，而不是直接算 MATCHED。"""
    outcome = match_skill_requirement(
        _req("Python"),
        facts=_facts((_skill("Python", evidence=()),)),
        cfg=rule_config,
        retrieved=[_hit()],
    )
    assert outcome.status is SkillMatchStatus.PARTIAL
    assert outcome.evidence_tier == EvidenceTier.RETRIEVED.value


def test_semantic_but_not_equivalent_skills_do_not_match(rule_config) -> None:
    """§36：必须防止 Python → PyTorch 这类的未批准匹配。"""
    outcome = match_skill_requirement(
        _req("Python"), facts=_facts((_skill("PyTorch"), _skill("PySpark"),)), cfg=rule_config, retrieved=[]
    )
    assert outcome.status is SkillMatchStatus.UNKNOWN


def test_missing_skill_name_is_unknown(rule_config) -> None:
    req = RequirementFact(
        id=uuid.uuid4(),
        req_type=RequirementType.SKILL.value,
        operator="has",
        value={},
        is_hard=True,
        source_text=None,
        anchors=(),
    )
    outcome = match_skill_requirement(req, facts=_facts(), cfg=rule_config, retrieved=[])
    assert outcome.status is SkillMatchStatus.UNKNOWN
    assert outcome.reason_code == SkillReason.MISSING_REQUIRED_SKILL.value


def test_non_skill_requirement_is_rejected(rule_config) -> None:
    outcome = match_skill_requirement(
        _req("本科", req_type=RequirementType.DEGREE.value), facts=_facts(), cfg=rule_config, retrieved=[]
    )
    assert outcome.status is SkillMatchStatus.UNKNOWN
    assert outcome.reason_code == SkillReason.UNSUPPORTED_REQUIREMENT_TYPE.value


def test_match_all_filters_to_skill_requirements_only(rule_config) -> None:
    skill_req = _req("Python")
    degree_req = _req("本科", req_type=RequirementType.DEGREE.value)
    outcomes = match_skill_requirements(
        requirements=[degree_req, skill_req],
        facts=_facts((_skill("Python"),)),
        cfg=rule_config,
        retrieved_by_requirement={},
    )
    assert [item.requirement_id for item in outcomes] == [skill_req.id]


def test_deterministic_selection_among_multiple_candidates(rule_config) -> None:
    """同一 canonical 命中多行时的选择必须确定（非 claimed_only 优先，其次 raw/id）。"""
    facts = _facts((_skill("python3"), _skill("Python"), _skill("Python Programming", claimed_only=True)))
    first = match_skill_requirement(_req("Python"), facts=facts, cfg=rule_config, retrieved=[])
    second = match_skill_requirement(_req("Python"), facts=facts, cfg=rule_config, retrieved=[])
    assert first.resume_skill_id == second.resume_skill_id
    assert first.status is SkillMatchStatus.MATCHED

"""Resume DTO → 领域 ResumeProfile（含 grounding 与 UNKNOWN 语义）。

规则：
- 任何条目必须能回溯到 chunk；回溯失败 => 丢弃该条目 + extraction_warning（不猜、不补）。
- 缺失字段保持 None / 空集合（ABSENCE OF EVIDENCE != FALSE，ADR-008）。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from jobfit.core.enums import DocumentKind
from jobfit.core.schemas import (
    ResumeEducation,
    ResumeExperience,
    ResumeProfile,
    ResumeSkill,
)
from jobfit.extraction.anchors import ChunkLike, find_literal, ground_quotes
from jobfit.extraction.dto import LLMResumeProfile


@dataclass
class ResumeBuildResult:
    profile: ResumeProfile
    warnings: list[str] = field(default_factory=list)


def _verify_scalar(
    *,
    name: str,
    value: str | None,
    chunks: Sequence[ChunkLike],
    parsed_document_id: str,
    warnings: list[str],
) -> str | None:
    if value is None:
        return None
    anchor, _ = find_literal(
        literal=value,
        chunks=chunks,
        parsed_document_id=parsed_document_id,
        doc_kind=DocumentKind.RESUME,
    )
    if anchor is None:
        warnings.append(f"ungrounded_scalar:{name}")
        return None
    return value


def build_resume_profile(
    *,
    dto: LLMResumeProfile,
    document_id: str,
    parsed_document_id: str,
    chunks: Sequence[ChunkLike],
) -> ResumeBuildResult:
    warnings: list[str] = list(dto.extraction_warnings)

    education: list[ResumeEducation] = []
    for edu_index, edu_item in enumerate(dto.education):
        grounding = ground_quotes(
            quotes=edu_item.evidence_quotes,
            chunks=chunks,
            parsed_document_id=parsed_document_id,
            doc_kind=DocumentKind.RESUME,
        )
        if not grounding.grounded:
            warnings.append(f"ungrounded_item:education[{edu_index}]")
            continue
        education.append(
            ResumeEducation(
                school=edu_item.school,
                degree=edu_item.degree,
                major=edu_item.major,
                start_date=edu_item.start_date,
                end_date=edu_item.end_date,
                gpa=edu_item.gpa,
                honor=edu_item.honor,
                evidence_ids=grounding.evidence_ids,
                anchors=grounding.anchors,
            )
        )

    experiences: list[ResumeExperience] = []
    for exp_index, exp_item in enumerate(dto.experiences):
        grounding = ground_quotes(
            quotes=exp_item.evidence_quotes,
            chunks=chunks,
            parsed_document_id=parsed_document_id,
            doc_kind=DocumentKind.RESUME,
        )
        if not grounding.grounded:
            warnings.append(f"ungrounded_item:experiences[{exp_index}]")
            continue
        bullets: list[str] = []
        for bullet_index, bullet in enumerate(exp_item.bullets):
            anchor, _ = find_literal(
                literal=bullet,
                chunks=chunks,
                parsed_document_id=parsed_document_id,
                doc_kind=DocumentKind.RESUME,
            )
            if anchor is None:
                warnings.append(f"ungrounded_bullet:experiences[{exp_index}][{bullet_index}]")
                continue
            bullets.append(bullet)
        experiences.append(
            ResumeExperience(
                company=exp_item.company,
                title=exp_item.title,
                location=exp_item.location,
                start_date=exp_item.start_date,
                end_date=exp_item.end_date,
                bullets=bullets,
                evidence_ids=grounding.evidence_ids,
                anchors=grounding.anchors,
            )
        )

    skills: list[ResumeSkill] = []
    for skill_index, skill_item in enumerate(dto.skills):
        grounding = ground_quotes(
            quotes=skill_item.evidence_quotes,
            chunks=chunks,
            parsed_document_id=parsed_document_id,
            doc_kind=DocumentKind.RESUME,
        )
        if not grounding.grounded:
            warnings.append(f"ungrounded_item:skills[{skill_index}]")
            continue
        skills.append(
            ResumeSkill(
                skill_raw=skill_item.skill_raw,
                category=skill_item.category,
                proficiency=skill_item.proficiency,
                claimed_only=False,
                evidence_ids=grounding.evidence_ids,
            )
        )

    languages: list[str] = []
    for index, value in enumerate(dto.languages):
        anchor, _ = find_literal(
            literal=value,
            chunks=chunks,
            parsed_document_id=parsed_document_id,
            doc_kind=DocumentKind.RESUME,
        )
        if anchor is None:
            warnings.append(f"ungrounded_scalar:languages[{index}]")
            continue
        languages.append(value)

    certifications: list[str] = []
    for index, value in enumerate(dto.certifications):
        anchor, _ = find_literal(
            literal=value,
            chunks=chunks,
            parsed_document_id=parsed_document_id,
            doc_kind=DocumentKind.RESUME,
        )
        if anchor is None:
            warnings.append(f"ungrounded_scalar:certifications[{index}]")
            continue
        certifications.append(value)

    profile = ResumeProfile(
        document_id=document_id,
        parsed_document_id=parsed_document_id,
        name=_verify_scalar(
            name="name",
            value=dto.name,
            chunks=chunks,
            parsed_document_id=parsed_document_id,
            warnings=warnings,
        ),
        phone=_verify_scalar(
            name="phone",
            value=dto.phone,
            chunks=chunks,
            parsed_document_id=parsed_document_id,
            warnings=warnings,
        ),
        email=_verify_scalar(
            name="email",
            value=dto.email,
            chunks=chunks,
            parsed_document_id=parsed_document_id,
            warnings=warnings,
        ),
        location=_verify_scalar(
            name="location",
            value=dto.location,
            chunks=chunks,
            parsed_document_id=parsed_document_id,
            warnings=warnings,
        ),
        education=education,
        experiences=experiences,
        skills=skills,
        languages=languages,
        certifications=certifications,
        extraction_warnings=warnings,
    )
    return ResumeBuildResult(profile=profile, warnings=warnings)

"""把 immutable artifact 转成**确定性事实视图**（Phase 3 §9/§10/§21）。

本模块只做两件事：
1. 从不可变的 resume / jd artifact 中读出计算所需的事实；
2. 明确标注"哪些事实是权威的、哪些是部分证据"——UNKNOWN 语义的判据来自这里。

**权威性约定（写入 ADR-031，决定 FALSE 是否可判定）：**
- `degree_level`：简历列出的最高学历是**权威标量事实**。已知等级 < 要求 => NOT_MET 成立。
- `experience_years`：由 dated 工作经历区间的并集（不重复计算重叠）得到下界；
  **仅当每一段经历都有起止日期**时才允许据此判 NOT_MET，否则 UNKNOWN。
- `location`：仅当简历存在可归一化的地点时才可比较；否则 UNKNOWN（绝不推断 OK/Not OK）。
- `skills` / `certifications`：**不是**穷尽列表。缺失只能是 UNKNOWN，不能是 FALSE。
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from jobfit.core.errors import ValidationFailed
from jobfit.core.schemas import JDProfile, ResumeProfile, strip_artifact_meta
from jobfit.db import models
from jobfit.matching.normalize import (
    degree_level,
    language_level,
    normalize_location,
    parse_language_entry,
)
from jobfit.matching.rules import RuleConfig

_DAYS_PER_YEAR = 365.25


@dataclass(frozen=True)
class ResumeSkillFact:
    id: uuid.UUID
    skill_raw: str
    claimed_only: bool
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class LanguageFact:
    raw: str
    language: str
    label: str | None
    level: int | None


@dataclass(frozen=True)
class ResumeFacts:
    profile_id: uuid.UUID
    document_id: uuid.UUID
    parsed_document_id: uuid.UUID
    degree_level: int | None
    degree_raw: str | None
    education_evidence_ids: tuple[str, ...]
    experience_years: float | None
    experience_evidence_ids: tuple[str, ...]
    location_raw: str | None
    location_norm: str | None
    languages: tuple[LanguageFact, ...]
    certifications: tuple[str, ...]
    skills: tuple[ResumeSkillFact, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class RequirementFact:
    id: uuid.UUID
    req_type: str
    operator: str
    value: dict[str, Any]
    is_hard: bool
    source_text: str | None
    anchors: tuple[dict[str, Any], ...]


def _union_days(intervals: Sequence[tuple[date, date]]) -> float:
    """区间并集天数（重叠不重复计算）——确定性、无时间依赖。"""
    if not intervals:
        return 0.0
    ordered = sorted(intervals)
    total = 0
    cursor_start, cursor_end = ordered[0]
    for start, end in ordered[1:]:
        if start <= cursor_end:
            cursor_end = max(cursor_end, end)
            continue
        total += (cursor_end - cursor_start).days
        cursor_start, cursor_end = start, end
    total += (cursor_end - cursor_start).days
    return float(total)


def load_resume_domain(session: Session, profile_id: uuid.UUID) -> tuple[models.ResumeProfile, ResumeProfile]:
    row = session.get(models.ResumeProfile, profile_id)
    if row is None:
        raise ValidationFailed(f"resume profile {profile_id} not found")
    return row, ResumeProfile.model_validate(strip_artifact_meta(row.full_dump))


def load_jd_domain(session: Session, profile_id: uuid.UUID) -> tuple[models.JDProfile, JDProfile]:
    row = session.get(models.JDProfile, profile_id)
    if row is None:
        raise ValidationFailed(f"jd profile {profile_id} not found")
    return row, JDProfile.model_validate(strip_artifact_meta(row.full_dump))


def load_resume_facts(session: Session, profile_id: uuid.UUID, cfg: RuleConfig) -> ResumeFacts:
    row, domain = load_resume_domain(session, profile_id)

    education_rows = list(
        session.execute(
            select(models.ResumeEducation)
            .where(models.ResumeEducation.profile_id == profile_id)
            .order_by(models.ResumeEducation.created_at, models.ResumeEducation.id)
        )
        .scalars()
        .all()
    )
    levels = [degree_level(edu.degree, cfg) for edu in education_rows]
    known = [level for level in levels if level is not None]
    degree_raw = None
    if known:
        best = max(known)
        for edu in education_rows:
            if degree_level(edu.degree, cfg) == best:
                degree_raw = edu.degree
                break

    experience_rows = list(
        session.execute(
            select(models.ResumeExperience)
            .where(models.ResumeExperience.profile_id == profile_id)
            .order_by(models.ResumeExperience.created_at, models.ResumeExperience.id)
        )
        .scalars()
        .all()
    )
    intervals: list[tuple[date, date]] = []
    complete = bool(experience_rows)
    for exp in experience_rows:
        if exp.start_date is None or exp.end_date is None:
            complete = False
            continue
        intervals.append((exp.start_date.date(), exp.end_date.date()))
    experience_years = (
        round(_union_days(intervals) / _DAYS_PER_YEAR, 6) if complete and intervals else None
    )

    skill_rows = list(
        session.execute(
            select(models.ResumeSkill)
            .where(models.ResumeSkill.profile_id == profile_id)
            .order_by(models.ResumeSkill.skill_raw, models.ResumeSkill.id)
        )
        .scalars()
        .all()
    )

    languages: list[LanguageFact] = []
    for raw in domain.languages:
        parsed = parse_language_entry(raw, cfg)
        if parsed is None:
            languages.append(LanguageFact(raw=raw, language="", label=None, level=None))
            continue
        language, label = parsed
        languages.append(
            LanguageFact(
                raw=raw,
                language=language,
                label=label,
                level=language_level(language, label, cfg),
            )
        )

    return ResumeFacts(
        profile_id=row.id,
        document_id=row.document_id,
        parsed_document_id=row.parsed_document_id,
        degree_level=max(known) if known else None,
        degree_raw=degree_raw,
        education_evidence_ids=tuple(
            str(eid) for edu in education_rows for eid in (edu.bullet_evidence_ids or [])
        ),
        experience_years=experience_years,
        experience_evidence_ids=tuple(
            str(eid) for exp in experience_rows for eid in (exp.bullet_evidence_ids or [])
        ),
        location_raw=domain.location,
        location_norm=normalize_location(domain.location, cfg),
        languages=tuple(languages),
        certifications=tuple(domain.certifications),
        skills=tuple(
            ResumeSkillFact(
                id=skill.id,
                skill_raw=skill.skill_raw,
                claimed_only=bool(skill.claimed_only),
                evidence_ids=tuple(str(eid) for eid in (skill.evidence_ids or [])),
            )
            for skill in skill_rows
        ),
        warnings=tuple(domain.extraction_warnings),
    )


def load_requirements(session: Session, jd_profile_id: uuid.UUID) -> list[RequirementFact]:
    rows = list(
        session.execute(
            select(models.JDRequirement)
            .where(models.JDRequirement.profile_id == jd_profile_id)
            .order_by(models.JDRequirement.req_type, models.JDRequirement.id)
        )
        .scalars()
        .all()
    )
    return [
        RequirementFact(
            id=row.id,
            req_type=row.req_type,
            operator=row.operator,
            value=dict(row.value or {}),
            is_hard=bool(row.is_hard),
            source_text=row.source_text,
            anchors=tuple(row.anchors or []),
        )
        for row in rows
    ]

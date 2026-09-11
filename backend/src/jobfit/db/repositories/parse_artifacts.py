"""parse artifact 子行（chunks）与 extraction artifact 子行（children）的仓储。

并发语义：一律依赖数据库 UNIQUE constraint + ON CONFLICT DO NOTHING，
禁止 "SELECT 不存在 => INSERT" 的 TOCTOU；reuse 时不重复 INSERT、永不 UPDATE。
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from jobfit.core.schemas import JDProfile, ResumeProfile
from jobfit.db import models
from jobfit.parsing.chunker import ChunkSpec


def get_parsed_document(
    session: Session, *, document_id: uuid.UUID, parser_version: str
) -> models.ParsedDocument | None:
    return session.execute(
        select(models.ParsedDocument).where(
            models.ParsedDocument.document_id == document_id,
            models.ParsedDocument.parser_version == parser_version,
        )
    ).scalar_one_or_none()


def count_chunks(session: Session, parsed_document_id: uuid.UUID) -> int:
    return int(
        session.execute(
            select(func.count())
            .select_from(models.DocumentChunk)
            .where(models.DocumentChunk.parsed_document_id == parsed_document_id)
        ).scalar_one()
    )


def create_or_reuse_chunks(
    session: Session, *, parsed_document_id: uuid.UUID, specs: list[ChunkSpec]
) -> int:
    """批量插入 chunks；`UNIQUE(parsed_document_id, chunk_index)` 冲突即复用，返回新增行数。

    created 数量用插入前后计数差得到：多值 INSERT ... ON CONFLICT 的 rowcount 不可靠。
    """
    if not specs:
        return 0
    before = count_chunks(session, parsed_document_id)
    rows = [
        {
            "parsed_document_id": parsed_document_id,
            "chunk_index": spec.chunk_index,
            "content": spec.content,
            "page": spec.page,
            "char_start": spec.char_start,
            "char_end": spec.char_end,
            "span_sha256": spec.span_sha256,
        }
        for spec in specs
    ]
    stmt = (
        pg_insert(models.DocumentChunk)
        .values(rows)
        .on_conflict_do_nothing(index_elements=["parsed_document_id", "chunk_index"])
    )
    session.execute(stmt)
    session.commit()
    return count_chunks(session, parsed_document_id) - before


def list_chunks(session: Session, parsed_document_id: uuid.UUID) -> list[models.DocumentChunk]:
    return list(
        session.execute(
            select(models.DocumentChunk)
            .where(models.DocumentChunk.parsed_document_id == parsed_document_id)
            .order_by(models.DocumentChunk.chunk_index)
        )
        .scalars()
        .all()
    )


def _count(session: Session, model: Any, profile_id: uuid.UUID) -> int:
    column = model.profile_id
    return int(
        session.execute(select(func.count()).select_from(model).where(column == profile_id)).scalar_one()
    )


def create_resume_children(session: Session, *, profile_id: uuid.UUID, profile: ResumeProfile) -> int:
    """写 resume_education / resume_experiences / resume_skills；已存在则跳过（immutable reuse）。"""
    if _count(session, models.ResumeEducation, profile_id) == 0:
        for edu in profile.education:
            session.add(
                models.ResumeEducation(
                    profile_id=profile_id,
                    school=edu.school,
                    degree=edu.degree,
                    major=edu.major,
                    start_date=edu.start_date,
                    end_date=edu.end_date,
                    gpa=edu.gpa,
                    honor=edu.honor,
                    bullet_evidence_ids=list(edu.evidence_ids),
                    anchors=[a.model_dump(mode="json") for a in edu.anchors],
                )
            )
    if _count(session, models.ResumeExperience, profile_id) == 0:
        for exp in profile.experiences:
            session.add(
                models.ResumeExperience(
                    profile_id=profile_id,
                    company=exp.company,
                    title=exp.title,
                    location=exp.location,
                    start_date=exp.start_date,
                    end_date=exp.end_date,
                    bullets=list(exp.bullets),
                    bullet_evidence_ids=list(exp.evidence_ids),
                    anchors=[a.model_dump(mode="json") for a in exp.anchors],
                )
            )
    if _count(session, models.ResumeSkill, profile_id) == 0:
        for skill in profile.skills:
            session.add(
                models.ResumeSkill(
                    profile_id=profile_id,
                    skill_raw=skill.skill_raw,
                    skill_norm=skill.skill_norm,
                    category=skill.category,
                    proficiency=skill.proficiency,
                    claimed_only=skill.claimed_only,
                    evidence_ids=list(skill.evidence_ids),
                )
            )
    session.commit()
    return (
        _count(session, models.ResumeEducation, profile_id)
        + _count(session, models.ResumeExperience, profile_id)
        + _count(session, models.ResumeSkill, profile_id)
    )


def create_jd_requirements(session: Session, *, profile_id: uuid.UUID, profile: JDProfile) -> int:
    if _count(session, models.JDRequirement, profile_id) == 0:
        for req in [*profile.requirements, *profile.preferred_qualifications]:
            session.add(
                models.JDRequirement(
                    profile_id=profile_id,
                    req_type=req.req_type.value,
                    operator=req.operator,
                    value=req.value,
                    weight=req.weight,
                    is_hard=req.is_hard,
                    source_text=req.source_text,
                    anchors=[a.model_dump(mode="json") for a in req.anchors],
                )
            )
        session.commit()
    return _count(session, models.JDRequirement, profile_id)


def list_resume_profiles(session: Session, document_id: uuid.UUID) -> list[models.ResumeProfile]:
    return list(
        session.execute(
            select(models.ResumeProfile)
            .where(models.ResumeProfile.document_id == document_id)
            .order_by(models.ResumeProfile.extracted_at)
        )
        .scalars()
        .all()
    )


def list_jd_profiles(session: Session, document_id: uuid.UUID) -> list[models.JDProfile]:
    return list(
        session.execute(
            select(models.JDProfile)
            .where(models.JDProfile.document_id == document_id)
            .order_by(models.JDProfile.extracted_at)
        )
        .scalars()
        .all()
    )


def list_parsed_documents(session: Session, document_id: uuid.UUID) -> list[models.ParsedDocument]:
    return list(
        session.execute(
            select(models.ParsedDocument)
            .where(models.ParsedDocument.document_id == document_id)
            .order_by(models.ParsedDocument.created_at)
        )
        .scalars()
        .all()
    )

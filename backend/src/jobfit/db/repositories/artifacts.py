"""Document-scoped immutable artifact 仓储（ADR-023）。

只允许：按需创建（唯一键冲突即复用）+ 只读引用。
永不 UPDATE/overwrite/删除在用的版本。
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from jobfit.db import models

DEFAULT_PARSER_VERSION = "p:0.1.0"


def sha256_hex(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- parse artifact

def get_or_create_parsed_document(
    session: Session,
    *,
    document_id: uuid.UUID,
    parser_version: str,
    text: str,
    pages: dict,
) -> tuple[models.ParsedDocument, bool]:
    """按 (document_id, parser_version) 幂等创建/复用 parse artifact。"""
    existing = session.execute(
        select(models.ParsedDocument).where(
            models.ParsedDocument.document_id == document_id,
            models.ParsedDocument.parser_version == parser_version,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing, False

    stmt = (
        pg_insert(models.ParsedDocument)
        .values(
            document_id=document_id,
            parser_version=parser_version,
            text=text,
            pages=pages,
            content_sha256=sha256_hex(text),
        )
        .on_conflict_do_nothing(
            index_elements=["document_id", "parser_version"],
            index_where=None,
        )
        .returning(models.ParsedDocument.id)
    )
    inserted_id = session.execute(stmt).scalar_one_or_none()
    session.commit()
    if inserted_id is not None:
        obj = session.get(models.ParsedDocument, inserted_id)
        assert obj is not None
        return obj, True
    # 并发下唯一键冲突：读取并发方提交的行
    session.rollback()
    existing = session.execute(
        select(models.ParsedDocument).where(
            models.ParsedDocument.document_id == document_id,
            models.ParsedDocument.parser_version == parser_version,
        )
    ).scalar_one()
    return existing, False


# ---------------------------------------------------------------- resume artifact

def get_or_create_resume_profile(
    session: Session,
    *,
    document_id: uuid.UUID,
    parsed_document_id: uuid.UUID,
    pipeline_version: str,
    extraction_schema_version: str,
    prompt_version: str,
    llm_model: str,
    full_dump: dict,
    extraction_warnings: list[str] | None = None,
    pii_hashes: dict[str, str | None] | None = None,
) -> tuple[models.ResumeProfile, bool]:
    existing = session.execute(
        select(models.ResumeProfile).where(
            models.ResumeProfile.document_id == document_id,
            models.ResumeProfile.pipeline_version == pipeline_version,
            models.ResumeProfile.extraction_schema_version == extraction_schema_version,
            models.ResumeProfile.prompt_version == prompt_version,
            models.ResumeProfile.llm_model == llm_model,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing, False

    hashes = pii_hashes or {}
    stmt = (
        pg_insert(models.ResumeProfile)
        .values(
            document_id=document_id,
            parsed_document_id=parsed_document_id,
            pipeline_version=pipeline_version,
            extraction_schema_version=extraction_schema_version,
            prompt_version=prompt_version,
            llm_model=llm_model,
            full_dump=full_dump,
            name_sha256=hashes.get("name"),
            phone_sha256=hashes.get("phone"),
            email_sha256=hashes.get("email"),
            extraction_warnings=extraction_warnings or [],
        )
        .on_conflict_do_nothing(
            index_elements=[
                "document_id",
                "pipeline_version",
                "extraction_schema_version",
                "prompt_version",
                "llm_model",
            ]
        )
        .returning(models.ResumeProfile.id)
    )
    inserted_id = session.execute(stmt).scalar_one_or_none()
    session.commit()
    if inserted_id is not None:
        obj = session.get(models.ResumeProfile, inserted_id)
        assert obj is not None
        return obj, True
    session.rollback()
    existing = session.execute(
        select(models.ResumeProfile).where(
            models.ResumeProfile.document_id == document_id,
            models.ResumeProfile.pipeline_version == pipeline_version,
            models.ResumeProfile.extraction_schema_version == extraction_schema_version,
            models.ResumeProfile.prompt_version == prompt_version,
            models.ResumeProfile.llm_model == llm_model,
        )
    ).scalar_one()
    return existing, False


# ---------------------------------------------------------------- jd artifact

def get_or_create_jd_profile(
    session: Session,
    *,
    document_id: uuid.UUID,
    parsed_document_id: uuid.UUID,
    pipeline_version: str,
    extraction_schema_version: str,
    prompt_version: str,
    llm_model: str,
    full_dump: dict,
    extraction_warnings: list[str] | None = None,
) -> tuple[models.JDProfile, bool]:
    existing = session.execute(
        select(models.JDProfile).where(
            models.JDProfile.document_id == document_id,
            models.JDProfile.pipeline_version == pipeline_version,
            models.JDProfile.extraction_schema_version == extraction_schema_version,
            models.JDProfile.prompt_version == prompt_version,
            models.JDProfile.llm_model == llm_model,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing, False

    stmt = (
        pg_insert(models.JDProfile)
        .values(
            document_id=document_id,
            parsed_document_id=parsed_document_id,
            pipeline_version=pipeline_version,
            extraction_schema_version=extraction_schema_version,
            prompt_version=prompt_version,
            llm_model=llm_model,
            full_dump=full_dump,
            extraction_warnings=extraction_warnings or [],
        )
        .on_conflict_do_nothing(
            index_elements=[
                "document_id",
                "pipeline_version",
                "extraction_schema_version",
                "prompt_version",
                "llm_model",
            ]
        )
        .returning(models.JDProfile.id)
    )
    inserted_id = session.execute(stmt).scalar_one_or_none()
    session.commit()
    if inserted_id is not None:
        obj = session.get(models.JDProfile, inserted_id)
        assert obj is not None
        return obj, True
    session.rollback()
    existing = session.execute(
        select(models.JDProfile).where(
            models.JDProfile.document_id == document_id,
            models.JDProfile.pipeline_version == pipeline_version,
            models.JDProfile.extraction_schema_version == extraction_schema_version,
            models.JDProfile.prompt_version == prompt_version,
            models.JDProfile.llm_model == llm_model,
        )
    ).scalar_one()
    return existing, False


def list_profiles(
    session: Session, *, kind: str, limit: int = 50, offset: int = 0
) -> tuple[list[models.ResumeProfile | models.JDProfile], int]:
    """列出可复用的 immutable profile artifact（Analysis 创建页的候选选择，§8）。

    kind ∈ {"resume", "jd"}。顺序确定性：`extracted_at DESC, id DESC`。
    **不返回任何 PII**——profile 行只含 document/parsed_document 引用与版本五元组。
    """
    from sqlalchemy import func

    model: Any = models.ResumeProfile if kind == "resume" else models.JDProfile
    total = session.execute(select(func.count()).select_from(model)).scalar_one()
    rows = list(
        session.execute(
            select(model)
            .order_by(model.extracted_at.desc(), model.id.desc())
            .limit(limit)
            .offset(offset)
        )
        .scalars()
        .all()
    )
    return rows, int(total)

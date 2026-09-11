"""documents 仓储（upload 阶段写入，dedupe by sha256）。"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from jobfit.core.errors import DuplicateDocument
from jobfit.db import models


def find_by_sha256(session: Session, sha256: str) -> models.Document | None:
    return session.execute(
        select(models.Document).where(models.Document.sha256 == sha256)
    ).scalar_one_or_none()


def get_by_id(session: Session, document_id: uuid.UUID) -> models.Document | None:
    return session.get(models.Document, document_id)


def create_document(
    session: Session,
    *,
    kind: str,
    original_filename: str,
    mime_type: str,
    size_bytes: int,
    sha256: str,
    storage_path: str,
) -> tuple[models.Document, bool]:
    """幂等创建：同 sha 已存在 => 返回现有行 + created=False。"""
    existing = find_by_sha256(session, sha256)
    if existing is not None:
        return existing, False
    doc = models.Document(
        kind=kind,
        original_filename=original_filename,
        mime_type=mime_type,
        size_bytes=size_bytes,
        sha256=sha256,
        storage_path=storage_path,
    )
    session.add(doc)
    try:
        session.commit()
    except Exception:
        session.rollback()
        raise DuplicateDocument(f"duplicate sha256 {sha256}") from None
    session.refresh(doc)
    return doc, True

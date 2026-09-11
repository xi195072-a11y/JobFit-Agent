"""upload 阶段服务（architecture.md §3.1 step 1）：校验→去重→存储→写 documents。

不执行 parsing/extraction（属于后续 Phase）；不会伪造解析结果。
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from jobfit.config.settings import Settings
from jobfit.db import models
from jobfit.db.repositories import documents as docs_repo
from jobfit.ingestion.validate import (
    LocalStorage,
    SniffedFile,
    compute_sha256,
    enforce_size,
    sniff_file,
)


def ingest_document(
    session: Session,
    settings: Settings,
    *,
    kind: str,
    filename: str,
    data: bytes,
) -> tuple[models.Document, bool, SniffedFile]:
    """持久化 document artifact；同 sha256 时幂等返回现有行（created=False）。"""
    sniffed = sniff_file(data[:8 * 1024], declared_kind=kind, filename=filename)
    enforce_size(len(data), settings.max_upload_bytes)
    sha256 = compute_sha256(data)
    storage = LocalStorage(settings.storage_dir)
    storage_path = storage.store(sha256, data)
    doc, created = docs_repo.create_document(
        session,
        kind=sniffed.kind,
        original_filename=filename,
        mime_type=sniffed.mime_type,
        size_bytes=len(data),
        sha256=sha256,
        storage_path=storage_path,
    )
    return doc, created, sniffed


def require_document(session: Session, document_id: uuid.UUID) -> models.Document:
    from jobfit.core.errors import NotFound

    doc = docs_repo.get_by_id(session, document_id)
    if doc is None:
        raise NotFound(f"document {document_id} not found")
    return doc

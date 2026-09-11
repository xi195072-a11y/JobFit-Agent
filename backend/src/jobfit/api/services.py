"""api services（薄层；真实逻辑不在此伪造）。

Phase 3 职责：
- 入队时固化版本集 + config_snapshot（ADR-019），并记录 `embedding_model`（检索身份，§15）；
- **显式绑定** profile（§5）：调用方可以提供 resume_profile_id / jd_profile_id，
  必须校验其归属 document；未提供时保持 NULL，由执行期 extract 节点绑定（不猜）；
- **analysis 幂等 identity**（§29）：未显式给 idempotency_key 时由版本集确定性推导；
  命中已存在的 analysis 直接复用（不制造无意义的新行）。并发下由 UNIQUE 约束裁决。
"""

from __future__ import annotations

import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from jobfit.config.loader import get_loader
from jobfit.config.settings import Settings
from jobfit.core.errors import ValidationFailed
from jobfit.core.ids import analysis_identity_key
from jobfit.core.schemas import SchemaVersions, combined_extraction_schema
from jobfit.db import models
from jobfit.db.repositories import analyses as analyses_repo
from jobfit.ingestion.service import require_document


def _validate_binding(
    session: Session,
    *,
    profile_id: uuid.UUID | None,
    document_id: uuid.UUID,
    kind: str,
) -> None:
    if profile_id is None:
        return
    if kind == "resume":
        profile = session.get(models.ResumeProfile, profile_id)
    else:
        profile = session.get(models.JDProfile, profile_id)
    if profile is None:
        raise ValidationFailed(f"{kind} profile {profile_id} not found")
    if profile.document_id != document_id:
        raise ValidationFailed(
            f"{kind} profile {profile_id} belongs to document {profile.document_id}, not {document_id}"
        )


def queue_analysis(
    session: Session,
    settings: Settings,
    *,
    resume_document_id: uuid.UUID,
    jd_document_id: uuid.UUID,
    resume_profile_id: uuid.UUID | None = None,
    jd_profile_id: uuid.UUID | None = None,
    idempotency_key: str | None = None,
) -> tuple[models.Analysis, bool]:
    """创建（或复用）queued analysis。返回 (analysis, reused)。"""
    resume_doc = require_document(session, resume_document_id)
    jd_doc = require_document(session, jd_document_id)
    if resume_doc.kind != "resume":
        raise ValidationFailed(f"resume_document_id must point to a resume, got {resume_doc.kind}")
    if jd_doc.kind != "jd":
        raise ValidationFailed(f"jd_document_id must point to a jd, got {jd_doc.kind}")
    _validate_binding(session, profile_id=resume_profile_id, document_id=resume_document_id, kind="resume")
    _validate_binding(session, profile_id=jd_profile_id, document_id=jd_document_id, kind="jd")

    versions = get_loader(settings).resolve_versions(
        pipeline_version=settings.pipeline_version,
        extraction_schema_version=combined_extraction_schema(SchemaVersions()),
        llm_model=settings.deepseek_model,
        embedding_model=settings.embedding_model,
    )
    key = idempotency_key or analysis_identity_key(
        resume_profile_id=str(resume_profile_id) if resume_profile_id else None,
        jd_profile_id=str(jd_profile_id) if jd_profile_id else None,
        resume_document_id=str(resume_document_id),
        jd_document_id=str(jd_document_id),
        pipeline_version=versions.pipeline_version,
        extraction_schema_version=versions.extraction_schema_version,
        prompt_version=versions.prompt_version,
        ruleset_version=versions.ruleset_version,
        scoring_version=versions.scoring_version,
        embedding_model=versions.embedding_model,
    )
    existing = analyses_repo.get_by_idempotency_key(session, key)
    if existing is not None:
        return existing, True

    try:
        analysis = analyses_repo.create_analysis(
            session,
            resume_document_id=resume_document_id,
            jd_document_id=jd_document_id,
            pipeline_version=versions.pipeline_version,
            extraction_schema_version=versions.extraction_schema_version,
            prompt_version=versions.prompt_version,
            ruleset_version=versions.ruleset_version,
            scoring_version=versions.scoring_version,
            llm_model=versions.llm_model,
            embedding_model=versions.embedding_model,
            config_snapshot=versions.config_snapshot,
            idempotency_key=key,
            resume_profile_id=resume_profile_id,
            jd_profile_id=jd_profile_id,
        )
    except IntegrityError:
        # 并发入队：唯一键裁决，回滚后复用已有行（禁止 TOCTOU 的"先查再插"）。
        session.rollback()
        existing = analyses_repo.get_by_idempotency_key(session, key)
        if existing is None:  # pragma: no cover - 唯一约束冲突必然伴随已有行
            raise
        return existing, True
    return analysis, False

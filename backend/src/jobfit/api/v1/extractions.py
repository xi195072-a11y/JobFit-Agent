"""Phase 2 最小可调用入口：parse / artifacts / extract。

- 返回 Pydantic schema，绝不直接返回 SQLAlchemy model。
- 不返回 PII（只返回 id/版本/计数/状态/指纹）。
- 幂等：相同 artifact identity 复用（重复调用不会新增 artifact）。
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, sessionmaker

from jobfit.api.deps import get_llm_provider as get_provider
from jobfit.api.deps import get_session, get_session_factory, get_settings_dep
from jobfit.api.errors import ERROR_RESPONSES
from jobfit.api.schemas import (
    ArtifactSummary,
    DocumentArtifactsRead,
    ExtractionRunRead,
    ParseResultRead,
)
from jobfit.config.settings import Settings
from jobfit.core.errors import NotFound
from jobfit.db.repositories import analyses as analyses_repo
from jobfit.db.repositories import documents as docs_repo
from jobfit.db.repositories import parse_artifacts as pa_repo
from jobfit.llm.provider import LLMProvider
from jobfit.parsing.service import parse_document
from jobfit.workflow.runner import run_extraction_pipeline

router = APIRouter(tags=["phase2"], responses=ERROR_RESPONSES)

__all__ = ["get_provider", "get_settings_dep", "router"]


@router.post("/documents/{document_id}/parse", response_model=ParseResultRead)
def parse_document_endpoint(
    document_id: uuid.UUID,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
) -> ParseResultRead:
    document = docs_repo.get_by_id(session, document_id)
    if document is None:
        raise NotFound(f"document {document_id} not found")
    outcome = parse_document(session, settings, document)
    return ParseResultRead(
        document_id=document_id,
        parsed_document_id=outcome.parsed_document_id,
        parser_version=outcome.parser_version,
        actual_kind=outcome.actual_kind,
        chunk_count=outcome.chunk_count,
        chunks_created=outcome.chunks_created,
        reused_artifact=outcome.reused_artifact,
    )


@router.get(
    "/documents/{document_id}/artifacts",
    response_model=DocumentArtifactsRead,
    responses=ERROR_RESPONSES,
)
def list_document_artifacts(
    document_id: uuid.UUID,
    session: Session = Depends(get_session),
) -> DocumentArtifactsRead:
    document = docs_repo.get_by_id(session, document_id)
    if document is None:
        raise NotFound(f"document {document_id} not found")

    artifacts: list[ArtifactSummary] = []
    for parsed in pa_repo.list_parsed_documents(session, document_id):
        artifacts.append(
            ArtifactSummary(
                kind="parsed_document",
                artifact_id=parsed.id,
                version=parsed.parser_version,
                content_sha256=parsed.content_sha256,
                created_at=parsed.created_at,
                extra={"chunk_count": len(pa_repo.list_chunks(session, parsed.id))},
            )
        )
    for profile in pa_repo.list_resume_profiles(session, document_id):
        artifacts.append(
            ArtifactSummary(
                kind="resume_profile",
                artifact_id=profile.id,
                version=(
                    f"{profile.pipeline_version}|{profile.extraction_schema_version}"
                    f"|{profile.prompt_version}|{profile.llm_model}"
                ),
                content_sha256=str(profile.full_dump.get("_meta", {}).get("fingerprint", "")),
                created_at=profile.extracted_at,
                extra={"warning_count": len(profile.extraction_warnings or [])},
            )
        )
    for jd_profile in pa_repo.list_jd_profiles(session, document_id):
        artifacts.append(
            ArtifactSummary(
                kind="jd_profile",
                artifact_id=jd_profile.id,
                version=(
                    f"{jd_profile.pipeline_version}|{jd_profile.extraction_schema_version}"
                    f"|{jd_profile.prompt_version}|{jd_profile.llm_model}"
                ),
                content_sha256=str(jd_profile.full_dump.get("_meta", {}).get("fingerprint", "")),
                created_at=jd_profile.extracted_at,
                extra={"warning_count": len(jd_profile.extraction_warnings or [])},
            )
        )
    return DocumentArtifactsRead(document_id=document_id, artifacts=artifacts)


@router.post("/analyses/{analysis_id}/extract", response_model=ExtractionRunRead)
async def extract_analysis_endpoint(
    analysis_id: uuid.UUID,
    session: Session = Depends(get_session),
    session_factory: sessionmaker[Session] = Depends(get_session_factory),
    provider: LLMProvider = Depends(get_provider),
    settings: Settings = Depends(get_settings_dep),
) -> ExtractionRunRead:
    analysis = analyses_repo.get_analysis(session, analysis_id)
    if analysis is None:
        raise NotFound(f"analysis {analysis_id} not found")

    result = await run_extraction_pipeline(
        session_factory=session_factory,
        settings=settings,
        provider=provider,
        analysis_id=analysis_id,
    )
    if result.status != "completed":
        raise HTTPException(
            status_code=409,
            detail={"status": result.status, "errors": result.errors},
        )
    return ExtractionRunRead(
        analysis_id=analysis_id,
        status=result.status,
        resume_profile_id=_maybe_uuid(result.resume_profile_id),
        jd_profile_id=_maybe_uuid(result.jd_profile_id),
        resume_fingerprint=result.resume_fingerprint,
        jd_fingerprint=result.jd_fingerprint,
        errors=result.errors,
    )


def _maybe_uuid(value: str | None) -> uuid.UUID | None:
    return uuid.UUID(value) if value else None

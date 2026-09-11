"""Phase 2 抽取编排（parse → chunk → reserve → structured extraction → persist → bind）。

事务边界（ADR-017/§4.3）：
  短事务(读/建 artifact) → COMMIT → （await provider HTTP）→ 短事务(写 artifact/绑定)
任何 await provider 期间都不持有业务 DB 事务。

Attempt reservation（ADR-018）：每次真实 provider 调用前先
`reserve_llm_attempt` 并提交；失败即不调用 provider。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, TypeVar

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from jobfit.config.prompts import JD_PROMPT, REPAIR_PROMPT, RESUME_PROMPT, PromptRegistry
from jobfit.config.settings import Settings
from jobfit.core.errors import ConfigurationError, StructuredOutputError
from jobfit.core.schemas import JDProfile, ResumeProfile, strip_artifact_meta
from jobfit.db import models
from jobfit.db.repositories import analyses as analyses_repo
from jobfit.db.repositories import artifacts as artifacts_repo
from jobfit.db.repositories import documents as docs_repo
from jobfit.db.repositories import parse_artifacts as pa_repo
from jobfit.extraction.dto import LLMJdProfile, LLMResumeProfile
from jobfit.extraction.fingerprints import jd_profile_fingerprint, resume_profile_fingerprint
from jobfit.extraction.jd import build_jd_profile
from jobfit.extraction.resume import build_resume_profile
from jobfit.llm.provider import LLMProvider
from jobfit.observability.logging import get_logger
from jobfit.parsing.service import ParseOutcome, parse_document

PROMPT_CHAR_BUDGET = 12000
_LOG = get_logger(name="jobfit.extraction")


@dataclass
class ArtifactOutcome:
    profile_id: uuid.UUID
    created: bool
    fingerprint: str
    warnings: list[str] = field(default_factory=list)
    attempt_no: int | None = None
    llm_called: bool = False


@dataclass
class ExtractionOutcome:
    resume: ArtifactOutcome
    jd: ArtifactOutcome


def _chunks_for_prompt(chunks: list[models.DocumentChunk], budget: int = PROMPT_CHAR_BUDGET) -> tuple[str, bool]:
    """deterministic 截断：按 chunk_index 顺序取到预算，超出的部分不进 prompt。"""
    parts: list[str] = []
    used = 0
    truncated = False
    for chunk in chunks:
        line = f"[chunk:{chunk.chunk_index}] {chunk.content}"
        if used + len(line) > budget and parts:
            truncated = True
            break
        parts.append(line)
        used += len(line)
    return "\n".join(parts), truncated


def _sha_or_none(value: str | None) -> str | None:
    if not value:
        return None
    return artifacts_repo.sha256_hex(value)


def _assert_identity_matches(existing: Any, *, fingerprint: str, kind: str) -> None:
    """Phase 3 §4 Scenario B 守卫：禁止"解析版本变了但 pipeline_version 没变"的静默复用。

    profile 的**复用身份是五元组**（DB 唯一键），而 fingerprint 额外包含 `parsed_document_id`。
    因此当 parser / 解析库版本变化时，唯一键仍会命中旧行——若不检查，新的 analysis 会
    悄悄绑定**旧解析产物**产出的 profile。这里把"静默错误复用"变成显式、可执行的失败：
    要求 bump `PIPELINE_VERSION` 后重新入队（ADR-026 第 3 条）。
    """
    stored = str((existing.full_dump or {}).get("_meta", {}).get("fingerprint", ""))
    if stored == fingerprint:
        return
    raise ConfigurationError(
        f"{kind} artifact {existing.id} 已存在，但 fingerprint 不一致 "
        f"(stored={stored or '<missing>'!r}, computed={fingerprint})："
        "说明 parser/解析库版本发生了变化而 PIPELINE_VERSION 未变更。"
        "请 bump PIPELINE_VERSION 后重新入队，禁止静默复用旧解析产物（ADR-026/§4 Scenario B）。"
    )


# ------------------------------------------------------------------ lookup / reuse

def _find_resume_profile(
    session: Session, *, document_id: uuid.UUID, pipeline_version: str, schema_version: str,
    prompt_version: str, llm_model: str,
) -> models.ResumeProfile | None:
    return session.execute(
        select(models.ResumeProfile).where(
            models.ResumeProfile.document_id == document_id,
            models.ResumeProfile.pipeline_version == pipeline_version,
            models.ResumeProfile.extraction_schema_version == schema_version,
            models.ResumeProfile.prompt_version == prompt_version,
            models.ResumeProfile.llm_model == llm_model,
        )
    ).scalar_one_or_none()


def _find_jd_profile(
    session: Session, *, document_id: uuid.UUID, pipeline_version: str, schema_version: str,
    prompt_version: str, llm_model: str,
) -> models.JDProfile | None:
    return session.execute(
        select(models.JDProfile).where(
            models.JDProfile.document_id == document_id,
            models.JDProfile.pipeline_version == pipeline_version,
            models.JDProfile.extraction_schema_version == schema_version,
            models.JDProfile.prompt_version == prompt_version,
            models.JDProfile.llm_model == llm_model,
        )
    ).scalar_one_or_none()


def _ensure_parsed(session: Session, settings: Settings, document: models.Document) -> ParseOutcome:
    return parse_document(session, settings, document)


# --------------------------------------------------- attempt reservation + repair
T = TypeVar("T", bound=BaseModel)


def _reserve_attempt(
    session: Session,
    *,
    analysis: models.Analysis,
    claim_token: uuid.UUID | None,
    max_llm_attempts: int,
    phase: str,
) -> int | None:
    """真实 provider 调用前的原子预占（ADR-018）。

    `claim_token is None` 表示离线/无 lease 场景（测试直调），此时不预占也不计数。
    """
    if claim_token is None:
        return None
    return analyses_repo.reserve_llm_attempt(
        session,
        analysis_id=analysis.id,
        claim_token=claim_token,
        max_llm_attempts=max_llm_attempts,
        phase=phase,
    )


async def _complete_structured_with_repair(
    session: Session,
    settings: Settings,
    *,
    analysis: models.Analysis,
    claim_token: uuid.UUID | None,
    provider: LLMProvider,
    max_llm_attempts: int,
    registry: PromptRegistry,
    prompt: str,
    schema: type[T],
    chunks_text: str,
    phase: str,
) -> tuple[T, int | None]:
    """structured 抽取 + schema 校验失败后的 repair retry（ADR-001/ADR-002/ADR-018）。

    - **每次**真实 provider 调用前都先 `reserve_llm_attempt`（预占不因 crash 回滚）；
    - 重试上限 `settings.max_repair_retries`，重试 prompt 携带上一次的校验错误；
    - 预算耗尽时 `ReservationFailed` 直接上抛（不吞掉预算错误）；
    - 重试仍失败则抛最后一次 `StructuredOutputError`，由上层决定降级/失败语义。

    返回 `(schema 实例, 产出该结果的 attempt_no)`。
    """
    attempt_no = _reserve_attempt(
        session,
        analysis=analysis,
        claim_token=claim_token,
        max_llm_attempts=max_llm_attempts,
        phase=phase,
    )
    try:
        return await provider.complete_structured(prompt, schema=schema), attempt_no
    except StructuredOutputError as exc:
        last_error = exc

    for _ in range(settings.max_repair_retries):
        attempt_no = _reserve_attempt(
            session,
            analysis=analysis,
            claim_token=claim_token,
            max_llm_attempts=max_llm_attempts,
            phase=f"{phase}_repair",
        )
        repair_prompt = registry.get(REPAIR_PROMPT).render(
            chunks=chunks_text, error=str(last_error)
        )
        _LOG.info(
            "structured_repair_retry",
            analysis_id=str(analysis.id),
            phase=phase,
            attempt_no=attempt_no,
        )
        try:
            return await provider.complete_structured(repair_prompt, schema=schema), attempt_no
        except StructuredOutputError as retry_exc:
            last_error = retry_exc
    raise last_error


# ------------------------------------------------------------------ resume

async def reuse_or_extract_resume(
    session: Session,
    settings: Settings,
    *,
    analysis: models.Analysis,
    claim_token: uuid.UUID | None,
    provider: LLMProvider,
    max_llm_attempts: int,
    bind: bool = True,
) -> ArtifactOutcome:
    document = docs_repo.get_by_id(session, analysis.resume_document_id)
    if document is None:
        raise LookupError("resume document missing")
    parse_outcome = _ensure_parsed(session, settings, document)
    chunks = pa_repo.list_chunks(session, parse_outcome.parsed_document_id)

    registry = PromptRegistry(settings.config_dir / "prompts")
    prompt_version = registry.combined_version()
    fingerprint = resume_profile_fingerprint(
        document_id=str(document.id),
        parsed_document_id=str(parse_outcome.parsed_document_id),
        pipeline_version=analysis.pipeline_version,
        extraction_schema_version=ResumeProfile.SCHEMA_VERSION,
        prompt_version=prompt_version,
        llm_model=analysis.llm_model or "",
    )

    existing = _find_resume_profile(
        session,
        document_id=document.id,
        pipeline_version=analysis.pipeline_version,
        schema_version=ResumeProfile.SCHEMA_VERSION,
        prompt_version=prompt_version,
        llm_model=analysis.llm_model or "",
    )
    if existing is not None:
        # 复用：不调用 LLM、不消耗 attempt；若历史运行中断导致子行缺失则补齐（幂等）
        _assert_identity_matches(existing, fingerprint=fingerprint, kind="resume_profile")
        domain = ResumeProfile.model_validate(strip_artifact_meta(existing.full_dump))
        pa_repo.create_resume_children(session, profile_id=existing.id, profile=domain)
        if bind and claim_token is not None:
            analyses_repo.fenced_bind_profile(
                session,
                analysis_id=analysis.id,
                claim_token=claim_token,
                profile_kind="resume",
                profile_id=existing.id,
            )
        return ArtifactOutcome(
            profile_id=existing.id,
            created=False,
            fingerprint=fingerprint,
            warnings=list(domain.extraction_warnings),
        )

    # 真实 provider 调用前：先原子预占并提交（reservation 不因 crash 回滚）；
    # 首次 structured output 校验失败时按 max_repair_retries 带纠错提示重试（ADR-018）。
    chunks_text, truncated = _chunks_for_prompt(chunks)
    prompt = registry.get(RESUME_PROMPT).render(chunks=chunks_text)
    dto, attempt_no = await _complete_structured_with_repair(
        session,
        settings,
        analysis=analysis,
        claim_token=claim_token,
        provider=provider,
        max_llm_attempts=max_llm_attempts,
        registry=registry,
        prompt=prompt,
        schema=LLMResumeProfile,
        chunks_text=chunks_text,
        phase="extract_resume",
    )

    build = build_resume_profile(
        dto=dto,
        document_id=str(document.id),
        parsed_document_id=str(parse_outcome.parsed_document_id),
        chunks=chunks,
    )
    warnings = list(build.warnings)
    if truncated:
        warnings.append("prompt_budget_truncated_chunks")
    profile = build.profile.model_copy(update={"extraction_warnings": warnings})
    full_dump = profile.model_dump(mode="json")
    full_dump["_meta"] = {
        "fingerprint": fingerprint,
        "prompt_version": prompt_version,
        "parser_version": parse_outcome.parser_version,
        "schema_version": ResumeProfile.SCHEMA_VERSION,
        "llm_model": analysis.llm_model,
        "attempt_no": attempt_no,
    }
    row, created = artifacts_repo.get_or_create_resume_profile(
        session,
        document_id=document.id,
        parsed_document_id=parse_outcome.parsed_document_id,
        pipeline_version=analysis.pipeline_version,
        extraction_schema_version=ResumeProfile.SCHEMA_VERSION,
        prompt_version=prompt_version,
        llm_model=analysis.llm_model or "",
        full_dump=full_dump,
        extraction_warnings=warnings,
        pii_hashes={
            "name": _sha_or_none(profile.name),
            "phone": _sha_or_none(profile.phone),
            "email": _sha_or_none(profile.email),
        },
    )
    pa_repo.create_resume_children(session, profile_id=row.id, profile=profile)
    if bind and claim_token is not None:
        analyses_repo.fenced_bind_profile(
            session,
            analysis_id=analysis.id,
            claim_token=claim_token,
            profile_kind="resume",
            profile_id=row.id,
        )
    _LOG.info(
        "artifact_persisted",
        artifact="resume_profile",
        profile_id=str(row.id),
        fingerprint=fingerprint,
        attempt_no=attempt_no,
        chunk_count=len(chunks),
        warning_count=len(warnings),
        created=created,
    )
    return ArtifactOutcome(
        profile_id=row.id,
        created=created,
        fingerprint=fingerprint,
        warnings=warnings,
        attempt_no=attempt_no,
        llm_called=True,
    )


# ------------------------------------------------------------------ jd
async def reuse_or_extract_jd(
    session: Session,
    settings: Settings,
    *,
    analysis: models.Analysis,
    claim_token: uuid.UUID | None,
    provider: LLMProvider,
    max_llm_attempts: int,
    bind: bool = True,
) -> ArtifactOutcome:
    document = docs_repo.get_by_id(session, analysis.jd_document_id)
    if document is None:
        raise LookupError("jd document missing")
    parse_outcome = _ensure_parsed(session, settings, document)
    chunks = pa_repo.list_chunks(session, parse_outcome.parsed_document_id)

    registry = PromptRegistry(settings.config_dir / "prompts")
    prompt_version = registry.combined_version()
    fingerprint = jd_profile_fingerprint(
        document_id=str(document.id),
        parsed_document_id=str(parse_outcome.parsed_document_id),
        pipeline_version=analysis.pipeline_version,
        extraction_schema_version=JDProfile.SCHEMA_VERSION,
        prompt_version=prompt_version,
        llm_model=analysis.llm_model or "",
    )

    existing = _find_jd_profile(
        session,
        document_id=document.id,
        pipeline_version=analysis.pipeline_version,
        schema_version=JDProfile.SCHEMA_VERSION,
        prompt_version=prompt_version,
        llm_model=analysis.llm_model or "",
    )
    if existing is not None:
        _assert_identity_matches(existing, fingerprint=fingerprint, kind="jd_profile")
        domain = JDProfile.model_validate(strip_artifact_meta(existing.full_dump))
        pa_repo.create_jd_requirements(session, profile_id=existing.id, profile=domain)
        if bind and claim_token is not None:
            analyses_repo.fenced_bind_profile(
                session,
                analysis_id=analysis.id,
                claim_token=claim_token,
                profile_kind="jd",
                profile_id=existing.id,
            )
        return ArtifactOutcome(
            profile_id=existing.id,
            created=False,
            fingerprint=fingerprint,
            warnings=list(domain.extraction_warnings),
        )

    # 同上：先预占，再调用；校验失败走 repair retry（ADR-018）。
    chunks_text, truncated = _chunks_for_prompt(chunks)
    prompt = registry.get(JD_PROMPT).render(chunks=chunks_text)
    dto, attempt_no = await _complete_structured_with_repair(
        session,
        settings,
        analysis=analysis,
        claim_token=claim_token,
        provider=provider,
        max_llm_attempts=max_llm_attempts,
        registry=registry,
        prompt=prompt,
        schema=LLMJdProfile,
        chunks_text=chunks_text,
        phase="extract_jd",
    )

    build = build_jd_profile(
        dto=dto,
        document_id=str(document.id),
        parsed_document_id=str(parse_outcome.parsed_document_id),
        chunks=chunks,
    )
    warnings = list(build.warnings)
    if truncated:
        warnings.append("prompt_budget_truncated_chunks")
    profile = build.profile.model_copy(update={"extraction_warnings": warnings})
    full_dump = profile.model_dump(mode="json")
    full_dump["_meta"] = {
        "fingerprint": fingerprint,
        "prompt_version": prompt_version,
        "parser_version": parse_outcome.parser_version,
        "schema_version": JDProfile.SCHEMA_VERSION,
        "llm_model": analysis.llm_model,
        "attempt_no": attempt_no,
    }
    row, created = artifacts_repo.get_or_create_jd_profile(
        session,
        document_id=document.id,
        parsed_document_id=parse_outcome.parsed_document_id,
        pipeline_version=analysis.pipeline_version,
        extraction_schema_version=JDProfile.SCHEMA_VERSION,
        prompt_version=prompt_version,
        llm_model=analysis.llm_model or "",
        full_dump=full_dump,
        extraction_warnings=warnings,
    )
    pa_repo.create_jd_requirements(session, profile_id=row.id, profile=profile)
    if bind and claim_token is not None:
        analyses_repo.fenced_bind_profile(
            session,
            analysis_id=analysis.id,
            claim_token=claim_token,
            profile_kind="jd",
            profile_id=row.id,
        )
    _LOG.info(
        "artifact_persisted",
        artifact="jd_profile",
        profile_id=str(row.id),
        fingerprint=fingerprint,
        attempt_no=attempt_no,
        chunk_count=len(chunks),
        warning_count=len(warnings),
        created=created,
    )
    return ArtifactOutcome(
        profile_id=row.id,
        created=created,
        fingerprint=fingerprint,
        warnings=warnings,
        attempt_no=attempt_no,
        llm_called=True,
    )


# ------------------------------------------------------------------ compose

async def extract_for_analysis(
    session: Session,
    settings: Settings,
    *,
    analysis: models.Analysis,
    claim_token: uuid.UUID | None,
    provider: LLMProvider,
    max_llm_attempts: int,
    bind: bool = True,
) -> ExtractionOutcome:
    """一次完成 resume + jd 两个 extraction artifact（各自独立 reservation）。"""
    resume_outcome = await reuse_or_extract_resume(
        session,
        settings,
        analysis=analysis,
        claim_token=claim_token,
        provider=provider,
        max_llm_attempts=max_llm_attempts,
        bind=bind,
    )
    jd_outcome = await reuse_or_extract_jd(
        session,
        settings,
        analysis=analysis,
        claim_token=claim_token,
        provider=provider,
        max_llm_attempts=max_llm_attempts,
        bind=bind,
    )
    return ExtractionOutcome(resume=resume_outcome, jd=jd_outcome)

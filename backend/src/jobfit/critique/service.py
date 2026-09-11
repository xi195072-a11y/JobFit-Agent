"""Critique 编排服务（Phase 4 §16–§21）。

事务边界（与 Phase 2/3 一致）：
  短事务(guard lease + 构建受控 context + fingerprint + attempt reservation)
    → COMMIT（reservation 不因 crash 回滚）
    → provider HTTP（不持有业务事务）
    → 校验（schema → citation → UNKNOWN 语义）
    → 短事务(guard lease + 幂等 upsert critique) → COMMIT

- 幂等（§20/§21）：同 fingerprint（analysis+prompt+schema+provider+model+config）=> 复用旧行。
- 无 provider（EXTERNAL CREDENTIAL BLOCKED）：落 `status=unavailable` 记录，不伪造 live 验证。
- 任何 citation invalid => `validation_status=rejected`（audit 留档，report 不得以 validated 引用）。
- 只读 critique 输入 = CritiqueContext（§7）；从未修改确定性结果表。
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session, sessionmaker

from jobfit.core.enums import CritiqueStatus, ValidationStatus
from jobfit.core.errors import LeaseLost, StructuredOutputError
from jobfit.critique.context import CritiqueContext, load_critique_context
from jobfit.critique.fingerprint import critique_fingerprint
from jobfit.critique.prompt import CRITIQUE_PROMPT_VERSION, build_critique_prompt
from jobfit.critique.schema import SCHEMA_VERSION, CritiqueSchema
from jobfit.critique.validation import (
    CitationValidationResult,
    CitationValidator,
    load_validation_store,
)
from jobfit.db import models
from jobfit.db.repositories import critiques as critiques_repo
from jobfit.db.repositories import results as results_repo
from jobfit.llm.provider import LLMProvider
from jobfit.observability.logging import get_logger

_LOG = get_logger(name="jobfit.critique")


@dataclass
class CritiqueOutcome:
    critique: models.Critique | None = None
    reused: bool = False
    unavailable: bool = False
    validation: CitationValidationResult | None = None
    fingerprint: str | None = None


def _build_fingerprint(
    *, analysis_id: uuid.UUID, prompt_version: str, provider: str, model: str, ctx: CritiqueContext
) -> str:
    return critique_fingerprint(
        analysis_id=str(analysis_id),
        prompt_version=prompt_version,
        provider=provider,
        model=model,
        config_snapshot=ctx.config_snapshot,
    )


async def generate_critique(
    *,
    session_factory: sessionmaker[Session],
    analysis_id: uuid.UUID,
    claim_token: uuid.UUID,
    provider: LLMProvider | None,
    max_llm_attempts: int,
    prompt_version: str = CRITIQUE_PROMPT_VERSION,
) -> CritiqueOutcome:
    """生成/复用一次 critique（fenced）。返回已有行或新建行。"""
    model = provider.model_name if provider is not None else ""

    # ---- 短事务 1：guard + context + fingerprint + 幂等检查 + attempt reservation
    with session_factory() as session:
        if not results_repo.guard_lease(session, analysis_id=analysis_id, claim_token=claim_token):
            session.rollback()
            raise LeaseLost(
                f"lease lost before critique (analysis={analysis_id}); stale worker 不得写 critique"
            )
        ctx = load_critique_context(session, analysis_id=analysis_id)
        fingerprint = _build_fingerprint(
            analysis_id=analysis_id,
            prompt_version=prompt_version,
            provider="deepseek",
            model=model,
            ctx=ctx,
        )
        existing = critiques_repo.get_latest_critique(session, analysis_id)
        if existing is not None and existing.fingerprint == fingerprint:
            _LOG.info("critique_reused", analysis_id=str(analysis_id), fingerprint=fingerprint)
            return CritiqueOutcome(critique=existing, reused=True, fingerprint=fingerprint)

        version = critiques_repo.next_critique_version(session, analysis_id)

        if provider is None:
            # EXTERNAL CREDENTIAL BLOCKED：只记录不可用，不伪造 live 验证。
            created = critiques_repo.upsert_critique(
                session,
                analysis_id=analysis_id,
                version=version,
                status=CritiqueStatus.UNAVAILABLE.value,
                model=model,
                provider="deepseek",
                prompt_version=prompt_version,
                schema_version=SCHEMA_VERSION,
                content={"unavailable": True, "reason": "external_credential_blocked"},
                validation_status=ValidationStatus.PENDING.value,
                citations_validated=False,
                fingerprint=fingerprint,
            )
            row = critiques_repo.get_latest_critique(session, analysis_id)
            assert row is not None
            _LOG.warning(
                "critique_unavailable",
                analysis_id=str(analysis_id),
                created=created,
                reason="external_credential_blocked",
            )
            return CritiqueOutcome(critique=row, unavailable=True, fingerprint=fingerprint)

        # attempt reservation：先于 HTTP、独立提交（ADR-018 / §4.4）
        from jobfit.db.repositories import analyses as analyses_repo

        analyses_repo.reserve_llm_attempt(
            session,
            analysis_id=analysis_id,
            claim_token=claim_token,
            max_llm_attempts=max_llm_attempts,
            phase="critique",
        )
        prompt_dict = ctx.to_prompt_dict()

    # ---- provider HTTP（不持有业务事务）
    prompt_text = build_critique_prompt(prompt_dict)
    started = time.monotonic()
    try:
        critique = await provider.complete_structured(prompt_text, schema=CritiqueSchema)  # type: ignore[union-attr]
        unavailable = False
        latency_ms = int((time.monotonic() - started) * 1000)
    except StructuredOutputError as exc:
        _LOG.warning(
            "critique_schema_failed",
            analysis_id=str(analysis_id),
            error=str(exc)[:200],
        )
        critique = None
        unavailable = True
        latency_ms = int((time.monotonic() - started) * 1000)

    # ---- 短事务 2：校验 + 幂等 upsert（fenced）
    validation: CitationValidationResult | None = None
    validation_status = ValidationStatus.PENDING.value
    citations_validated = False
    content: dict = {"unavailable": True, "reason": "structured_output_failed"}

    if critique is not None:
        with session_factory() as session:
            if not results_repo.guard_lease(session, analysis_id=analysis_id, claim_token=claim_token):
                session.rollback()
                raise LeaseLost(
                    f"lease lost during critique (analysis={analysis_id}); 结果丢弃、不落库"
                )
            store = load_validation_store(session, analysis_id=analysis_id)
            validation = CitationValidator().validate(critique, store)
        if validation.valid:
            validation_status = ValidationStatus.VALIDATED.value
            citations_validated = True
        else:
            validation_status = ValidationStatus.REJECTED.value
        content = critique.as_dict()

    with session_factory() as session:
        if not results_repo.guard_lease(session, analysis_id=analysis_id, claim_token=claim_token):
            session.rollback()
            raise LeaseLost(f"lease lost before critique persist (analysis={analysis_id}); 结果丢弃")
        critiques_repo.upsert_critique(
            session,
            analysis_id=analysis_id,
            version=version,
            status=(
                CritiqueStatus.UNAVAILABLE.value if unavailable else CritiqueStatus.OK.value
            ),
            model=model,
            provider="deepseek",
            prompt_version=prompt_version,
            schema_version=SCHEMA_VERSION,
            content=content,
            validation_status=validation_status,
            citations_validated=citations_validated,
            fingerprint=fingerprint,
            latency_ms=latency_ms,
            tokens_in=None,
            tokens_out=None,
        )
        row = critiques_repo.get_latest_critique(session, analysis_id)
        assert row is not None
    _LOG.info(
        "critique_persisted",
        analysis_id=str(analysis_id),
        status=row.status,
        validation=validation_status,
        citations_validated=citations_validated,
    )
    return CritiqueOutcome(
        critique=row, unavailable=unavailable, validation=validation, fingerprint=fingerprint
    )


__all__ = ["CritiqueOutcome", "generate_critique"]

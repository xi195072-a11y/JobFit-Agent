# mypy: disable-error-code="arg-type,attr-defined,assignment,union-attr,index"
"""integration: LLM attempt reservation 顺序/预算/stale fencing（ADR-018 / 需求 17）。"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from jobfit.core.errors import ReservationFailed, StructuredOutputError
from jobfit.db import models
from jobfit.db.repositories import analyses as analyses_repo
from jobfit.extraction.dto import LLMJdProfile, LLMResumeProfile
from jobfit.workflow.runner import run_extraction_pipeline
from support import (
    JD_PAYLOAD,
    RESUME_PAYLOAD,
    DeterministicProvider,
    make_analysis,
    make_document,
    read_fixture_bytes,
)

pytestmark = pytest.mark.db


class _FlakyStructuredProvider:
    """resume 抽取的前 `fail_times` 次抛 StructuredOutputError，之后返回合法 payload。"""

    def __init__(self, *, fail_times: int = 1) -> None:
        self.model_name = "flaky-structured"
        self._remaining = fail_times
        self.calls: list[str] = []
        self.prompts: list[str] = []

    async def complete_text(self, prompt: str, *, max_tokens: int | None = None) -> str:
        raise AssertionError("structured extraction must not use complete_text")

    async def complete_structured(self, prompt: str, *, schema: Any) -> Any:
        self.prompts.append(prompt)
        if schema is LLMResumeProfile:
            if self._remaining > 0:
                self._remaining -= 1
                self.calls.append("resume:fail")
                raise StructuredOutputError("schema validation failed: field required")
            self.calls.append("resume:ok")
            return schema.model_validate(RESUME_PAYLOAD)
        assert schema is LLMJdProfile
        self.calls.append("jd:ok")
        return schema.model_validate(JD_PAYLOAD)


def _attempt_rows(session: Session, analysis_id: Any) -> int:
    return int(
        session.execute(
            select(func.count())
            .select_from(models.LlmAttemptLog)
            .where(models.LlmAttemptLog.analysis_id == analysis_id)
        ).scalar_one()
    )


def _prepare(session: Session, settings) -> models.Analysis:
    resume = make_document(
        session, settings, kind="resume", filename="resume.txt", content=read_fixture_bytes("resume.txt")
    )
    jd = make_document(session, settings, kind="jd", filename="jd.txt", content=read_fixture_bytes("jd.txt"))
    return make_analysis(session, resume_document_id=resume.id, jd_document_id=jd.id)


def test_attempt_row_is_committed_before_provider_call(session: Session, session_factory, db_settings) -> None:
    analysis = _prepare(session, db_settings)
    observations: list[int] = []

    def probe(_schema: type) -> None:
        with session_factory() as probe_session:
            count = probe_session.execute(
                select(func.count())
                .select_from(models.LlmAttemptLog)
                .where(models.LlmAttemptLog.analysis_id == analysis.id)
            ).scalar_one()
        observations.append(int(count))

    provider = DeterministicProvider(on_call=probe)
    result = asyncio.run(
        run_extraction_pipeline(
            session_factory=session_factory, settings=db_settings, provider=provider, analysis_id=analysis.id
        )
    )
    assert result.status == "completed", result.errors
    # provider 被调用时，对应 attempt 行必须已经提交可见
    assert observations == [1, 2]
    assert provider.calls == ["resume", "jd"]


def test_budget_exhaustion_blocks_second_provider_call(
    session: Session, session_factory, db_settings
) -> None:
    analysis = _prepare(session, db_settings)
    constrained = db_settings.model_copy(update={"max_llm_attempts": 1})
    provider = DeterministicProvider()
    result = asyncio.run(
        run_extraction_pipeline(
            session_factory=session_factory, settings=constrained, provider=provider, analysis_id=analysis.id
        )
    )
    assert result.status == "failed"
    assert any("ReservationFailed" in err for err in result.errors)
    assert provider.calls == ["resume"]  # 第二次真实调用被预算拒绝

    session.expire_all()
    row = session.get(models.Analysis, analysis.id)
    assert row is not None
    assert row.status == "failed"
    assert row.current_phase is not None and row.current_phase.startswith("failed:ReservationFailed")
    attempts = session.execute(
        select(func.count()).select_from(models.LlmAttemptLog).where(models.LlmAttemptLog.analysis_id == analysis.id)
    ).scalar_one()
    assert attempts == 1
    # 第一次调用的 artifact 已落库（已提交结果不因后续失败而回滚）
    profiles = session.execute(select(func.count()).select_from(models.ResumeProfile)).scalar_one()
    assert profiles == 1


def test_stale_claim_token_cannot_reserve(session: Session, db_settings) -> None:
    analysis = _prepare(session, db_settings)
    token = analyses_repo.claim_specific(session, analysis_id=analysis.id, worker_id="w1", ttl_seconds=120)
    assert token is not None
    # 模拟被 reclaim：状态回到 queued 且 token 失效
    session.execute(
        text("UPDATE analyses SET status='queued', claim_token=NULL WHERE id=:id"), {"id": analysis.id}
    )
    session.commit()
    with pytest.raises(ReservationFailed):
        analyses_repo.reserve_llm_attempt(
            session, analysis_id=analysis.id, claim_token=token, max_llm_attempts=5
        )
    attempts = session.execute(
        select(func.count()).select_from(models.LlmAttemptLog).where(models.LlmAttemptLog.analysis_id == analysis.id)
    ).scalar_one()
    assert attempts == 0


def test_expired_lease_cannot_reserve(session: Session, db_settings) -> None:
    analysis = _prepare(session, db_settings)
    token = analyses_repo.claim_specific(session, analysis_id=analysis.id, worker_id="w1", ttl_seconds=120)
    assert token is not None
    session.execute(
        text("UPDATE analyses SET lease_expires_at = clock_timestamp() - interval '5 seconds' WHERE id=:id"),
        {"id": analysis.id},
    )
    session.commit()
    with pytest.raises(ReservationFailed):
        analyses_repo.reserve_llm_attempt(
            session, analysis_id=analysis.id, claim_token=token, max_llm_attempts=5
        )


# ------------------------------------------------- schema validation repair retry


def test_schema_failure_triggers_repair_retry(
    session: Session, session_factory, db_settings
) -> None:
    """ADR-001/ADR-018：首次 structured output 校验失败 → 带纠错提示 repair retry。

    断言 repair 是**第二次真实调用**且携带上一次的校验错误，并且额外预占一次 attempt。
    """
    analysis = _prepare(session, db_settings)
    provider = _FlakyStructuredProvider(fail_times=1)

    result = asyncio.run(
        run_extraction_pipeline(
            session_factory=session_factory,
            settings=db_settings,
            provider=provider,
            analysis_id=analysis.id,
        )
    )

    assert result.status == "completed", result.errors
    assert provider.calls == ["resume:fail", "resume:ok", "jd:ok"]
    assert provider.prompts[1] != provider.prompts[0]
    assert "schema validation failed" in provider.prompts[1]  # 纠错提示确实带上了
    # resume 失败 1 次 + repair 1 次 + jd 1 次：每次真实调用各预占一次
    assert _attempt_rows(session, analysis.id) == 3


def test_repair_exhausted_marks_analysis_failed(
    session: Session, session_factory, db_settings
) -> None:
    """重试用尽后抛最后一次 StructuredOutputError，不再继续后续节点。"""
    analysis = _prepare(session, db_settings)
    provider = _FlakyStructuredProvider(fail_times=2)

    result = asyncio.run(
        run_extraction_pipeline(
            session_factory=session_factory,
            settings=db_settings,
            provider=provider,
            analysis_id=analysis.id,
        )
    )

    assert result.status == "failed"
    assert any("StructuredOutputError" in err for err in result.errors)
    assert provider.calls == ["resume:fail", "resume:fail"]
    session.expire_all()
    row = session.get(models.Analysis, analysis.id)
    assert row is not None
    assert row.current_phase == "failed:StructuredOutputError"
    assert _attempt_rows(session, analysis.id) == 2  # 1 次原始 + 1 次 repair


def test_repair_disabled_fails_fast(session: Session, session_factory, db_settings) -> None:
    """`max_repair_retries=0` 时不做任何 repair 重试（保持原有 fail-fast 语义）。"""
    analysis = _prepare(session, db_settings)
    no_repair = db_settings.model_copy(update={"max_repair_retries": 0})
    provider = _FlakyStructuredProvider(fail_times=1)

    result = asyncio.run(
        run_extraction_pipeline(
            session_factory=session_factory,
            settings=no_repair,
            provider=provider,
            analysis_id=analysis.id,
        )
    )

    assert result.status == "failed"
    assert provider.calls == ["resume:fail"]
    assert _attempt_rows(session, analysis.id) == 1

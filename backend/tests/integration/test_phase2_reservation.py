# mypy: disable-error-code="arg-type,attr-defined,assignment,union-attr,index"
"""integration: LLM attempt reservation 顺序/预算/stale fencing（ADR-018 / 需求 17）。"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from jobfit.core.errors import ReservationFailed
from jobfit.db import models
from jobfit.db.repositories import analyses as analyses_repo
from jobfit.workflow.runner import run_extraction_pipeline
from support import DeterministicProvider, make_analysis, make_document, read_fixture_bytes

pytestmark = pytest.mark.db


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

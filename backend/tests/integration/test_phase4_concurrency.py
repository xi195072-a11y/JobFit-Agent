# mypy: disable-error-code="arg-type,attr-defined,assignment,union-attr,return-value"
"""integration: Phase 4 并发安全（§30/§16/§20）——真实 PG + 真实并发线程。

- claim_phase4 竞态：最多一个 worker 领到 Phase 4 续接；
- 并发 approve：单条条件 UPDATE 原子裁决，只一个 reviewer 生效（无 double finalize）；
- 并发 critique 同 fingerprint 写入：UNIQUE + ON CONFLICT 收敛为单行（幂等）。
"""

from __future__ import annotations

import threading
import uuid

import pytest
from sqlalchemy.orm import Session, sessionmaker

from jobfit.core.enums import AnalysisStatus, ReviewDecision
from jobfit.core.errors import Conflict
from jobfit.db import models
from jobfit.db.repositories import analyses as analyses_repo
from jobfit.db.repositories import critiques as critiques_repo
from jobfit.db.repositories import reports as reports_repo
from jobfit.db.repositories import reviews as reviews_repo
from jobfit.review.service import apply_review
from support import (
    JD_MATCH_PAYLOAD,
    RESUME_MATCH_PAYLOAD,
    DeterministicProvider,
    build_valid_critique_payload,
    make_analysis_pair,
    run_analysis,
    run_phase4,
)

pytestmark = pytest.mark.db


def _succeeded_match(
    session: Session, session_factory: sessionmaker[Session], settings
) -> uuid.UUID:
    pair = make_analysis_pair(
        session, settings, resume_fixture="resume_match.txt", jd_fixture="jd_match.txt"
    )
    result = run_analysis(
        session_factory=session_factory,
        settings=settings,
        analysis_id=pair.analysis.id,
        provider=DeterministicProvider(
            resume_payload=dict(RESUME_MATCH_PAYLOAD), jd_payload=dict(JD_MATCH_PAYLOAD)
        ),
    )
    assert result.status == "completed"
    return pair.analysis.id


def _full_phase4(
    session: Session, session_factory: sessionmaker[Session], settings
) -> uuid.UUID:
    """succeeded -> (validated critique -> validated report -> awaiting_review)。"""
    analysis_id = _succeeded_match(session, session_factory, settings)
    result = run_phase4(
        session_factory=session_factory,
        settings=settings,
        analysis_id=analysis_id,
        provider=DeterministicProvider(critique_payload=build_valid_critique_payload),
    )
    assert result.status == "completed"
    assert result.critique_validation == "validated"
    assert result.report_stage == "validated"
    return analysis_id


def test_concurrent_phase4_claim_only_one_wins(
    session: Session, session_factory: sessionmaker[Session], db_settings
) -> None:
    analysis_id = _succeeded_match(session, session_factory, db_settings)
    barrier = threading.Barrier(2)
    tokens: list[uuid.UUID | None] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def _worker(worker_id: str) -> None:
        try:
            with session_factory() as work:
                barrier.wait(timeout=10)
                token = analyses_repo.claim_phase4(
                    work, analysis_id=analysis_id, worker_id=worker_id, ttl_seconds=120
                )
            with lock:
                tokens.append(token)
        except BaseException as exc:  # noqa: BLE001 - 线程异常带回主线程
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=_worker, args=(f"p4-{i}",)) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert not errors, errors
    winners = [token for token in tokens if token is not None]
    assert len(winners) == 1  # §39：只一个 worker 领到 Phase 4 续接
    session.expire_all()
    row = session.get(models.Analysis, analysis_id)
    assert row is not None
    assert row.status == AnalysisStatus.RUNNING.value
    assert row.claim_token in winners


def test_concurrent_approve_only_one_wins(
    session: Session, session_factory: sessionmaker[Session], db_settings
) -> None:
    analysis_id = _full_phase4(session, session_factory, db_settings)
    barrier = threading.Barrier(2)
    outcomes: list[str] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def _approve(reviewer: str) -> None:
        try:
            barrier.wait(timeout=10)
            outcome = apply_review(
                session_factory,
                analysis_id=analysis_id,
                decision=ReviewDecision.APPROVE.value,
                comments="ok",
                reviewed_by=reviewer,
            )
            with lock:
                outcomes.append(outcome.to_state)
        except BaseException as exc:  # noqa: BLE001
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=_approve, args=(f"reviewer-{i}",)) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    # 一个成功，一个 Conflict（0-row 条件 UPDATE，§30）
    assert len(outcomes) == 1 and outcomes[0] == AnalysisStatus.FINALIZED.value
    assert any(isinstance(exc, Conflict) for exc in errors), errors

    session.expire_all()
    row = session.get(models.Analysis, analysis_id)
    assert row is not None
    assert row.status == AnalysisStatus.FINALIZED.value
    report = reports_repo.get_latest_report(session, analysis_id)
    assert report is not None and report.stage == "final"
    # 只落一条 approve 审计（无 double finalize / lost update）
    assert len(reviews_repo.list_reviews(session, analysis_id)) == 1


def test_concurrent_critique_upserts_converge_to_single_row(
    session: Session, session_factory: sessionmaker[Session], db_settings
) -> None:
    """两个 worker 用同一 claim 并发写同 fingerprint critique：收敛为单行（§20/§21）。"""
    analysis_id = _succeeded_match(session, session_factory, db_settings)
    token = analyses_repo.claim_phase4(
        session, analysis_id=analysis_id, worker_id="p4-writer", ttl_seconds=120
    )
    assert token is not None

    import asyncio

    from jobfit.critique.service import generate_critique

    provider = DeterministicProvider(critique_payload=build_valid_critique_payload)

    def _write() -> None:
        asyncio.run(
            generate_critique(
                session_factory=session_factory,
                analysis_id=analysis_id,
                claim_token=token,
                provider=provider,
                max_llm_attempts=10,
            )
        )

    errors: list[BaseException] = []
    barrier = threading.Barrier(2)

    def _thread() -> None:
        try:
            barrier.wait(timeout=10)
            _write()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=_thread) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert not errors, errors

    session.expire_all()
    rows = critiques_repo.list_critiques(session, analysis_id)
    assert len(rows) == 1  # UNIQUE(analysis_id, fingerprint) + ON CONFLICT DO NOTHING
    assert rows[0].validation_status == "validated"

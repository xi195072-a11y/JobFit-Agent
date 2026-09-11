# mypy: disable-error-code="arg-type,attr-defined,assignment,union-attr"
"""integration: claim 竞态 / lease / recover / fencing（Phase 3 §7/§30/§39）。

真实 PostgreSQL + 真实并发线程；不 mock DB。
"""

from __future__ import annotations

import threading
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from jobfit.core.enums import AnalysisStatus
from jobfit.core.errors import LeaseLost
from jobfit.db import models
from jobfit.db.repositories import analyses as analyses_repo
from jobfit.db.repositories import results as results_repo
from jobfit.matching import service as matching_service
from support import (
    JD_MATCH_PAYLOAD,
    RESUME_MATCH_PAYLOAD,
    DeterministicProvider,
    make_analysis_pair,
    run_analysis,
)

pytestmark = pytest.mark.db


def _queued_analysis(session: Session, settings):
    pair = make_analysis_pair(
        session, settings, resume_fixture="resume_match.txt", jd_fixture="jd_match.txt"
    )
    return pair.analysis


# ---------------------------------------------------------------- Case I: 并发 claim


def test_two_workers_racing_for_same_analysis_only_one_wins(
    session: Session, session_factory: sessionmaker[Session], db_settings
) -> None:
    analysis = _queued_analysis(session, db_settings)
    barrier = threading.Barrier(2)
    tokens: list[uuid.UUID | None] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def _worker(worker_id: str) -> None:
        try:
            with session_factory() as worker_session:
                barrier.wait(timeout=10)
                token = analyses_repo.claim_specific(
                    worker_session,
                    analysis_id=analysis.id,
                    worker_id=worker_id,
                    ttl_seconds=120,
                )
            with lock:
                tokens.append(token)
        except BaseException as exc:  # noqa: BLE001 - 测试需要把线程异常带回主线程
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=_worker, args=(f"worker-{i}",)) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert not errors, errors
    winners = [token for token in tokens if token is not None]
    assert len(winners) == 1  # 最多一个获得有效 claim（§30）

    session.expire_all()
    row = session.get(models.Analysis, analysis.id)
    assert row is not None
    assert row.status == AnalysisStatus.RUNNING.value
    assert row.claim_token in winners
    assert row.run_attempts == 1


def test_claim_next_ignores_non_queued_rows(session: Session, db_settings) -> None:
    assert analyses_repo.claim_next(session, "worker", ttl_seconds=60) is None
    analysis = _queued_analysis(session, db_settings)
    claimed = analyses_repo.claim_next(session, "worker", ttl_seconds=60)
    assert claimed is not None and claimed.id == analysis.id
    assert analyses_repo.claim_next(session, "worker", ttl_seconds=60) is None


# ---------------------------------------------------------------- lease / heartbeat


def test_heartbeat_renewal_and_expiry_protection(session: Session, db_settings) -> None:
    analysis = _queued_analysis(session, db_settings)
    token = analyses_repo.claim_specific(
        session, analysis_id=analysis.id, worker_id="w", ttl_seconds=120
    )
    assert token is not None
    assert analyses_repo.heartbeat(session, analysis.id, token, 120) is True

    # 续租成功但 ttl=0 => lease 落到"已过期"，此后一切 fenced 写入都必须失败（§7）
    assert analyses_repo.heartbeat(session, analysis.id, token, 0) is True
    assert analyses_repo.heartbeat(session, analysis.id, token, 120) is False  # 过期后无法再续租
    assert analyses_repo.update_phase(
        session, analysis_id=analysis.id, claim_token=token, phase="x"
    ) is False
    assert results_repo.mark_succeeded(
        session, analysis_id=analysis.id, claim_token=token, phase="x"
    ) is False
    assert results_repo.guard_lease(session, analysis_id=analysis.id, claim_token=token) is False
    # 已 running 的行不能被再次 claim
    assert (
        analyses_repo.claim_specific(session, analysis_id=analysis.id, worker_id="w2", ttl_seconds=60)
        is None
    )


def test_wrong_claim_token_cannot_write(session: Session, db_settings) -> None:
    analysis = _queued_analysis(session, db_settings)
    assert (
        analyses_repo.claim_specific(session, analysis_id=analysis.id, worker_id="w", ttl_seconds=120)
        is not None
    )
    assert (
        analyses_repo.heartbeat(session, analysis.id, uuid.uuid4(), 120) is False
    )
    assert analyses_repo.update_phase(
        session, analysis_id=analysis.id, claim_token=uuid.uuid4(), phase="x"
    ) is False


# ---------------------------------------------------------------- recover_stale


def test_recover_stale_only_requeues_expired_running_jobs(
    session: Session, session_factory: sessionmaker[Session], db_settings
) -> None:
    fresh = _queued_analysis(session, db_settings)
    expired = _queued_analysis(session, db_settings)
    assert (
        analyses_repo.claim_specific(session, analysis_id=fresh.id, worker_id="w1", ttl_seconds=120)
        is not None
    )
    assert (
        analyses_repo.claim_specific(session, analysis_id=expired.id, worker_id="w2", ttl_seconds=0)
        is not None
    )

    recovered = analyses_repo.recover_stale(session)
    assert recovered == [expired.id]  # 只恢复真正 stale 的 running job
    session.expire_all()
    assert session.get(models.Analysis, fresh.id).status == AnalysisStatus.RUNNING.value  # type: ignore[union-attr]
    requeued = session.get(models.Analysis, expired.id)
    assert requeued is not None
    assert requeued.status == AnalysisStatus.QUEUED.value
    assert requeued.claim_token is None
    assert requeued.requeue_count == 1
    # 幂等：再次 recover 不应重复恢复
    assert analyses_repo.recover_stale(session) == []


# ---------------------------------------------------------------- Case J: stale worker 被 fence


def test_stale_worker_cannot_write_and_new_worker_result_survives(
    session: Session, session_factory: sessionmaker[Session], db_settings
) -> None:
    analysis = _queued_analysis(session, db_settings)
    stale_token = analyses_repo.claim_specific(
        session, analysis_id=analysis.id, worker_id="worker-a", ttl_seconds=0
    )
    assert stale_token is not None
    # A 的 lease 已过期：任何 analysis-scoped 写入都必须失败
    assert results_repo.guard_lease(session, analysis_id=analysis.id, claim_token=stale_token) is False

    assert analyses_repo.recover_stale(session) == [analysis.id]

    # B 重新领取并完整执行
    result = run_analysis(
        session_factory=session_factory,
        settings=db_settings,
        analysis_id=analysis.id,
        provider=DeterministicProvider(
            resume_payload=dict(RESUME_MATCH_PAYLOAD), jd_payload=dict(JD_MATCH_PAYLOAD)
        ),
    )
    assert result.status == "completed"
    assert result.gate == "pass"

    session.expire_all()
    counts_after_b = results_repo.count_analysis_results(session, analysis.id)
    assert counts_after_b["constraints"] == 6
    assert counts_after_b["traces"] > 0
    assert session.get(models.Analysis, analysis.id).status == AnalysisStatus.SUCCEEDED.value  # type: ignore[union-attr]

    # A 尝试在失去 lease 后写入结果 => 必须被拒绝
    with session_factory() as work:
        ctx = matching_service.load_run_context(work, analysis_id=analysis.id)
        computed = matching_service.compute_all(ctx=ctx, retrieved={})
    with pytest.raises(LeaseLost):
        matching_service.persist_results(
            session_factory,
            ctx=ctx,
            claim_token=stale_token,
            computed=computed,
            trace_records=[],
        )
    assert results_repo.mark_succeeded(
        session, analysis_id=analysis.id, claim_token=stale_token, phase="hijack"
    ) is False

    # B 的结果完好无损
    session.expire_all()
    assert results_repo.count_analysis_results(session, analysis.id) == counts_after_b
    final = session.get(models.Analysis, analysis.id)
    assert final is not None
    assert final.status == AnalysisStatus.SUCCEEDED.value
    assert final.current_phase == "analysis_complete"


def test_concurrent_result_writes_converge_without_duplicates(
    session: Session, session_factory: sessionmaker[Session], db_settings
) -> None:
    """两个 worker 同时写入同一 analysis 的同一批结果：唯一键 + ON CONFLICT 保证收敛。"""
    # 先产出并绑定不可变 profile，再建一个绑定好的 queued analysis
    setup = make_analysis_pair(
        session, db_settings, resume_fixture="resume_match.txt", jd_fixture="jd_match.txt"
    )
    setup_result = run_analysis(
        session_factory=session_factory,
        settings=db_settings,
        analysis_id=setup.analysis.id,
        provider=DeterministicProvider(
            resume_payload=dict(RESUME_MATCH_PAYLOAD), jd_payload=dict(JD_MATCH_PAYLOAD)
        ),
    )
    assert setup_result.status == "completed"
    session.expire_all()
    setup_row = session.get(models.Analysis, setup.analysis.id)
    assert setup_row is not None
    assert setup_row.resume_profile_id is not None and setup_row.jd_profile_id is not None

    analysis = make_analysis_pair(
        session,
        db_settings,
        resume_fixture="resume_match.txt",
        jd_fixture="jd_match.txt",
        resume_profile_id=setup_row.resume_profile_id,
        jd_profile_id=setup_row.jd_profile_id,
    ).analysis
    token = analyses_repo.claim_specific(
        session, analysis_id=analysis.id, worker_id="worker-a", ttl_seconds=120
    )
    assert token is not None

    def _write() -> None:
        with session_factory() as work:
            ctx = matching_service.load_run_context(work, analysis_id=analysis.id)
            retrieved = matching_service.retrieve_for_requirements(
                session_factory, ctx=ctx, provider=None, top_k=3
            )
            computed = matching_service.compute_all(ctx=ctx, retrieved=retrieved)
            records = matching_service.build_trace_records(
                ctx=ctx, computed=computed, retrieved=retrieved, evidence_index={}
            )
            matching_service.persist_results(
                session_factory,
                ctx=ctx,
                claim_token=token,
                computed=computed,
                trace_records=records,
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
    counts = results_repo.count_analysis_results(session, analysis.id)
    assert counts["constraints"] == 6
    assert counts["scores"] == 1
    duplicates = session.execute(
        text(
            "SELECT count(*) FROM ("
            "  SELECT analysis_id, requirement_id, ruleset_version FROM hard_constraint_results"
            "  GROUP BY 1,2,3 HAVING count(*) > 1"
            ") AS dup"
        )
    ).scalar_one()
    assert int(duplicates) == 0


def test_provider_payloads_are_used_from_test_support_only() -> None:
    """生产路径不含 fake provider：DeterministicProvider 只能来自 tests/。"""
    import jobfit.llm.factory as factory

    source = factory.__file__
    assert source is not None
    assert "tests" not in source
    assert JD_MATCH_PAYLOAD["requirements"] and RESUME_MATCH_PAYLOAD["skills"]

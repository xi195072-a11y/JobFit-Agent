# mypy: disable-error-code="arg-type,attr-defined,assignment,union-attr,return-value"
"""integration: critique 服务（Phase 4 §16–§21）——真实 PG + DeterministicProvider。

覆盖：validated 路径、fingerprint 幂等、fabricated/cross-analysis citation 拒绝、
UNKNOWN 越权断言拒绝（injection 防线）、无凭证 unavailable、lease-lost fencing、
critique 永不触碰确定性结果表。
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy.orm import Session, sessionmaker

from jobfit.core.enums import AnalysisStatus, CritiqueStatus, ValidationStatus
from jobfit.core.errors import LeaseLost
from jobfit.critique.service import generate_critique
from jobfit.critique.validation import load_validation_store
from jobfit.db import models
from jobfit.db.repositories import analyses as analyses_repo
from jobfit.db.repositories import critiques as critiques_repo
from jobfit.db.repositories import results as results_repo
from support import (
    JD_MATCH_PAYLOAD,
    JD_UNKNOWN_PAYLOAD,
    RESUME_MATCH_PAYLOAD,
    RESUME_MISMATCH_PAYLOAD,
    DeterministicProvider,
    build_valid_critique_payload,
    make_analysis_pair,
    run_analysis,
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


def _succeeded_unknown(
    session: Session, session_factory: sessionmaker[Session], settings
) -> uuid.UUID:
    pair = make_analysis_pair(
        session, settings, resume_fixture="resume_match.txt", jd_fixture="jd_unknown.txt"
    )
    result = run_analysis(
        session_factory=session_factory,
        settings=settings,
        analysis_id=pair.analysis.id,
        provider=DeterministicProvider(
            resume_payload=dict(RESUME_MATCH_PAYLOAD), jd_payload=dict(JD_UNKNOWN_PAYLOAD)
        ),
    )
    assert result.status == "completed"
    return pair.analysis.id


def _claim(
    session: Session, analysis_id: uuid.UUID, *, ttl_seconds: int = 120
) -> uuid.UUID:
    token = analyses_repo.claim_phase4(
        session, analysis_id=analysis_id, worker_id="test-critique", ttl_seconds=ttl_seconds
    )
    assert token is not None
    return token


def _run_generate_critique(
    session_factory: sessionmaker[Session],
    *,
    analysis_id: uuid.UUID,
    token: uuid.UUID,
    provider: DeterministicProvider | None = None,
    max_llm_attempts: int = 10,
):
    return asyncio.run(
        generate_critique(
            session_factory=session_factory,
            analysis_id=analysis_id,
            claim_token=token,
            provider=provider,
            max_llm_attempts=max_llm_attempts,
        )
    )


def test_valid_critique_is_validated(
    session: Session, session_factory: sessionmaker[Session], db_settings
) -> None:
    analysis_id = _succeeded_match(session, session_factory, db_settings)
    token = _claim(session, analysis_id)
    provider = DeterministicProvider(critique_payload=build_valid_critique_payload)

    outcome = _run_generate_critique(
        session_factory, analysis_id=analysis_id, token=token, provider=provider
    )
    assert outcome.critique is not None
    assert outcome.critique.status == CritiqueStatus.OK.value
    assert outcome.critique.validation_status == ValidationStatus.VALIDATED.value
    assert outcome.critique.citations_validated is True
    assert outcome.validation is not None and outcome.validation.valid
    content = outcome.critique.content
    assert content["strengths"]  # 已校验 critique 携带正文
    assert provider.calls.count("critique") == 1


def test_critique_fingerprint_reuse_is_idempotent(
    session: Session, session_factory: sessionmaker[Session], db_settings
) -> None:
    analysis_id = _succeeded_match(session, session_factory, db_settings)
    token = _claim(session, analysis_id)
    provider = DeterministicProvider(critique_payload=build_valid_critique_payload)

    first = _run_generate_critique(
        session_factory, analysis_id=analysis_id, token=token, provider=provider
    )
    second = _run_generate_critique(
        session_factory, analysis_id=analysis_id, token=token, provider=provider
    )
    assert first.critique is not None and second.critique is not None
    assert second.reused is True
    assert second.critique.id == first.critique.id
    assert provider.calls.count("critique") == 1  # 未再调 LLM

    session.expire_all()
    rows = critiques_repo.list_critiques(session, analysis_id)
    assert len(rows) == 1  # UNIQUE(analysis_id, fingerprint) 保证不产生重复行


def test_fabricated_citation_is_rejected(
    session: Session, session_factory: sessionmaker[Session], db_settings
) -> None:
    analysis_id = _succeeded_match(session, session_factory, db_settings)
    token = _claim(session, analysis_id)
    provider = DeterministicProvider(
        critique_payload=lambda prompt: build_valid_critique_payload(
            prompt, fabricated_chunk_id=str(uuid.uuid4())
        )
    )

    outcome = _run_generate_critique(
        session_factory, analysis_id=analysis_id, token=token, provider=provider
    )
    assert outcome.critique is not None
    assert outcome.critique.validation_status == ValidationStatus.REJECTED.value
    assert outcome.critique.citations_validated is False
    assert outcome.validation is not None and not outcome.validation.valid
    assert any("fabricated" in issue or "跨 document" in issue for issue in outcome.validation.issues)


def test_cross_analysis_reference_is_rejected(
    session: Session, session_factory: sessionmaker[Session], db_settings
) -> None:
    # A 用 resume_match；B 用 resume_mismatch（不同 sha256 => 不同 parsed document =>
    # 证据池互斥；否则 sha256 去重会共享同一 resume 文档的 chunk）
    pair_a = make_analysis_pair(
        session, db_settings, resume_fixture="resume_match.txt", jd_fixture="jd_match.txt"
    )
    result_a = run_analysis(
        session_factory=session_factory,
        settings=db_settings,
        analysis_id=pair_a.analysis.id,
        provider=DeterministicProvider(
            resume_payload=dict(RESUME_MATCH_PAYLOAD), jd_payload=dict(JD_MATCH_PAYLOAD)
        ),
    )
    assert result_a.status == "completed"
    analysis_a = pair_a.analysis.id

    pair_b = make_analysis_pair(
        session, db_settings, resume_fixture="resume_mismatch.txt", jd_fixture="jd_match.txt"
    )
    result_b = run_analysis(
        session_factory=session_factory,
        settings=db_settings,
        analysis_id=pair_b.analysis.id,
        provider=DeterministicProvider(
            resume_payload=dict(RESUME_MISMATCH_PAYLOAD), jd_payload=dict(JD_MATCH_PAYLOAD)
        ),
    )
    assert result_b.status == "completed"
    analysis_b = pair_b.analysis.id

    # 取 analysis B 自己的 chunk id（属于 B 的证据池，不属于 A）
    session.expire_all()  # run_analysis 在其他会话完成，需刷新 fixture session 的 analysis 绑定
    store_b = load_validation_store(session, analysis_id=analysis_b)
    assert store_b.chunks
    foreign_chunk = next(iter(store_b.chunks))

    token = _claim(session, analysis_a)
    provider = DeterministicProvider(
        critique_payload=lambda prompt: build_valid_critique_payload(
            prompt, fabricated_chunk_id=foreign_chunk
        )
    )
    outcome = _run_generate_critique(
        session_factory, analysis_id=analysis_a, token=token, provider=provider
    )
    assert outcome.critique is not None
    assert outcome.critique.validation_status == ValidationStatus.REJECTED.value
    assert any("不在当前 analysis 证据池" in issue for issue in outcome.validation.issues)


def test_supported_assertion_on_unknown_is_rejected(
    session: Session, session_factory: sessionmaker[Session], db_settings
) -> None:
    """Injection/越权防线：UNKNOWN 必须保留，LLM 不得以 SUPPORTED 断言确定结论（§14）。"""
    analysis_id = _succeeded_unknown(session, session_factory, db_settings)
    token = _claim(session, analysis_id)
    provider = DeterministicProvider(
        critique_payload=lambda prompt: build_valid_critique_payload(prompt, unknown_claim=True)
    )

    outcome = _run_generate_critique(
        session_factory, analysis_id=analysis_id, token=token, provider=provider
    )
    assert outcome.critique is not None
    assert outcome.critique.validation_status == ValidationStatus.REJECTED.value
    assert any("UNKNOWN" in issue and "SUPPORTED" in issue for issue in outcome.validation.issues)


def test_no_provider_records_unavailable_without_faking(
    session: Session, session_factory: sessionmaker[Session], db_settings
) -> None:
    analysis_id = _succeeded_match(session, session_factory, db_settings)
    token = _claim(session, analysis_id)

    outcome = _run_generate_critique(session_factory, analysis_id=analysis_id, token=token, provider=None)
    assert outcome.unavailable is True
    assert outcome.critique is not None
    assert outcome.critique.status == CritiqueStatus.UNAVAILABLE.value
    assert outcome.critique.validation_status == ValidationStatus.PENDING.value
    assert outcome.critique.citations_validated is False
    assert outcome.critique.content.get("reason") == "external_credential_blocked"


def test_critique_never_mutates_deterministic_results(
    session: Session, session_factory: sessionmaker[Session], db_settings
) -> None:
    """Critique 是只读分析层：即使 LLM 输出越权 claim，确定性结果表也必须原封不动。"""
    analysis_id = _succeeded_match(session, session_factory, db_settings)
    before = results_repo.count_analysis_results(session, analysis_id)
    token = _claim(session, analysis_id)
    provider = DeterministicProvider(
        critique_payload=lambda prompt: build_valid_critique_payload(
            prompt, fabricated_chunk_id=str(uuid.uuid4()), unknown_claim=True
        )
    )
    outcome = _run_generate_critique(
        session_factory, analysis_id=analysis_id, token=token, provider=provider
    )
    assert outcome.critique is not None
    assert outcome.critique.validation_status == ValidationStatus.REJECTED.value

    session.expire_all()
    after = results_repo.count_analysis_results(session, analysis_id)
    assert after == before
    store = load_validation_store(session, analysis_id=analysis_id)
    assert store.requirement_ids  # 证据/决策链完好


def test_lease_lost_critique_is_dropped(
    session: Session, session_factory: sessionmaker[Session], db_settings
) -> None:
    """过期 lease 的 stale worker 不得写 critique（fencing，§16）。"""
    analysis_id = _succeeded_match(session, session_factory, db_settings)
    token = _claim(session, analysis_id, ttl_seconds=120)
    # 显式耗尽 lease
    assert analyses_repo.heartbeat(session, analysis_id, token, 0) is True
    assert analyses_repo.heartbeat(session, analysis_id, token, 120) is False

    provider = DeterministicProvider(critique_payload=build_valid_critique_payload)
    with pytest.raises(LeaseLost):
        _run_generate_critique(
            session_factory, analysis_id=analysis_id, token=token, provider=provider
        )
    session.expire_all()
    assert critiques_repo.get_latest_critique(session, analysis_id) is None
    # 状态保持 running（等待 recover_stale 重排），不被污染
    row = session.get(models.Analysis, analysis_id)
    assert row is not None
    assert row.status == AnalysisStatus.RUNNING.value

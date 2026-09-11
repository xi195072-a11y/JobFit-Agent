# mypy: disable-error-code="arg-type,attr-defined,assignment,union-attr"
"""integration: Phase 3 端到端确定性分析（§33 Cases A–H / §38）。

真实 PostgreSQL，不 mock DB、不 mock 向量检索。
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from jobfit.core.enums import AnalysisStatus, SkillMatchStatus, Verdict
from jobfit.db import models
from jobfit.db.repositories import results as results_repo
from support import (
    JD_MATCH_PAYLOAD,
    JD_MISMATCH_PAYLOAD,
    JD_UNKNOWN_PAYLOAD,
    RESUME_MATCH_PAYLOAD,
    RESUME_MISMATCH_PAYLOAD,
    DeterministicProvider,
    make_analysis_pair,
    run_analysis,
)

pytestmark = pytest.mark.db


def _run(
    session: Session,
    session_factory: sessionmaker[Session],
    settings,
    *,
    resume_fixture: str,
    jd_fixture: str,
    resume_payload: dict,
    jd_payload: dict,
    ttl_seconds: int | None = None,
):
    pair = make_analysis_pair(
        session, settings, resume_fixture=resume_fixture, jd_fixture=jd_fixture
    )
    provider = DeterministicProvider(
        resume_payload=dict(resume_payload), jd_payload=dict(jd_payload)
    )
    result = run_analysis(
        session_factory=session_factory,
        settings=settings,
        analysis_id=pair.analysis.id,
        provider=provider,
        ttl_seconds=ttl_seconds,
    )
    return pair, result, provider


def _verdicts(session: Session, analysis_id: uuid.UUID) -> dict[str, str]:
    rows = session.execute(
        select(models.HardConstraintResult.constraint_type, models.HardConstraintResult.result).where(
            models.HardConstraintResult.analysis_id == analysis_id
        )
    ).all()
    out: dict[str, list[str]] = {}
    for constraint_type, result in rows:
        out.setdefault(constraint_type, []).append(result)
    return {key: ",".join(sorted(value)) for key, value in out.items()}


def _skill_status(session: Session, analysis_id: uuid.UUID) -> dict[str, str]:
    rows = session.execute(
        select(models.SkillMatchResult.norm_used, models.SkillMatchResult.status).where(
            models.SkillMatchResult.analysis_id == analysis_id
        )
    ).all()
    return {str(norm): status for norm, status in rows}


def _profile_bindings(
    session: Session, session_factory: sessionmaker[Session], settings
) -> tuple[uuid.UUID, uuid.UUID]:
    """先跑一次 MATCH 分析产出（并绑定）不可变 profile，返回其 id 供后续显式绑定。"""
    pair, result, _provider = _run(
        session,
        session_factory,
        settings,
        resume_fixture="resume_match.txt",
        jd_fixture="jd_match.txt",
        resume_payload=RESUME_MATCH_PAYLOAD,
        jd_payload=JD_MATCH_PAYLOAD,
    )
    assert result.status == "completed"
    session.expire_all()
    analysis = session.get(models.Analysis, pair.analysis.id)
    assert analysis is not None
    assert analysis.resume_profile_id is not None and analysis.jd_profile_id is not None
    return uuid.UUID(str(analysis.resume_profile_id)), uuid.UUID(str(analysis.jd_profile_id))


# ---------------------------------------------------------------- Case A/D/E/F/G: 全匹配


def test_case_a_match_all_hard_constraints_met(session: Session, session_factory, db_settings) -> None:
    pair, result, _provider = _run(
        session,
        session_factory,
        db_settings,
        resume_fixture="resume_match.txt",
        jd_fixture="jd_match.txt",
        resume_payload=RESUME_MATCH_PAYLOAD,
        jd_payload=JD_MATCH_PAYLOAD,
    )
    assert result.status == "completed"
    assert result.gate == "pass"
    assert result.score_total == 100.0

    verdicts = _verdicts(session, pair.analysis.id)
    # Case D（多 requirement）与 Case E/F/G（学历/年限/地点/语言）全覆盖
    assert verdicts["degree"] == Verdict.MET.value
    assert verdicts["years_experience"] == Verdict.MET.value
    assert verdicts["location"] == Verdict.MET.value
    assert verdicts["language"] == Verdict.MET.value
    assert verdicts["skill"] == f"{Verdict.MET.value},{Verdict.MET.value}"

    skills = _skill_status(session, pair.analysis.id)
    assert skills == {"postgresql": SkillMatchStatus.MATCHED.value, "python": SkillMatchStatus.MATCHED.value}

    analysis = session.get(models.Analysis, pair.analysis.id)
    assert analysis is not None
    session.refresh(analysis)
    assert analysis.status == AnalysisStatus.SUCCEEDED.value
    assert analysis.current_phase == "analysis_complete"
    assert analysis.resume_profile_id is not None and analysis.jd_profile_id is not None


# ---------------------------------------------------------------- Case B: 明确不匹配


def test_case_b_clear_mismatch_blocks_and_never_marks_absence_as_false(
    session: Session, session_factory, db_settings
) -> None:
    pair, result, _provider = _run(
        session,
        session_factory,
        db_settings,
        resume_fixture="resume_mismatch.txt",
        jd_fixture="jd_mismatch.txt",
        resume_payload=RESUME_MISMATCH_PAYLOAD,
        jd_payload=JD_MISMATCH_PAYLOAD,
    )
    assert result.status == "completed"
    assert result.gate == "blocked"
    assert result.score_total == 0.0

    verdicts = _verdicts(session, pair.analysis.id)
    assert verdicts["degree"] == Verdict.NOT_MET.value  # 大专 < 硕士（权威标量事实）
    assert verdicts["years_experience"] == Verdict.NOT_MET.value  # 1 年 < 5 年
    assert verdicts["location"] == Verdict.NOT_MET.value  # 北京 ≠ 上海
    # 技能：简历没有 PyTorch，但"没有证据"只能是 UNKNOWN，绝不能是 NOT_MET
    assert verdicts["skill"] == Verdict.UNKNOWN.value
    assert _skill_status(session, pair.analysis.id)["pytorch"] == SkillMatchStatus.UNKNOWN.value


# ---------------------------------------------------------------- Case C: UNKNOWN 证据


def test_case_c_unknown_evidence_and_partial_retrieval(
    session: Session, session_factory, db_settings
) -> None:
    pair, result, _provider = _run(
        session,
        session_factory,
        db_settings,
        resume_fixture="resume_mismatch.txt",
        jd_fixture="jd_unknown.txt",
        resume_payload=RESUME_MISMATCH_PAYLOAD,
        jd_payload=JD_UNKNOWN_PAYLOAD,
    )
    assert result.status == "completed"
    assert result.gate == "unknown"
    assert "HARD_CONSTRAINT_UNKNOWN" in result.flags

    verdicts = _verdicts(session, pair.analysis.id)
    # 未建模类别（工作签证）=> UNKNOWN + architecture gap，而不是猜
    assert verdicts["other"] == Verdict.UNKNOWN.value
    # Kubernetes 既无结构化技能也无检索证据 => UNKNOWN
    assert verdicts["skill"] == f"{Verdict.UNKNOWN.value},{Verdict.UNKNOWN.value}"

    skills = _skill_status(session, pair.analysis.id)
    assert skills["kubernetes"] == SkillMatchStatus.UNKNOWN.value
    # Redis 只出现在经历文本（结构化技能缺失）=> 检索证据 => PARTIAL
    assert skills["redis"] == SkillMatchStatus.PARTIAL.value

    reasons: dict[str, str] = dict(
        session.execute(
            select(models.HardConstraintResult.constraint_type, models.HardConstraintResult.reason_code).where(
                models.HardConstraintResult.analysis_id == pair.analysis.id
            )
        ).all()
    )
    assert reasons["other"] == "UNSUPPORTED_REQUIREMENT_TYPE"


# ---------------------------------------------------------------- Case H: rerun 幂等


def test_case_h_rerun_is_idempotent_and_creates_no_duplicates(
    session: Session, session_factory, db_settings
) -> None:
    pair, result, _provider = _run(
        session,
        session_factory,
        db_settings,
        resume_fixture="resume_match.txt",
        jd_fixture="jd_match.txt",
        resume_payload=RESUME_MATCH_PAYLOAD,
        jd_payload=JD_MATCH_PAYLOAD,
    )
    assert result.status == "completed"
    before = results_repo.count_analysis_results(session, pair.analysis.id)
    assert before["constraints"] == 6
    assert before["traces"] > 0

    # 再次执行同一个 analysis（先显式重排；结果写入是幂等 upsert）
    assert results_repo.requeue_analysis(session, pair.analysis.id) is True
    second = run_analysis(
        session_factory=session_factory, settings=db_settings, analysis_id=pair.analysis.id
    )
    assert second.status == "completed"

    after = results_repo.count_analysis_results(session, pair.analysis.id)
    assert after == before  # 无重复行
    analysis = session.get(models.Analysis, pair.analysis.id)
    assert analysis is not None
    session.refresh(analysis)
    assert analysis.status == AnalysisStatus.SUCCEEDED.value
    # 复跑没有产生新的 artifact（profile 五元组命中复用）
    resume_profiles = session.execute(select(func.count()).select_from(models.ResumeProfile)).scalar_one()
    assert int(resume_profiles) == 1


def test_rerun_of_succeeded_analysis_without_requeue_is_not_claimable(
    session: Session, session_factory, db_settings
) -> None:
    pair, result, _provider = _run(
        session,
        session_factory,
        db_settings,
        resume_fixture="resume_match.txt",
        jd_fixture="jd_match.txt",
        resume_payload=RESUME_MATCH_PAYLOAD,
        jd_payload=JD_MATCH_PAYLOAD,
    )
    assert result.status == "completed"
    again = run_analysis(
        session_factory=session_factory, settings=db_settings, analysis_id=pair.analysis.id
    )
    assert again.status == "not_claimable"
    assert again.constraint_count == 0  # 没有产生任何副作用


def test_unbound_analysis_cannot_be_analyzed_without_bindings(
    session: Session, session_factory, db_settings
) -> None:
    """§5：bindings 不完整时必须显式失败，绝不运行时"猜 profile"。"""
    from jobfit.core.errors import ValidationFailed
    from jobfit.matching.service import load_run_context

    pair = make_analysis_pair(
        session, db_settings, resume_fixture="resume_match.txt", jd_fixture="jd_match.txt"
    )
    # 先 claim（走 fencing），但 profiles 尚未绑定
    from jobfit.db.repositories import analyses as analyses_repo

    token = analyses_repo.claim_specific(
        session, analysis_id=pair.analysis.id, worker_id="probe", ttl_seconds=120
    )
    assert token is not None
    with pytest.raises(ValidationFailed):
        load_run_context(session, analysis_id=pair.analysis.id)


# ---------------------------------------------------------------- analysis 身份幂等


def test_second_analysis_over_same_artifacts_reuses_profiles(
    session: Session, session_factory, db_settings
) -> None:
    pair, result, provider = _run(
        session,
        session_factory,
        db_settings,
        resume_fixture="resume_match.txt",
        jd_fixture="jd_match.txt",
        resume_payload=RESUME_MATCH_PAYLOAD,
        jd_payload=JD_MATCH_PAYLOAD,
    )
    assert result.status == "completed"
    assert provider.call_count == 2  # 一次 resume + 一次 jd

    second_pair = make_analysis_pair(
        session, db_settings, resume_fixture="resume_match.txt", jd_fixture="jd_match.txt"
    )
    second_provider = DeterministicProvider(
        resume_payload=dict(RESUME_MATCH_PAYLOAD), jd_payload=dict(JD_MATCH_PAYLOAD)
    )
    second = asyncio.run(
        __import__("jobfit.workflow.runner", fromlist=["run_analysis_pipeline"]).run_analysis_pipeline(
            session_factory=session_factory,
            settings=db_settings,
            provider=second_provider,
            embedding_provider=None,
            analysis_id=second_pair.analysis.id,
        )
    )
    assert second.status == "completed"
    assert second_provider.calls == []  # 复用不可变 artifact => 不再调用 LLM

    session.expire_all()
    assert session.execute(select(func.count()).select_from(models.ResumeProfile)).scalar_one() == 1
    assert session.execute(select(func.count()).select_from(models.JDProfile)).scalar_one() == 1
    # 两个 analysis 绑定同一 profile
    first = session.get(models.Analysis, pair.analysis.id)
    other = session.get(models.Analysis, second_pair.analysis.id)
    assert first is not None and other is not None
    assert first.resume_profile_id == other.resume_profile_id
    assert first.jd_profile_id == other.jd_profile_id


def test_persist_is_idempotent_when_replayed_with_same_claim(
    session: Session, session_factory, db_settings
) -> None:
    """同一 claim 下重复 persist（at-least-once 重试）不产生重复行、不改变总数。"""
    from jobfit.db.repositories import analyses as analyses_repo
    from jobfit.matching import service as matching_service

    resume_profile_id, jd_profile_id = _profile_bindings(session, session_factory, db_settings)
    pair = make_analysis_pair(
        session,
        db_settings,
        resume_fixture="resume_match.txt",
        jd_fixture="jd_match.txt",
        resume_profile_id=resume_profile_id,
        jd_profile_id=jd_profile_id,
    )
    provider = DeterministicProvider(
        resume_payload=dict(RESUME_MATCH_PAYLOAD), jd_payload=dict(JD_MATCH_PAYLOAD)
    )
    token = analyses_repo.claim_specific(
        session, analysis_id=pair.analysis.id, worker_id="probe", ttl_seconds=120
    )
    assert token is not None
    with session_factory() as work:
        ctx = matching_service.load_run_context(work, analysis_id=pair.analysis.id)
        retrieved = matching_service.retrieve_for_requirements(
            session_factory, ctx=ctx, provider=None, top_k=5
        )
        computed = matching_service.compute_all(ctx=ctx, retrieved=retrieved)
        assert computed.score.gate == "pass"
        records = matching_service.build_trace_records(
            ctx=ctx, computed=computed, retrieved=retrieved, evidence_index={}
        )
        first = matching_service.persist_results(
            session_factory, ctx=ctx, claim_token=token, computed=computed, trace_records=records
        )
        second = matching_service.persist_results(
            session_factory, ctx=ctx, claim_token=token, computed=computed, trace_records=records
        )
    assert first.trace_count == second.trace_count
    assert provider.call_count == 0  # 该路径没有调用 LLM（profile 已绑定）
    counts = results_repo.count_analysis_results(session, pair.analysis.id)
    assert counts["traces"] == first.trace_count
    assert counts["scores"] == 1


def test_persist_rejects_write_when_lease_lost(
    session: Session, session_factory, db_settings
) -> None:
    """stale worker 的 analysis-scoped 写入必须被 fencing 拒绝并抛 LeaseLost（§7）。"""
    from jobfit.core.errors import LeaseLost
    from jobfit.db.repositories import analyses as analyses_repo
    from jobfit.matching import service as matching_service

    resume_profile_id, jd_profile_id = _profile_bindings(session, session_factory, db_settings)
    pair = make_analysis_pair(
        session,
        db_settings,
        resume_fixture="resume_match.txt",
        jd_fixture="jd_match.txt",
        resume_profile_id=resume_profile_id,
        jd_profile_id=jd_profile_id,
    )
    # ttl=0 => lease 立即过期
    token = analyses_repo.claim_specific(
        session, analysis_id=pair.analysis.id, worker_id="stale", ttl_seconds=0
    )
    assert token is not None
    with session_factory() as work:
        ctx = matching_service.load_run_context(work, analysis_id=pair.analysis.id)
        computed = matching_service.compute_all(ctx=ctx, retrieved={})
        with pytest.raises(LeaseLost):
            matching_service.persist_results(
                session_factory, ctx=ctx, claim_token=token, computed=computed, trace_records=[]
            )
    counts = results_repo.count_analysis_results(session, pair.analysis.id)
    assert counts == {"constraints": 0, "skill_matches": 0, "traces": 0, "scores": 0}


def test_analysis_does_not_leak_results_across_analyses(
    session: Session, session_factory, db_settings
) -> None:
    first_pair, first_result, _ = _run(
        session,
        session_factory,
        db_settings,
        resume_fixture="resume_match.txt",
        jd_fixture="jd_match.txt",
        resume_payload=RESUME_MATCH_PAYLOAD,
        jd_payload=JD_MATCH_PAYLOAD,
    )
    second_pair = make_analysis_pair(
        session, db_settings, resume_fixture="resume_mismatch.txt", jd_fixture="jd_mismatch.txt"
    )
    second_result = run_analysis(
        session_factory=session_factory,
        settings=db_settings,
        analysis_id=second_pair.analysis.id,
        provider=DeterministicProvider(
            resume_payload=dict(RESUME_MISMATCH_PAYLOAD), jd_payload=dict(JD_MISMATCH_PAYLOAD)
        ),
    )
    assert first_result.gate == "pass"
    assert second_result.gate == "blocked"
    assert _verdicts(session, first_pair.analysis.id)["degree"] == Verdict.MET.value
    assert _verdicts(session, second_pair.analysis.id)["degree"] == Verdict.NOT_MET.value

    # 结果行只属于自己的 analysis
    mismatched = session.execute(
        text(
            "SELECT count(*) FROM hard_constraint_results WHERE analysis_id = :a"
        ),
        {"a": second_pair.analysis.id},
    ).scalar_one()
    assert int(mismatched) == 4

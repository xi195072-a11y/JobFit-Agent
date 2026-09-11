# mypy: disable-error-code="arg-type,attr-defined,assignment,union-attr"
"""integration: Phase 2 profile identity 回归（Phase 3 §4 Scenario A/B）。

背景（ADR-026 第 3 条）：profile 的复用 identity 是五元组，而 fingerprint 额外包含
`parsed_document_id`。因此"parser 变了但 pipeline_version 没变"必须在**运行期被检测并拒绝**，
不能依赖开发人员记忆。
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from jobfit.core.enums import AnalysisStatus
from jobfit.db import models
from support import (
    JD_MATCH_PAYLOAD,
    RESUME_MATCH_PAYLOAD,
    DeterministicProvider,
    make_analysis_pair,
    run_analysis,
)

pytestmark = pytest.mark.db


def _provider() -> DeterministicProvider:
    return DeterministicProvider(
        resume_payload=dict(RESUME_MATCH_PAYLOAD), jd_payload=dict(JD_MATCH_PAYLOAD)
    )


def _count(session: Session, model) -> int:
    return int(session.execute(select(func.count()).select_from(model)).scalar_one())


# ---------------------------------------------------------------- Scenario A


def test_scenario_a_identical_identity_reuses_artifact(
    session: Session, session_factory: sessionmaker[Session], db_settings
) -> None:
    first_pair = make_analysis_pair(
        session, db_settings, resume_fixture="resume_match.txt", jd_fixture="jd_match.txt"
    )
    first = run_analysis(
        session_factory=session_factory,
        settings=db_settings,
        analysis_id=first_pair.analysis.id,
        provider=_provider(),
    )
    assert first.status == "completed"

    session.expire_all()
    first_row = session.get(models.Analysis, first_pair.analysis.id)
    assert first_row is not None
    first_fingerprint = session.execute(
        select(models.ResumeProfile.full_dump).where(
            models.ResumeProfile.id == first_row.resume_profile_id
        )
    ).scalar_one()["_meta"]["fingerprint"]

    # 完全相同的 identity（同 document / pipeline / schema / prompt / model / parse artifact）
    second_pair = make_analysis_pair(
        session, db_settings, resume_fixture="resume_match.txt", jd_fixture="jd_match.txt"
    )
    provider = _provider()
    second = run_analysis(
        session_factory=session_factory,
        settings=db_settings,
        analysis_id=second_pair.analysis.id,
        provider=provider,
    )
    assert second.status == "completed"
    assert provider.calls == []  # 复用 => 不调用 LLM
    assert _count(session, models.ResumeProfile) == 1
    assert _count(session, models.JDProfile) == 1
    assert _count(session, models.ParsedDocument) == 2  # 简历 1 个 + JD 1 个

    # 绑定的仍是同一 profile（显式绑定，不重新猜）
    session.expire_all()
    second_row = session.get(models.Analysis, second_pair.analysis.id)
    assert second_row is not None
    assert second_row.resume_profile_id == first_row.resume_profile_id
    assert second_row.jd_profile_id == first_row.jd_profile_id
    stored = session.execute(
        select(models.ResumeProfile.full_dump).where(
            models.ResumeProfile.id == second_row.resume_profile_id
        )
    ).scalar_one()
    assert stored["_meta"]["fingerprint"] == first_fingerprint


# ---------------------------------------------------------------- Scenario B


def test_scenario_b_parser_change_without_pipeline_bump_fails_loudly(
    session: Session, session_factory: sessionmaker[Session], db_settings, monkeypatch
) -> None:
    """parser/解析库版本变化但 PIPELINE_VERSION 未变 => 显式失败，绝不静默复用旧解析产物。"""
    baseline_pair = make_analysis_pair(
        session, db_settings, resume_fixture="resume_match.txt", jd_fixture="jd_match.txt"
    )
    baseline = run_analysis(
        session_factory=session_factory,
        settings=db_settings,
        analysis_id=baseline_pair.analysis.id,
        provider=_provider(),
    )
    assert baseline.status == "completed"

    session.expire_all()
    baseline_row = session.get(models.Analysis, baseline_pair.analysis.id)
    assert baseline_row is not None
    old_dump = dict(
        session.execute(
            select(models.ResumeProfile.full_dump).where(
                models.ResumeProfile.id == baseline_row.resume_profile_id
            )
        ).scalar_one()
    )
    profiles_before = _count(session, models.ResumeProfile)
    parse_artifacts_before = _count(session, models.ParsedDocument)

    # 模拟"升级了解析器/解析库，但没有 bump PIPELINE_VERSION"
    import jobfit.parsing.service as parsing_service

    monkeypatch.setattr(
        parsing_service, "parse_version", lambda: "p2.1.0+c1.0.0+size1200+ovl200+upgraded"
    )

    second_pair = make_analysis_pair(
        session, db_settings, resume_fixture="resume_match.txt", jd_fixture="jd_match.txt"
    )
    result = run_analysis(
        session_factory=session_factory,
        settings=db_settings,
        analysis_id=second_pair.analysis.id,
        provider=_provider(),
    )

    assert result.status == "failed"
    joined = " ".join(result.errors)
    assert "PIPELINE_VERSION" in joined
    assert "fingerprint" in joined

    session.expire_all()
    # 不产生新的 profile artifact（旧行不被覆盖、也不新增错误复用的行）
    assert _count(session, models.ResumeProfile) == profiles_before
    # 新的 parse artifact 会按版本正常创建（不可变、无害），但不会被用来产出旧 identity 的 profile
    assert _count(session, models.ParsedDocument) == parse_artifacts_before + 2
    # 旧 artifact 内容一字未改
    still_old = session.execute(
        select(models.ResumeProfile.full_dump).where(models.ResumeProfile.id == baseline_row.resume_profile_id)
    ).scalar_one()
    assert still_old == old_dump
    # analysis 进入 fenced 失败终态，且没有写入任何分析结果
    failed_row = session.get(models.Analysis, second_pair.analysis.id)
    assert failed_row is not None
    assert failed_row.status == AnalysisStatus.FAILED.value
    assert str(failed_row.current_phase).startswith("failed:")
    assert (
        session.execute(
            select(func.count())
            .select_from(models.HardConstraintResult)
            .where(models.HardConstraintResult.analysis_id == second_pair.analysis.id)
        ).scalar_one()
        == 0
    )


def test_scenario_b_is_fixed_by_bumping_pipeline_version(
    session: Session, session_factory: sessionmaker[Session], db_settings, monkeypatch
) -> None:
    """同一场景下，只要 bump PIPELINE_VERSION（身份变化）就能正常产出新 artifact。"""
    baseline_pair = make_analysis_pair(
        session, db_settings, resume_fixture="resume_match.txt", jd_fixture="jd_match.txt"
    )
    assert (
        run_analysis(
            session_factory=session_factory,
            settings=db_settings,
            analysis_id=baseline_pair.analysis.id,
            provider=_provider(),
        ).status
        == "completed"
    )

    import jobfit.parsing.service as parsing_service

    monkeypatch.setattr(
        parsing_service, "parse_version", lambda: "p2.1.0+c1.0.0+size1200+ovl200+upgraded"
    )
    bumped_pair = make_analysis_pair(
        session,
        db_settings,
        resume_fixture="resume_match.txt",
        jd_fixture="jd_match.txt",
        pipeline_version="test-pipeline-2",
    )
    result = run_analysis(
        session_factory=session_factory,
        settings=db_settings,
        analysis_id=bumped_pair.analysis.id,
        provider=_provider(),
    )
    assert result.status == "completed"
    assert result.gate == "pass"

    session.expire_all()
    assert _count(session, models.ResumeProfile) == 2  # 新旧两个版本并存（不可变）
    new_row = session.get(models.Analysis, bumped_pair.analysis.id)
    assert new_row is not None
    new_dump = session.execute(
        select(models.ResumeProfile.full_dump).where(
            models.ResumeProfile.id == new_row.resume_profile_id
        )
    ).scalar_one()
    assert new_dump["_meta"]["parser_version"] == "p2.1.0+c1.0.0+size1200+ovl200+upgraded"


def test_fingerprint_is_never_a_random_uuid(
    session: Session, session_factory: sessionmaker[Session], db_settings
) -> None:
    pair = make_analysis_pair(
        session, db_settings, resume_fixture="resume_match.txt", jd_fixture="jd_match.txt"
    )
    assert (
        run_analysis(
            session_factory=session_factory,
            settings=db_settings,
            analysis_id=pair.analysis.id,
            provider=_provider(),
        ).status
        == "completed"
    )
    session.expire_all()
    row = session.get(models.Analysis, pair.analysis.id)
    assert row is not None
    dump = session.execute(
        select(models.ResumeProfile.full_dump).where(models.ResumeProfile.id == row.resume_profile_id)
    ).scalar_one()
    fingerprint_value = dump["_meta"]["fingerprint"]
    assert len(fingerprint_value) == 64
    int(fingerprint_value, 16)  # 合法 hex（SHA-256），不是随机 UUID
    assert dump["_meta"]["prompt_version"].startswith("h:")
    assert dump["_meta"]["parser_version"].startswith("p2.")
    # artifact 自描述来源：parsed_document_id 指向真实存在的 parse artifact
    parsed_document_id = session.execute(
        select(models.ResumeProfile.parsed_document_id).where(
            models.ResumeProfile.id == row.resume_profile_id
        )
    ).scalar_one()
    assert session.get(models.ParsedDocument, parsed_document_id) is not None

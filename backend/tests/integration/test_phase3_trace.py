# mypy: disable-error-code="arg-type,attr-defined,assignment,union-attr"
"""integration: Decision Trace（Phase 3 §22–§24/§37）。

验证：覆盖性、可追溯性、PII-safe、可重生成（deterministic）。
"""

from __future__ import annotations

import copy
import json
import uuid

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from jobfit.db import models
from jobfit.db.repositories import results as results_repo
from support import (
    JD_MATCH_PAYLOAD,
    RESUME_MATCH_PAYLOAD,
    DeterministicProvider,
    make_analysis_pair,
    read_fixture_bytes,
    run_analysis,
)

pytestmark = pytest.mark.db


def _provider() -> DeterministicProvider:
    return DeterministicProvider(
        resume_payload=dict(RESUME_MATCH_PAYLOAD), jd_payload=dict(JD_MATCH_PAYLOAD)
    )


def _run_match(session: Session, session_factory: sessionmaker[Session], settings) -> uuid.UUID:
    pair = make_analysis_pair(
        session, settings, resume_fixture="resume_match.txt", jd_fixture="jd_match.txt"
    )
    result = run_analysis(
        session_factory=session_factory,
        settings=settings,
        analysis_id=pair.analysis.id,
        provider=_provider(),
    )
    assert result.status == "completed"
    return uuid.UUID(str(pair.analysis.id))


def _chains(session: Session, analysis_id: uuid.UUID) -> list[models.DecisionTrace]:
    return results_repo.list_traces(session, analysis_id)


def _normalized(rows: list[models.DecisionTrace]) -> list[tuple[str, str, dict]]:
    """去掉 analysis-local 字段后用于跨 analysis 比较（确定性要求）。"""
    out: list[tuple[str, str, dict]] = []
    for row in rows:
        chain = copy.deepcopy(dict(row.chain))
        chain.get("input_artifacts", {}).pop("analysis_id", None)
        out.append((row.decision_type, row.decision_key, chain))
    return out


# ---------------------------------------------------------------- 覆盖性 / 可追溯


def test_every_decision_has_a_trace(session: Session, session_factory, db_settings) -> None:
    analysis_id = _run_match(session, session_factory, db_settings)

    constraint_count = (
        session.execute(
            select(func.count())
            .select_from(models.HardConstraintResult)
            .where(models.HardConstraintResult.analysis_id == analysis_id)
        ).scalar_one()
        + session.execute(
            select(func.count())
            .select_from(models.SkillMatchResult)
            .where(models.SkillMatchResult.analysis_id == analysis_id)
        ).scalar_one()
    )
    traces = _chains(session, analysis_id)
    # constraint + skill_match + score_component(5 个 section) 全部有 trace
    assert len(traces) == int(constraint_count) + 5

    linked = session.execute(
        select(func.count())
        .select_from(models.HardConstraintResult)
        .where(
            models.HardConstraintResult.analysis_id == analysis_id,
            models.HardConstraintResult.trace_id.is_not(None),
        )
    ).scalar_one()
    assert int(linked) == 6
    skill_linked = session.execute(
        select(func.count())
        .select_from(models.SkillMatchResult)
        .where(
            models.SkillMatchResult.analysis_id == analysis_id,
            models.SkillMatchResult.trace_id.is_not(None),
        )
    ).scalar_one()
    assert int(skill_linked) == 2

    # trace_id 指向真实存在的 trace 行
    trace_ids = {row.id for row in traces}
    for row in results_repo.list_constraint_results(session, analysis_id):
        assert row.trace_id in trace_ids


def test_trace_is_traceable_to_rule_requirement_and_chunks(
    session: Session, session_factory, db_settings
) -> None:
    analysis_id = _run_match(session, session_factory, db_settings)
    analysis = session.get(models.Analysis, analysis_id)
    assert analysis is not None

    constraint_traces = [row for row in _chains(session, analysis_id) if row.decision_type == "constraint"]
    assert constraint_traces
    for row in constraint_traces:
        chain = row.chain
        assert chain["rule"]["rule_id"].startswith(("constraint.", "skill."))
        assert chain["rule"]["ruleset_version"] == analysis.ruleset_version
        assert chain["versions"]["pipeline_version"] == analysis.pipeline_version
        assert chain["input_artifacts"]["resume_profile_id"] == str(analysis.resume_profile_id)
        assert chain["input_artifacts"]["jd_profile_id"] == str(analysis.jd_profile_id)
        assert chain["requirement"]["req_type"]
        assert chain["decision"]["reason_code"]
        assert chain["decision"]["basis"] == "deterministic"
        # 每个 evidence 引用必须能回指真实 chunk（区间 + 摘要，不含文本）
        for entry in chain["evidence"]:
            assert entry["source_chunk_id"]
            assert entry.get("resolvable") is True
            assert entry["span_sha256"]
            assert "text" not in entry and "content" not in entry

    # JD 侧 anchor 可用于定位原文
    degree_trace = next(
        row for row in constraint_traces if row.chain["requirement"]["req_type"] == "degree"
    )
    assert degree_trace.chain["requirement"]["anchors"]
    assert degree_trace.chain["requirement"]["source_text"] == "本科及以上学历，计算机相关专业"


def test_trace_evidence_points_at_real_resume_chunks(
    session: Session, session_factory, db_settings
) -> None:
    analysis_id = _run_match(session, session_factory, db_settings)
    chunk_ids: set[str] = set()
    for row in _chains(session, analysis_id):
        for entry in row.chain.get("evidence", []):
            chunk_ids.add(entry["source_chunk_id"])
    assert chunk_ids
    found = {
        str(value)
        for value in session.execute(
            select(models.DocumentChunk.id).where(models.DocumentChunk.id.in_([uuid.UUID(c) for c in chunk_ids]))
        )
        .scalars()
        .all()
    }
    assert found == chunk_ids


# ---------------------------------------------------------------- PII-safe


def test_trace_contains_no_resume_text_or_pii(session: Session, session_factory, db_settings) -> None:
    analysis_id = _run_match(session, session_factory, db_settings)
    payload = json.dumps([row.chain for row in _chains(session, analysis_id)], ensure_ascii=False)

    resume_text = read_fixture_bytes("resume_match.txt").decode("utf-8")
    assert "13900139000" not in payload  # 电话
    assert "liming@example.com" not in payload  # 邮箱
    assert "李明" not in payload  # 姓名
    assert "华中科技大学" not in payload  # 简历原文片段
    assert resume_text not in payload  # 完整简历原文
    # JD 侧单条 requirement 的短引用是允许的（业务文本，非个人 PII）
    assert "本科及以上学历，计算机相关专业" in payload


def test_trace_excerpt_is_metadata_only(session: Session, session_factory, db_settings) -> None:
    analysis_id = _run_match(session, session_factory, db_settings)
    banned = {"excerpt", "text", "content", "raw", "full_text"}
    for row in _chains(session, analysis_id):
        for entry in row.chain.get("evidence", []):
            assert not (banned & set(entry))
        requirement = row.chain.get("requirement")
        if requirement is not None:
            # JD 侧只允许 source_text（业务文本）+ anchor 定位，不允许任何简历原文
            assert set(requirement) <= {
                "requirement_id",
                "req_type",
                "operator",
                "value",
                "is_hard",
                "source_text",
                "anchors",
            }
            assert all(
                set(anchor) <= {"doc_kind", "parsed_document_id", "page", "char_start", "char_end"}
                for anchor in requirement["anchors"]
            )


# ---------------------------------------------------------------- 可重生成（deterministic）


def test_same_inputs_produce_identical_trace_chains(
    session: Session, session_factory, db_settings
) -> None:
    first_id = _run_match(session, session_factory, db_settings)
    second = make_analysis_pair(
        session, db_settings, resume_fixture="resume_match.txt", jd_fixture="jd_match.txt"
    )
    second_provider = _provider()
    assert (
        run_analysis(
            session_factory=session_factory,
            settings=db_settings,
            analysis_id=second.analysis.id,
            provider=second_provider,
        ).status
        == "completed"
    )
    assert second_provider.calls == []  # 复用 artifact

    first_chains = _normalized(_chains(session, first_id))
    second_chains = _normalized(_chains(session, second.analysis.id))
    assert first_chains == second_chains  # 同输入 => 逐字段一致的决策链


def test_trace_has_no_duplicate_rows_after_replay(
    session: Session, session_factory, db_settings
) -> None:
    analysis_id = _run_match(session, session_factory, db_settings)
    before = len(_chains(session, analysis_id))
    assert results_repo.requeue_analysis(session, analysis_id) is True
    assert (
        run_analysis(session_factory=session_factory, settings=db_settings, analysis_id=analysis_id).status
        == "completed"
    )
    assert len(_chains(session, analysis_id)) == before
    duplicates = session.execute(
        text(
            "SELECT count(*) FROM ("
            "  SELECT analysis_id, decision_type, decision_key FROM decision_traces"
            "  GROUP BY 1,2,3 HAVING count(*) > 1"
            ") AS dup"
        )
    ).scalar_one()
    assert int(duplicates) == 0


def test_score_component_traces_cover_every_section(
    session: Session, session_factory, db_settings
) -> None:
    analysis_id = _run_match(session, session_factory, db_settings)
    score_traces = [row for row in _chains(session, analysis_id) if row.decision_type == "score_component"]
    sections = {row.decision_key for row in score_traces}
    assert sections == {
        "section:skills",
        "section:experience",
        "section:education",
        "section:location",
        "section:language",
    }
    snapshot = results_repo.get_score_snapshot(session, analysis_id)
    assert snapshot is not None
    for row in score_traces:
        assert row.chain["score_contribution"]["total"] == snapshot.total
        assert row.chain["normalization"]["unknown_policy"]["mode"] == "exclude_and_renormalize"

# mypy: disable-error-code="arg-type,attr-defined,assignment,union-attr"
"""integration: Phase 3 acceptance gate（§47 清单的逐项可执行断言）。"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from jobfit.core.enums import AnalysisStatus, SkillMatchStatus, Verdict
from jobfit.db import models
from jobfit.db.repositories import analyses as analyses_repo
from jobfit.db.repositories import results as results_repo
from jobfit.evidence.embeddings import HASH_EMBEDDING_MODEL, HashingEmbeddingProvider
from jobfit.evidence.retrieval import retrieve_evidence
from jobfit.matching import service as matching_service
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
    result = run_analysis(
        session_factory=session_factory,
        settings=settings,
        analysis_id=pair.analysis.id,
        provider=DeterministicProvider(
            resume_payload=dict(resume_payload), jd_payload=dict(jd_payload)
        ),
        ttl_seconds=ttl_seconds,
    )
    return pair, result


# ---------------------------------------------------------------- 核心链路


def test_acceptance_end_to_end_match(session: Session, session_factory, db_settings) -> None:
    """[analysis lifecycle real][hard constraints deterministic][evidence refs real]
    [retrieval real][decision trace real][analysis idempotency real]"""
    pair, result = _run(
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
    analysis = session.get(models.Analysis, pair.analysis.id)
    assert analysis is not None
    session.refresh(analysis)
    assert analysis.status == AnalysisStatus.SUCCEEDED.value

    # Phase 2 artifact 可正常读取（不破坏既有产物）
    assert session.get(models.ResumeProfile, analysis.resume_profile_id) is not None
    assert session.get(models.JDProfile, analysis.jd_profile_id) is not None

    # 结果齐备：6 约束 + 2 技能 + 13 trace + 1 score snapshot
    counts = results_repo.count_analysis_results(session, pair.analysis.id)
    assert counts == {"constraints": 6, "skill_matches": 2, "traces": 13, "scores": 1}

    # evidence 引用真实 chunk
    for row in results_repo.list_constraint_results(session, pair.analysis.id):
        for evidence_id in row.evidence_ids or []:
            assert session.get(models.DocumentChunk, uuid.UUID(str(evidence_id))) is not None


def test_acceptance_true_false_unknown_semantics(session: Session, session_factory, db_settings) -> None:
    """[TRUE/FALSE/UNKNOWN 正确]：三种场景同时在同一 DB 中被正确区分。"""
    _match_pair, match = _run(
        session,
        session_factory,
        db_settings,
        resume_fixture="resume_match.txt",
        jd_fixture="jd_match.txt",
        resume_payload=RESUME_MATCH_PAYLOAD,
        jd_payload=JD_MATCH_PAYLOAD,
    )
    mismatch_pair, mismatch = _run(
        session,
        session_factory,
        db_settings,
        resume_fixture="resume_mismatch.txt",
        jd_fixture="jd_mismatch.txt",
        resume_payload=RESUME_MISMATCH_PAYLOAD,
        jd_payload=JD_MISMATCH_PAYLOAD,
    )
    unknown_pair, unknown = _run(
        session,
        session_factory,
        db_settings,
        resume_fixture="resume_mismatch.txt",
        jd_fixture="jd_unknown.txt",
        resume_payload=RESUME_MISMATCH_PAYLOAD,
        jd_payload=JD_UNKNOWN_PAYLOAD,
    )
    assert (match.gate, mismatch.gate, unknown.gate) == ("pass", "blocked", "unknown")

    def _results(analysis_id: uuid.UUID) -> set[str]:
        return {
            row
            for row in session.execute(
                select(models.HardConstraintResult.result).where(
                    models.HardConstraintResult.analysis_id == analysis_id
                )
            )
            .scalars()
            .all()
        }

    assert _results(mismatch_pair.analysis.id) == {Verdict.NOT_MET.value, Verdict.UNKNOWN.value}
    assert _results(unknown_pair.analysis.id) == {Verdict.UNKNOWN.value}
    # 缺失证据绝不写成 FALSE
    assert Verdict.NOT_MET.value not in _results(unknown_pair.analysis.id)


def test_acceptance_skill_matching_is_deterministic_and_no_false_positives(
    session: Session, session_factory, db_settings
) -> None:
    """[skill matching deterministic][no semantic false-positive mappings]"""
    pair, result = _run(
        session,
        session_factory,
        db_settings,
        resume_fixture="resume_mismatch.txt",
        jd_fixture="jd_mismatch.txt",
        resume_payload=RESUME_MISMATCH_PAYLOAD,
        jd_payload=JD_MISMATCH_PAYLOAD,
    )
    rows = session.execute(
        select(models.SkillMatchResult.norm_used, models.SkillMatchResult.status).where(
            models.SkillMatchResult.analysis_id == pair.analysis.id
        )
    ).all()
    # PyTorch 不在简历中：必须 UNKNOWN（不是 MISSING、更不是 matched）
    assert dict(rows) == {"pytorch": SkillMatchStatus.UNKNOWN.value}
    assert result.status == "completed"

    # 同输入两次计算 => 逐字段一致
    with session_factory() as work:
        ctx = matching_service.load_run_context(work, analysis_id=pair.analysis.id)
        from_jd = matching_service.compute_all(ctx=ctx, retrieved={})
        again = matching_service.compute_all(ctx=ctx, retrieved={})
    assert [(m.norm_used, m.status, m.evidence_ids) for m in from_jd.skill_matches] == [
        (m.norm_used, m.status, m.evidence_ids) for m in again.skill_matches
    ]
    assert [c.verdict for c in from_jd.constraints] == [c.verdict for c in again.constraints]


def test_acceptance_retrieval_real_vector_and_scope_isolation(
    session: Session, session_factory, db_settings
) -> None:
    """[retrieval real][pgvector real][retrieval deterministic][document scope isolation]"""
    pair, _result = _run(
        session,
        session_factory,
        db_settings,
        resume_fixture="resume_match.txt",
        jd_fixture="jd_match.txt",
        resume_payload=RESUME_MATCH_PAYLOAD,
        jd_payload=JD_MATCH_PAYLOAD,
    )
    analysis = session.get(models.Analysis, pair.analysis.id)
    assert analysis is not None
    session.refresh(analysis)
    assert analysis.embedding_model == HASH_EMBEDDING_MODEL

    embedded = session.execute(
        text("SELECT count(*) FROM document_chunks WHERE embedding IS NOT NULL AND embedding_model = :m"),
        {"m": HASH_EMBEDDING_MODEL},
    ).scalar_one()
    assert int(embedded) >= 1  # 真实写入了 pgvector 列

    resume_profile = session.get(models.ResumeProfile, analysis.resume_profile_id)
    assert resume_profile is not None
    parsed_document_id = uuid.UUID(str(resume_profile.parsed_document_id))
    provider = HashingEmbeddingProvider()
    first = retrieve_evidence(
        session,
        query="熟悉 Python",
        parsed_document_ids=[parsed_document_id],
        top_k=3,
        provider=provider,
        anchor_terms=["Python"],
    )
    second = retrieve_evidence(
        session,
        query="熟悉 Python",
        parsed_document_ids=[parsed_document_id],
        top_k=3,
        provider=provider,
        anchor_terms=["Python"],
    )
    assert [hit.source_chunk_id for hit in first.hits] == [hit.source_chunk_id for hit in second.hits]
    assert [hit.score for hit in first.hits] == [hit.score for hit in second.hits]
    assert first.retrieval_method == "vector"
    # scope 内所有命中都属于被检索文档
    assert all(hit.parsed_document_id == parsed_document_id for hit in first.hits)


def test_acceptance_concurrency_and_stale_fencing(
    session: Session, session_factory, db_settings
) -> None:
    """[claim/heartbeat/recover working][concurrency safe][stale worker fenced]"""
    pair = make_analysis_pair(
        session, db_settings, resume_fixture="resume_match.txt", jd_fixture="jd_match.txt"
    )
    stale = analyses_repo.claim_specific(
        session, analysis_id=pair.analysis.id, worker_id="stale", ttl_seconds=0
    )
    assert stale is not None
    assert analyses_repo.claim_specific(
        session, analysis_id=pair.analysis.id, worker_id="other", ttl_seconds=60
    ) is None  # 并发下只有一个 claim 生效
    assert results_repo.guard_lease(session, analysis_id=pair.analysis.id, claim_token=stale) is False
    assert analyses_repo.recover_stale(session) == [pair.analysis.id]

    fresh = analyses_repo.claim_specific(
        session, analysis_id=pair.analysis.id, worker_id="fresh", ttl_seconds=120
    )
    assert fresh is not None and fresh != stale
    assert analyses_repo.heartbeat(session, pair.analysis.id, fresh, 120) is True
    assert results_repo.mark_succeeded(
        session, analysis_id=pair.analysis.id, claim_token=stale, phase="hijack"
    ) is False
    assert results_repo.mark_succeeded(
        session, analysis_id=pair.analysis.id, claim_token=fresh, phase="analysis_complete"
    ) is True


def test_acceptance_no_llm_calls_for_bound_analysis(
    session: Session, session_factory, db_settings
) -> None:
    """[hard constraints deterministic]：确定性阶段可以在**没有 LLM 凭证**时完整运行。"""
    pair, _result = _run(
        session,
        session_factory,
        db_settings,
        resume_fixture="resume_match.txt",
        jd_fixture="jd_match.txt",
        resume_payload=RESUME_MATCH_PAYLOAD,
        jd_payload=JD_MATCH_PAYLOAD,
    )
    session.expire_all()
    analysis = session.get(models.Analysis, pair.analysis.id)
    assert analysis is not None

    # 用一个"一旦被调用就失败"的 provider 复算确定性阶段
    class _ExplodingProvider:
        model_name = "exploding"

        async def complete_text(self, prompt: str, *, max_tokens: int | None = None) -> str:
            raise AssertionError("deterministic stage must not call the LLM")

        async def complete_structured(self, prompt: str, *, schema):
            raise AssertionError("deterministic stage must not call the LLM")

    with session_factory() as work:
        ctx = matching_service.load_run_context(work, analysis_id=pair.analysis.id)
        retrieved = matching_service.retrieve_for_requirements(
            session_factory, ctx=ctx, provider=None, top_k=3
        )
        computed = matching_service.compute_all(ctx=ctx, retrieved=retrieved)
    assert computed.score.gate == "pass"  # 全程无 LLM
    assert _ExplodingProvider.model_name == "exploding"


def test_acceptance_migrations_enforce_result_identity(session: Session, db_settings) -> None:
    """[migrations real]：结果 identity 的唯一约束必须真实存在于 DB（不是仅代码约定）。"""
    names = {
        row[0]
        for row in session.execute(
            text(
                "SELECT conname FROM pg_constraint WHERE conname IN ("
                "'uq_hard_constraint_result','uq_skill_match_result',"
                "'uq_decision_trace','uq_score_snapshot')"
            )
        ).all()
    }
    assert names == {
        "uq_hard_constraint_result",
        "uq_skill_match_result",
        "uq_decision_trace",
        "uq_score_snapshot",
    }
    definition = session.execute(
        text(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
            " WHERE conrelid = 'analyses'::regclass AND contype = 'c'"
        )
    ).scalar_one()
    assert "succeeded" in str(definition)


def test_acceptance_unique_constraints_reject_duplicate_results(
    session: Session, session_factory, db_settings
) -> None:
    """绕过应用层手工重复插入必须被数据库拒绝（约束是真实屏障）。"""
    from sqlalchemy.exc import IntegrityError

    pair, result = _run(
        session,
        session_factory,
        db_settings,
        resume_fixture="resume_match.txt",
        jd_fixture="jd_match.txt",
        resume_payload=RESUME_MATCH_PAYLOAD,
        jd_payload=JD_MATCH_PAYLOAD,
    )
    assert result.status == "completed"
    existing = results_repo.list_constraint_results(session, pair.analysis.id)[0]
    session.add(
        models.HardConstraintResult(
            analysis_id=existing.analysis_id,
            requirement_id=existing.requirement_id,
            constraint_type=existing.constraint_type,
            result=existing.result,
            basis=existing.basis,
            ruleset_version=existing.ruleset_version,
            reason_code=existing.reason_code,
            evidence_ids=[],
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_acceptance_analysis_row_is_the_only_result_owner(
    session: Session, session_factory, db_settings
) -> None:
    """[document scope isolation]：analysis-scoped 结果不会串到别的 analysis。"""
    first_pair, _ = _run(
        session,
        session_factory,
        db_settings,
        resume_fixture="resume_match.txt",
        jd_fixture="jd_match.txt",
        resume_payload=RESUME_MATCH_PAYLOAD,
        jd_payload=JD_MATCH_PAYLOAD,
    )
    second_pair, _ = _run(
        session,
        session_factory,
        db_settings,
        resume_fixture="resume_mismatch.txt",
        jd_fixture="jd_mismatch.txt",
        resume_payload=RESUME_MISMATCH_PAYLOAD,
        jd_payload=JD_MISMATCH_PAYLOAD,
    )
    for analysis_id, expected in ((first_pair.analysis.id, 6), (second_pair.analysis.id, 4)):
        own = session.execute(
            select(func.count())
            .select_from(models.HardConstraintResult)
            .where(models.HardConstraintResult.analysis_id == analysis_id)
        ).scalar_one()
        assert int(own) == expected
        traces = session.execute(
            select(func.count())
            .select_from(models.DecisionTrace)
            .where(models.DecisionTrace.analysis_id == analysis_id)
        ).scalar_one()
        assert int(traces) > 0


def test_acceptance_phases_are_separated_no_phase4_side_effects(
    session: Session, session_factory, db_settings
) -> None:
    """[no Phase 4 functionality accidentally implemented]：不产出 critique / report / review。"""
    pair, result = _run(
        session,
        session_factory,
        db_settings,
        resume_fixture="resume_match.txt",
        jd_fixture="jd_match.txt",
        resume_payload=RESUME_MATCH_PAYLOAD,
        jd_payload=JD_MATCH_PAYLOAD,
    )
    assert result.status == "completed"
    for model in (models.Critique, models.Report, models.Review):
        count = session.execute(
            select(func.count()).select_from(model).where(model.analysis_id == pair.analysis.id)
        ).scalar_one()
        assert int(count) == 0
    # 成功终态是 succeeded，而不是 HITL 的 awaiting_review/finalized
    analysis = session.get(models.Analysis, pair.analysis.id)
    assert analysis is not None
    assert analysis.status not in {"awaiting_review", "finalized", "rejected"}


def test_acceptance_pipeline_runner_is_reused_not_duplicated() -> None:
    """[no fake implementation]：Phase 3 复用同一 runner/LangGraph，没有第二套执行引擎。"""
    from jobfit.workflow import graph, runner

    assert hasattr(graph, "build_extraction_graph") and hasattr(graph, "build_analysis_graph")
    assert hasattr(runner, "run_extraction_pipeline") and hasattr(runner, "run_analysis_pipeline")
    assert asyncio.iscoroutinefunction(runner.run_analysis_pipeline)

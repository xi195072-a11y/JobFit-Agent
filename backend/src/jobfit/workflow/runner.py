"""运行入口：claim（fencing）→ 执行图 → 结果/失败终态。

- `run_extraction_pipeline`：Phase 2（只做 parse + structured extraction + 绑定）。
- `run_analysis_pipeline`：Phase 3（在抽取之后执行确定性分析并落库，终态 `succeeded`）。

两者共用同一套 claim/state/mark_failed 语义，不重复建设执行引擎。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy.orm import Session, sessionmaker

from jobfit.config.settings import Settings
from jobfit.core.errors import JobFitError, LeaseLost
from jobfit.db.repositories import analyses as analyses_repo
from jobfit.evidence.embeddings import EmbeddingProvider
from jobfit.llm.provider import LLMProvider
from jobfit.matching.service import DEFAULT_TOP_K
from jobfit.workflow.graph import (
    build_analysis_graph,
    build_extraction_graph,
    build_phase4_resume_graph,
)
from jobfit.workflow.state import initial_state

WORKER_ID = "phase2-inline-runner"
ANALYSIS_WORKER_ID = "phase3-inline-runner"
PHASE4_WORKER_ID = "phase4-inline-runner"


@dataclass
class ExtractionRunResult:
    status: str
    analysis_id: str
    resume_profile_id: str | None = None
    jd_profile_id: str | None = None
    resume_fingerprint: str | None = None
    jd_fingerprint: str | None = None
    errors: list[str] = field(default_factory=list)


@dataclass
class AnalysisRunResult:
    status: str
    analysis_id: str
    gate: str | None = None
    score_total: float | None = None
    constraint_count: int = 0
    skill_match_count: int = 0
    trace_count: int = 0
    flags: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class Phase4RunResult:
    """Phase 4 续接结果：critique -> report -> awaiting_review。"""

    status: str
    analysis_id: str
    critique_status: str | None = None
    critique_validation: str | None = None
    report_version: int | None = None
    report_stage: str | None = None
    errors: list[str] = field(default_factory=list)


def _claim_and_state(
    session_factory: sessionmaker[Session],
    *,
    analysis_id: uuid.UUID,
    worker_id: str,
    ttl_seconds: int,
):
    """原子领取 + 构造初始 state；返回 (claim_token, state) 或 (None, None)。"""
    with session_factory() as session:
        claim_token = analyses_repo.claim_specific(
            session, analysis_id=analysis_id, worker_id=worker_id, ttl_seconds=ttl_seconds
        )
        if claim_token is None:
            return None, None
        analysis = analyses_repo.get_analysis(session, analysis_id)
        assert analysis is not None
        state = initial_state(
            analysis_id=str(analysis.id),
            claim_token=str(claim_token),
            worker_id=worker_id,
            pipeline_version=analysis.pipeline_version,
            llm_model=analysis.llm_model or "",
            resume_document_id=str(analysis.resume_document_id),
            jd_document_id=str(analysis.jd_document_id),
        )
    return claim_token, state


async def run_extraction_pipeline(
    *,
    session_factory: sessionmaker[Session],
    settings: Settings,
    provider: LLMProvider,
    analysis_id: uuid.UUID,
    worker_id: str = WORKER_ID,
    ttl_seconds: int | None = None,
) -> ExtractionRunResult:
    ttl = ttl_seconds or settings.lease_ttl_seconds
    claim_token, state = _claim_and_state(
        session_factory, analysis_id=analysis_id, worker_id=worker_id, ttl_seconds=ttl
    )
    if claim_token is None or state is None:
        return ExtractionRunResult(
            status="not_claimable",
            analysis_id=str(analysis_id),
            errors=["analysis is not in queued state (already claimed or terminal)"],
        )

    graph = build_extraction_graph(
        session_factory=session_factory,
        settings=settings,
        provider=provider,
        max_llm_attempts=settings.max_llm_attempts,
        ttl_seconds=ttl,
    )

    try:
        final_state = await graph.ainvoke(state)
    except JobFitError as exc:
        with session_factory() as session:
            analyses_repo.mark_failed(
                session,
                analysis_id=analysis_id,
                claim_token=claim_token,
                phase=f"failed:{type(exc).__name__}",
            )
        return ExtractionRunResult(
            status="failed",
            analysis_id=str(analysis_id),
            errors=[f"{type(exc).__name__}: {exc}"],
        )

    return ExtractionRunResult(
        status="completed",
        analysis_id=str(analysis_id),
        resume_profile_id=final_state.get("resume_profile_id"),
        jd_profile_id=final_state.get("jd_profile_id"),
        resume_fingerprint=final_state.get("resume_fingerprint"),
        jd_fingerprint=final_state.get("jd_fingerprint"),
        errors=list(final_state.get("errors", [])),
    )


async def run_analysis_pipeline(
    *,
    session_factory: sessionmaker[Session],
    settings: Settings,
    provider: LLMProvider,
    embedding_provider: EmbeddingProvider | None,
    analysis_id: uuid.UUID,
    worker_id: str = ANALYSIS_WORKER_ID,
    ttl_seconds: int | None = None,
    top_k: int = DEFAULT_TOP_K,
) -> AnalysisRunResult:
    """执行一次完整的确定性 analysis（parse → extract → bind → constraints → retrieval
    → skill matching → trace → score → succeeded）。

    失败语义：
    - `not_claimable`：不是 queued（已被领取或已终态）——不写任何业务数据；
    - `lease_lost`：执行中失去 lease —— **不写结果**，也不改状态（交由 recover_stale 重排）；
    - `failed`：确定性失败（fenced mark_failed）。
    """
    ttl = ttl_seconds or settings.lease_ttl_seconds
    claim_token, state = _claim_and_state(
        session_factory, analysis_id=analysis_id, worker_id=worker_id, ttl_seconds=ttl
    )
    if claim_token is None or state is None:
        return AnalysisRunResult(
            status="not_claimable",
            analysis_id=str(analysis_id),
            errors=["analysis is not in queued state (already claimed or terminal)"],
        )

    graph = build_analysis_graph(
        session_factory=session_factory,
        settings=settings,
        provider=provider,
        embedding_provider=embedding_provider,
        max_llm_attempts=settings.max_llm_attempts,
        ttl_seconds=ttl,
        top_k=top_k,
    )

    try:
        final_state = await graph.ainvoke(state)
    except LeaseLost as exc:
        # 不写任何结果；状态保持 running，等待 recover_stale 重排。
        return AnalysisRunResult(
            status="lease_lost",
            analysis_id=str(analysis_id),
            errors=[f"LeaseLost: {exc}"],
        )
    except JobFitError as exc:
        with session_factory() as session:
            analyses_repo.mark_failed(
                session,
                analysis_id=analysis_id,
                claim_token=claim_token,
                phase=f"failed:{type(exc).__name__}",
            )
        return AnalysisRunResult(
            status="failed",
            analysis_id=str(analysis_id),
            errors=[f"{type(exc).__name__}: {exc}"],
        )

    return AnalysisRunResult(
        status="completed",
        analysis_id=str(analysis_id),
        gate=final_state.get("gate"),
        score_total=final_state.get("score_total"),
        constraint_count=int(final_state.get("constraint_count", 0)),
        skill_match_count=int(final_state.get("skill_match_count", 0)),
        trace_count=int(final_state.get("trace_count", 0)),
        flags=list(final_state.get("analysis_flags", [])),
        errors=list(final_state.get("errors", [])),
    )


async def run_phase4_pipeline(
    *,
    session_factory: sessionmaker[Session],
    settings: Settings,
    provider: LLMProvider | None,
    analysis_id: uuid.UUID,
    worker_id: str = PHASE4_WORKER_ID,
    ttl_seconds: int | None = None,
) -> Phase4RunResult:
    """Phase 4 续接（§39）：succeeded -> (critique -> report -> awaiting_review)。

    - 只对已 `succeeded` 的 analysis 生效（claim_phase4 原子领取；非 succeeded /
      已被并发领取 => not_claimable，无副作用）；
    - 复用既有确定性结果（resume 图不重跑抽取/检索/重算），critique/report 全为
      fenced 写入；
    - provider=None => critique 落 unavailable（EXTERNAL CREDENTIAL BLOCKED），
      不伪造 live 验证。
    """
    ttl = ttl_seconds or settings.lease_ttl_seconds
    with session_factory() as session:
        claim_token = analyses_repo.claim_phase4(
            session, analysis_id=analysis_id, worker_id=worker_id, ttl_seconds=ttl
        )
        if claim_token is None:
            return Phase4RunResult(
                status="not_claimable",
                analysis_id=str(analysis_id),
                errors=["analysis is not in succeeded state (already claimed or terminal)"],
            )
        analysis = analyses_repo.get_analysis(session, analysis_id)
        assert analysis is not None
        state = initial_state(
            analysis_id=str(analysis.id),
            claim_token=str(claim_token),
            worker_id=worker_id,
            pipeline_version=analysis.pipeline_version,
            llm_model=analysis.llm_model or "",
            resume_document_id=str(analysis.resume_document_id),
            jd_document_id=str(analysis.jd_document_id),
        )

    graph = build_phase4_resume_graph(
        session_factory=session_factory,
        provider=provider,
        max_llm_attempts=settings.max_llm_attempts,
        ttl_seconds=ttl,
    )

    try:
        final_state = await graph.ainvoke(state)
    except LeaseLost as exc:
        # 不写任何结果；状态保持 running，等待 recover_stale 重排。
        return Phase4RunResult(
            status="lease_lost",
            analysis_id=str(analysis_id),
            errors=[f"LeaseLost: {exc}"],
        )
    except JobFitError as exc:
        with session_factory() as session:
            analyses_repo.mark_failed(
                session,
                analysis_id=analysis_id,
                claim_token=claim_token,
                phase=f"failed:{type(exc).__name__}",
            )
        return Phase4RunResult(
            status="failed",
            analysis_id=str(analysis_id),
            errors=[f"{type(exc).__name__}: {exc}"],
        )

    return Phase4RunResult(
        status="completed",
        analysis_id=str(analysis_id),
        critique_status=final_state.get("critique_status"),
        critique_validation=final_state.get("critique_validation"),
        report_version=(
            int(final_state["report_version"])
            if final_state.get("report_version") is not None
            else None
        ),
        report_stage=final_state.get("report_stage"),
        errors=list(final_state.get("errors", [])),
    )

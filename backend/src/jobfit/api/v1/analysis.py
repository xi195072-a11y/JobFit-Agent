"""Phase 3 API：执行确定性 analysis + 读取约束/技能匹配/决策链/评分 + 检索。

约定（与 Phase 2 一致）：
- 一律返回 Pydantic response，绝不直接返回 SQLAlchemy 对象；
- 不泄露 PII（本层不返回简历原文；decision trace 内也不含原文）；
- 可重复调用：`POST /analyses/{id}/run` 对非 queued 的 analysis 返回 409，不产生副作用。
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, sessionmaker

from jobfit.api.deps import (
    get_embedding_provider,
    get_llm_provider,
    get_session,
    get_session_factory,
    get_settings_dep,
)
from jobfit.api.errors import ERROR_RESPONSES
from jobfit.api.schemas import (
    AnalysisRunRead,
    AnalysisRunRequest,
    ConstraintResultRead,
    DecisionTraceRead,
    EvidenceListRead,
    EvidenceReferenceRead,
    EvidenceRefRead,
    RetrievalHitRead,
    RetrievalQuery,
    RetrievalResponse,
    ScoreSectionRead,
    ScoreSnapshotRead,
    SkillMatchResultRead,
)
from jobfit.config.settings import Settings
from jobfit.core.errors import NotFound
from jobfit.db.repositories import analyses as analyses_repo
from jobfit.db.repositories import results as results_repo
from jobfit.evidence.embeddings import EmbeddingProvider
from jobfit.evidence.retrieval import retrieve_evidence
from jobfit.llm.provider import LLMProvider
from jobfit.matching import trace as trace_mod
from jobfit.workflow.runner import run_analysis_pipeline

router = APIRouter(tags=["phase3"], responses=ERROR_RESPONSES)


def _gate_from_flags(flags: list[str]) -> str:
    for flag in flags:
        if flag.startswith("GATE:"):
            return flag.split(":", 1)[1]
    return "unknown"


@router.post("/analyses/{analysis_id}/run", response_model=AnalysisRunRead)
async def run_analysis_endpoint(
    analysis_id: uuid.UUID,
    body: AnalysisRunRequest | None = None,
    session: Session = Depends(get_session),
    session_factory: sessionmaker[Session] = Depends(get_session_factory),
    provider: LLMProvider = Depends(get_llm_provider),
    embedding_provider: EmbeddingProvider = Depends(get_embedding_provider),
    settings: Settings = Depends(get_settings_dep),
) -> AnalysisRunRead:
    analysis = analyses_repo.get_analysis(session, analysis_id)
    if analysis is None:
        raise NotFound(f"analysis {analysis_id} not found")
    if body is not None and body.requeue:
        results_repo.requeue_analysis(session, analysis_id)

    result = await run_analysis_pipeline(
        session_factory=session_factory,
        settings=settings,
        provider=provider,
        embedding_provider=embedding_provider,
        analysis_id=analysis_id,
    )
    if result.status != "completed":
        raise HTTPException(
            status_code=409,
            detail={
                "status": result.status,
                "errors": result.errors,
            },
        )
    return AnalysisRunRead(
        analysis_id=analysis_id,
        status=result.status,
        gate=result.gate,
        score_total=result.score_total,
        constraint_count=result.constraint_count,
        skill_match_count=result.skill_match_count,
        trace_count=result.trace_count,
        flags=result.flags,
        errors=result.errors,
    )


@router.get("/analyses/{analysis_id}/constraints", response_model=list[ConstraintResultRead])
def list_constraints(
    analysis_id: uuid.UUID,
    session: Session = Depends(get_session),
) -> list[ConstraintResultRead]:
    if analyses_repo.get_analysis(session, analysis_id) is None:
        raise NotFound(f"analysis {analysis_id} not found")
    return [
        ConstraintResultRead.model_validate(row)
        for row in results_repo.list_constraint_results(session, analysis_id)
    ]


@router.get(
    "/analyses/{analysis_id}/skill-matches",
    response_model=list[SkillMatchResultRead],
    responses=ERROR_RESPONSES,
)
def list_skill_matches(
    analysis_id: uuid.UUID,
    session: Session = Depends(get_session),
) -> list[SkillMatchResultRead]:
    if analyses_repo.get_analysis(session, analysis_id) is None:
        raise NotFound(f"analysis {analysis_id} not found")
    return [
        SkillMatchResultRead.model_validate(row)
        for row in results_repo.list_skill_match_results(session, analysis_id)
    ]


@router.get("/analyses/{analysis_id}/trace", response_model=list[DecisionTraceRead])
def list_traces(
    analysis_id: uuid.UUID,
    session: Session = Depends(get_session),
) -> list[DecisionTraceRead]:
    if analyses_repo.get_analysis(session, analysis_id) is None:
        raise NotFound(f"analysis {analysis_id} not found")
    return [
        DecisionTraceRead.model_validate(row) for row in results_repo.list_traces(session, analysis_id)
    ]


@router.get("/analyses/{analysis_id}/score", response_model=ScoreSnapshotRead)
def get_score(
    analysis_id: uuid.UUID,
    session: Session = Depends(get_session),
) -> ScoreSnapshotRead:
    if analyses_repo.get_analysis(session, analysis_id) is None:
        raise NotFound(f"analysis {analysis_id} not found")
    snapshot = results_repo.get_score_snapshot(session, analysis_id)
    if snapshot is None:
        raise NotFound(f"analysis {analysis_id} has no score snapshot yet")
    flags = list(snapshot.flags or [])
    return ScoreSnapshotRead(
        analysis_id=analysis_id,
        kind=snapshot.kind,
        total=snapshot.total,
        gate=_gate_from_flags(flags),
        scoring_version=snapshot.scoring_version,
        flags=flags,
        per_section=[ScoreSectionRead.model_validate(item) for item in snapshot.per_section or []],
    )


@router.post("/retrieval/search", response_model=RetrievalResponse)
def retrieval_search(
    body: RetrievalQuery,
    session: Session = Depends(get_session),
    embedding_provider: EmbeddingProvider = Depends(get_embedding_provider),
) -> RetrievalResponse:
    """显式检索服务（§16）：输入 query + candidate scope + top_k + 可选 filter / anchor gate。

    只返回 chunk 定位与分数（chunk id / 区间 / score / method），**不返回文本**。
    """
    result = retrieve_evidence(
        session,
        query=body.query,
        parsed_document_ids=body.parsed_document_ids,
        top_k=body.top_k,
        provider=embedding_provider,
        method=body.method,
        page=body.page,
        anchor_terms=body.anchor_terms,
        min_similarity=body.min_similarity,
        min_lexical_score=body.min_lexical_score,
    )
    return RetrievalResponse(
        query=body.query,
        retrieval_method=result.retrieval_method,
        embedding_model=result.embedding_model,
        scope_size=result.scope_size,
        hits=[
            RetrievalHitRead(
                source_chunk_id=hit.source_chunk_id,
                document_id=hit.document_id,
                parsed_document_id=hit.parsed_document_id,
                chunk_index=hit.chunk_index,
                page=hit.page,
                char_start=hit.char_start,
                char_end=hit.char_end,
                score=hit.score,
                retrieval_method=hit.retrieval_method,
            )
            for hit in result.hits
        ],
    )


@router.get(
    "/analyses/{analysis_id}/evidence", response_model=EvidenceListRead, responses=ERROR_RESPONSES
)
def list_evidence(
    analysis_id: uuid.UUID,
    session: Session = Depends(get_session),
) -> EvidenceListRead:
    """PII-safe 证据清单（§13）：chunk 定位 + hash + 「被谁引用」，**不含原文**。

    聚合 hard constraints / skill matches / decision traces 的 evidence 环，
    让前端展示 source / chunk reference / span / reason 时无需读取原始文档。
    返回顺序确定性（按 char_start, source_chunk_id；引用按 kind, ref_id）。
    """
    if analyses_repo.get_analysis(session, analysis_id) is None:
        raise NotFound(f"analysis {analysis_id} not found")

    buckets: dict[str, list[EvidenceReferenceRead]] = {}
    seen: set[tuple[str, str, str, str | None]] = set()

    def _add(chunk_id: object, reference: EvidenceReferenceRead) -> None:
        raw = str(chunk_id)
        try:
            uuid.UUID(raw)
        except ValueError:
            return
        key = (raw, reference.kind, reference.ref_id, reference.tier)
        if key in seen:
            return
        seen.add(key)
        buckets.setdefault(raw, []).append(reference)

    for row in results_repo.list_constraint_results(session, analysis_id):
        for chunk_id in row.evidence_ids or []:
            _add(
                chunk_id,
                EvidenceReferenceRead(
                    kind="constraint",
                    ref_id=str(row.requirement_id),
                    label=f"{row.constraint_type}:{row.result}",
                ),
            )
    for row in results_repo.list_skill_match_results(session, analysis_id):
        for chunk_id in row.evidence_ids or []:
            _add(
                chunk_id,
                EvidenceReferenceRead(
                    kind="skill",
                    ref_id=str(row.jd_requirement_id),
                    label=row.status,
                ),
            )
    for trace in results_repo.list_traces(session, analysis_id):
        for ring in (trace.chain or {}).get("evidence", []) or []:
            chunk_id = ring.get("source_chunk_id")
            if not chunk_id:
                continue
            _add(
                chunk_id,
                EvidenceReferenceRead(
                    kind="trace",
                    ref_id=f"{trace.decision_type}:{trace.decision_key}",
                    label=trace.decision_type,
                    tier=ring.get("tier"),
                ),
            )

    index = trace_mod.load_evidence_index(session, list(buckets))
    items: list[EvidenceRefRead] = []
    for chunk_id, references in buckets.items():
        meta = index.get(chunk_id)
        ordered_refs = sorted(references, key=lambda r: (r.kind, r.ref_id, r.tier or ""))
        if meta is None:
            items.append(
                EvidenceRefRead(
                    source_chunk_id=uuid.UUID(chunk_id),
                    resolvable=False,
                    referenced_by=ordered_refs,
                )
            )
            continue
        items.append(
            EvidenceRefRead(
                source_chunk_id=uuid.UUID(chunk_id),
                char_start=int(meta["char_start"]),
                char_end=int(meta["char_end"]),
                page=meta.get("page"),
                span_sha256=str(meta["span_sha256"]),
                resolvable=True,
                referenced_by=ordered_refs,
            )
        )
    items.sort(key=lambda item: (item.char_start, str(item.source_chunk_id)))
    return EvidenceListRead(analysis_id=analysis_id, items=items, total=len(items))

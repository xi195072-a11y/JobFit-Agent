"""Phase 4 API（§39/§40）：critique / report / review / finalize / reject。

约定（与 Phase 2/3 一致）：
- 一律返回 Pydantic response，绝不直接返回 SQLAlchemy 对象；
- 每个端点先校验 analysis 存在 + 状态转移合法（§40 ownership/state guard）；
- 不泄露 PII；report/critique 响应只含结构化结果与定位（无简历原文）；
- 写路径全部经过 service（fenced / 幂等），本层不直接写业务表。

DEEPSEEK_API_KEY 缺失时：POST /critique 仍可用，critique 落 `unavailable`
（EXTERNAL CREDENTIAL BLOCKED，§18/§48），绝不伪造 live 验证。
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, sessionmaker

from jobfit.api.deps import (
    get_optional_llm_provider,
    get_session,
    get_session_factory,
    get_settings_dep,
)
from jobfit.api.errors import ERROR_RESPONSES
from jobfit.api.schemas import (
    CritiqueRead,
    Phase4RunRead,
    ReportBuildRead,
    ReportRead,
    ReviewRead,
    ReviewRequest,
)
from jobfit.config.settings import Settings
from jobfit.core.enums import ReviewDecision
from jobfit.core.errors import NotFound, ValidationFailed
from jobfit.db.repositories import analyses as analyses_repo
from jobfit.db.repositories import critiques as critiques_repo
from jobfit.db.repositories import reports as reports_repo
from jobfit.db.repositories import reviews as reviews_repo
from jobfit.llm.provider import LLMProvider
from jobfit.reports.service import build_and_validate_report
from jobfit.review.service import apply_review
from jobfit.workflow.runner import run_phase4_pipeline

router = APIRouter(tags=["phase4"], responses=ERROR_RESPONSES)


def _require_analysis(session: Session, analysis_id: uuid.UUID) -> None:
    if analyses_repo.get_analysis(session, analysis_id) is None:
        raise NotFound(f"analysis {analysis_id} not found")


# ---------------------------------------------------------------- critique


@router.post("/analyses/{analysis_id}/critique", response_model=Phase4RunRead)
async def trigger_critique_endpoint(
    analysis_id: uuid.UUID,
    session_factory: sessionmaker[Session] = Depends(get_session_factory),
    settings: Settings = Depends(get_settings_dep),
    provider: LLMProvider | None = Depends(get_optional_llm_provider),
) -> Phase4RunRead:
    """触发 Phase 4 管线：succeeded -> (critique -> report -> awaiting_review)（§39）。

    幂等：只对 `succeeded` 生效（claim_phase4 原子领取）；analysis 不存在 => 404；
    已进入 awaiting_review/finalized/rejected 或并发领取 => 409，不产生副作用。

    provider 缺失（未配置凭证）=> `None`，critique 落 `unavailable`
    （EXTERNAL CREDENTIAL BLOCKED），绝不伪造 live 验证。
    """
    with session_factory() as session:
        _require_analysis(session, analysis_id)
    result = await run_phase4_pipeline(
        session_factory=session_factory,
        settings=settings,
        provider=provider,
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
    return Phase4RunRead(
        analysis_id=analysis_id,
        status=result.status,
        critique_status=result.critique_status,
        critique_validation=result.critique_validation,
        report_version=result.report_version,
        report_stage=result.report_stage,
        errors=result.errors,
    )


@router.get("/analyses/{analysis_id}/critique", response_model=CritiqueRead)
def get_latest_critique(
    analysis_id: uuid.UUID,
    session: Session = Depends(get_session),
) -> CritiqueRead:
    _require_analysis(session, analysis_id)
    critique = critiques_repo.get_latest_critique(session, analysis_id)
    if critique is None:
        raise NotFound(f"analysis {analysis_id} has no critique yet")
    return CritiqueRead.model_validate(critique)


@router.get("/analyses/{analysis_id}/critiques", response_model=list[CritiqueRead])
def list_critiques(
    analysis_id: uuid.UUID,
    session: Session = Depends(get_session),
) -> list[CritiqueRead]:
    _require_analysis(session, analysis_id)
    return [
        CritiqueRead.model_validate(row)
        for row in critiques_repo.list_critiques(session, analysis_id)
    ]


# ---------------------------------------------------------------- report


@router.post(
    "/analyses/{analysis_id}/report", response_model=ReportBuildRead, responses=ERROR_RESPONSES
)
def build_report_endpoint(
    analysis_id: uuid.UUID,
    session_factory: sessionmaker[Session] = Depends(get_session_factory),
) -> ReportBuildRead:
    """重新装配并校验报告（API 路径，无 claim；§23/§24）。

    前置条件：critique 已存在（报告 = 确定性装配 + 已校验 critique 解释层）；
    analysis 须在 succeeded/awaiting_review 且无 final 报告（final 不可再改，§25）。
    校验失败 => 报告以 draft 留档（audit），不发布为 validated。
    """
    with session_factory() as session:
        _require_analysis(session, analysis_id)
        if critiques_repo.get_latest_critique(session, analysis_id) is None:
            raise ValidationFailed(
                "no critique yet; run POST /analyses/{id}/critique first (§23)"
            )
    outcome = build_and_validate_report(session_factory, analysis_id=analysis_id)
    assert outcome.report is not None
    return ReportBuildRead(
        analysis_id=analysis_id,
        version=outcome.report.version,
        stage=outcome.report.stage,
        valid=outcome.valid,
        reused=outcome.reused,
        issues=outcome.issues,
    )


@router.get("/analyses/{analysis_id}/report", response_model=ReportRead)
def get_latest_report(
    analysis_id: uuid.UUID,
    session: Session = Depends(get_session),
) -> ReportRead:
    _require_analysis(session, analysis_id)
    report = reports_repo.get_latest_report(session, analysis_id)
    if report is None:
        raise NotFound(f"analysis {analysis_id} has no report yet")
    return ReportRead.model_validate(report)


@router.get("/analyses/{analysis_id}/reports", response_model=list[ReportRead])
def list_reports(
    analysis_id: uuid.UUID,
    session: Session = Depends(get_session),
) -> list[ReportRead]:
    _require_analysis(session, analysis_id)
    return [
        ReportRead.model_validate(row)
        for row in reports_repo.list_reports(session, analysis_id)
    ]


# ---------------------------------------------------------------- review


@router.post("/analyses/{analysis_id}/review", response_model=ReviewRead)
def review_endpoint(
    analysis_id: uuid.UUID,
    body: ReviewRequest,
    session_factory: sessionmaker[Session] = Depends(get_session_factory),
) -> ReviewRead:
    """执行一次 reviewer action（§28–§31）：approve / reject / request_changes。

    - 状态机 guard：非法转移 => 422；并发抢占 => 409（单条条件 UPDATE 原子裁决）；
    - approve 要求存在 validated 报告且 critique 非 rejected（§32）；
    - 每次 action 落一行 review + audit_log（谁/何时/改了什么/依据什么 comment）。
    """
    outcome = apply_review(
        session_factory,
        analysis_id=analysis_id,
        decision=body.decision,
        comments=body.comments,
        reviewed_by=body.reviewed_by,
        overrides=body.overrides,
    )
    assert outcome.review is not None
    return ReviewRead.model_validate(outcome.review)


@router.post("/analyses/{analysis_id}/finalize", response_model=ReviewRead)
def finalize_endpoint(
    analysis_id: uuid.UUID,
    body: ReviewRequest | None = None,
    session_factory: sessionmaker[Session] = Depends(get_session_factory),
) -> ReviewRead:
    """快捷 finalize（= approve）：report validated -> final + analysis -> finalized（§32）。

    body.decision 被忽略，固定 approve；可选 comments / reviewed_by。
    """
    return review_endpoint(
        analysis_id,
        ReviewRequest(
            decision=ReviewDecision.APPROVE.value,
            comments=body.comments if body else None,
            reviewed_by=body.reviewed_by if body else None,
            overrides=body.overrides if body else None,
        ),
        session_factory,
    )


@router.post(
    "/analyses/{analysis_id}/reject", response_model=ReviewRead, responses=ERROR_RESPONSES
)
def reject_endpoint(
    analysis_id: uuid.UUID,
    body: ReviewRequest | None = None,
    session_factory: sessionmaker[Session] = Depends(get_session_factory),
) -> ReviewRead:
    """快捷 reject（= reject）：analysis -> rejected。必须给出 rejection reason（§33）。

    body.comments 必填（rejection reason）；body.decision 被忽略，固定 reject。
    """
    if body is None or not (body.comments or "").strip():
        raise ValidationFailed("rejection requires a comments/reason field (§33)")
    return review_endpoint(
        analysis_id,
        ReviewRequest(
            decision=ReviewDecision.REJECT.value,
            comments=body.comments,
            reviewed_by=body.reviewed_by,
            overrides=body.overrides,
        ),
        session_factory,
    )


@router.get(
    "/analyses/{analysis_id}/reviews",
    response_model=list[ReviewRead],
    responses=ERROR_RESPONSES,
)
def list_reviews(
    analysis_id: uuid.UUID,
    session: Session = Depends(get_session),
) -> list[ReviewRead]:
    _require_analysis(session, analysis_id)
    return [
        ReviewRead.model_validate(row) for row in reviews_repo.list_reviews(session, analysis_id)
    ]


__all__ = ["router"]

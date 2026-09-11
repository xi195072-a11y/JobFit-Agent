"""Review 编排服务（Phase 4 §27–§33）。

reviewer action 必须 deterministic——**LLM 从不自动 finalized**（§27）。

并发安全：analyses 上的条件 UPDATE 是原子的（§30），两次并发 approve 只有一个生效，
败者收到 Conflict（不产生 double finalize / lost update）。

审计（§31）：每次 review action 落一行 reviews + 一条 audit_log（谁/何时/把什么
状态改成什么/依据什么 comment）。不记录 reviewer 敏感信息。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from jobfit.core.enums import ReviewDecision
from jobfit.core.errors import Conflict, NotFound, ValidationFailed
from jobfit.db import models
from jobfit.db.repositories import analyses as analyses_repo
from jobfit.db.repositories import reports as reports_repo
from jobfit.db.repositories import reviews as reviews_repo
from jobfit.observability.logging import get_logger
from jobfit.review.state import allowed_transition

_LOG = get_logger(name="jobfit.review")


@dataclass
class ReviewOutcome:
    analysis_id: uuid.UUID
    from_state: str
    to_state: str
    decision: str
    review: models.Review | None = None
    report_stage: str | None = None
    issues: list[str] = field(default_factory=list)


def apply_review(
    session_factory: sessionmaker[Session],
    *,
    analysis_id: uuid.UUID,
    decision: str,
    comments: str | None = None,
    reviewed_by: str | None = None,
    overrides: dict[str, Any] | None = None,
) -> ReviewOutcome:
    """执行一次 reviewer action（原子转移 + 审计）。非法转移 => ValidationFailed。"""
    with session_factory() as session:
        analysis = analyses_repo.get_analysis(session, analysis_id)
        if analysis is None:
            raise NotFound(f"analysis {analysis_id} not found")
        from_state = analysis.status
        to_state = allowed_transition(from_state, decision)
        if to_state is None:
            raise ValidationFailed(
                f"illegal review transition: {from_state} --{decision}--> ? (§29 状态机)"
            )

        issues: list[str] = []
        report_stage: str | None = None
        if decision == ReviewDecision.APPROVE.value:
            ready, reason = reviews_repo.finalize_ready(session, analysis_id=analysis_id)
            if not ready:
                raise ValidationFailed(f"cannot finalize: {reason} (§32)")

        updated = reviews_repo.transition_analysis(
            session, analysis_id=analysis_id, from_state=from_state, to_state=to_state
        )
        if not updated:
            session.rollback()
            raise Conflict(
                f"review lost race: analysis {analysis_id} no longer in {from_state}; "
                "another reviewer already transitioned it"
            )

        if decision == ReviewDecision.APPROVE.value:
            report = reports_repo.get_latest_report(session, analysis_id)
            assert report is not None and report.stage == "validated"
            if reports_repo.set_report_stage(
                session, report_id=report.id, stage="final", expected_stage="validated"
            ):
                report_stage = "final"

        review = reviews_repo.insert_review(
            session,
            analysis_id=analysis_id,
            decision=decision,
            comments=comments,
            reviewed_by=reviewed_by,
            from_state=from_state,
            to_state=to_state,
            overrides=overrides,
        )
        reviews_repo.audit(
            session,
            actor=reviewed_by or "reviewer",
            action="review",
            entity_id=analysis_id,
            detail={
                "from_state": from_state,
                "to_state": to_state,
                "decision": decision,
                "comments": comments,
                "report_stage": report_stage,
            },
        )
    _LOG.info(
        "review_applied",
        analysis_id=str(analysis_id),
        decision=decision,
        from_state=from_state,
        to_state=to_state,
        report_stage=report_stage,
    )
    return ReviewOutcome(
        analysis_id=analysis_id,
        from_state=from_state,
        to_state=to_state,
        decision=decision,
        review=review,
        report_stage=report_stage,
        issues=issues,
    )


def requeue_rejected(session_factory: sessionmaker[Session], *, analysis_id: uuid.UUID) -> ReviewOutcome:
    """rejected -> queued：显式重排为新 execution（§33），旧结果保留审计。"""
    return apply_review(
        session_factory,
        analysis_id=analysis_id,
        decision=ReviewDecision.REQUEST_CHANGES.value,
        comments="requeue after rejection",
        reviewed_by="system",
    )


__all__ = ["ReviewOutcome", "apply_review", "requeue_rejected"]

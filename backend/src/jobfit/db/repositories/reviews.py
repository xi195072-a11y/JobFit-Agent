"""reviews 持久化 + analyses HITL 状态转移（Phase 4 §27–§31）。

- 转移 = 单条**条件 UPDATE**（原子）：一次只有一个 reviewer 生效（§30 并发安全）；
- 每次 review action 插入一行 review（append-only audit）+ 一条 audit_log（§31）；
- 不存 reviewer 密码/凭据；不记录敏感信息。
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from jobfit.db import models
from jobfit.db.repositories import critiques as critiques_repo
from jobfit.db.repositories import reports as reports_repo


def transition_analysis(
    session: Session, *, analysis_id: uuid.UUID, from_state: str, to_state: str
) -> bool:
    """条件 UPDATE：只有当前状态 == from_state 才转移。0 行 => 已被他人抢先/非法。"""
    clear_claim = to_state == "queued"
    sql = """
        UPDATE analyses
           SET status = :to_state,
               current_phase = :to_state,
               claim_token = CASE WHEN :clear_claim THEN NULL ELSE claim_token END,
               claimed_by = CASE WHEN :clear_claim THEN NULL ELSE claimed_by END,
               lease_expires_at = CASE WHEN :clear_claim THEN NULL ELSE lease_expires_at END,
               last_heartbeat_at = CASE WHEN :clear_claim THEN NULL ELSE last_heartbeat_at END
         WHERE id = :analysis_id
           AND status = :from_state
    """
    res = session.execute(
        text(sql),
        {
            "to_state": to_state,
            "clear_claim": clear_claim,
            "analysis_id": analysis_id,
            "from_state": from_state,
        },
    )
    assert isinstance(res, CursorResult)
    return bool(res.rowcount and res.rowcount > 0)


def insert_review(
    session: Session,
    *,
    analysis_id: uuid.UUID,
    decision: str,
    comments: str | None,
    reviewed_by: str | None,
    from_state: str,
    to_state: str,
    overrides: dict[str, Any] | None = None,
) -> models.Review:
    row = models.Review(
        analysis_id=analysis_id,
        decision=decision,
        comments=comments,
        overrides=overrides,
        reviewed_by=reviewed_by,
        from_state=from_state,
        to_state=to_state,
        version=1,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def audit(
    session: Session,
    *,
    actor: str,
    action: str,
    entity_id: uuid.UUID | None,
    detail: dict[str, Any] | None = None,
) -> None:
    session.add(models.AuditLog(actor=actor, action=action, entity_id=entity_id, detail=detail))
    session.commit()


def list_reviews(session: Session, analysis_id: uuid.UUID) -> list[models.Review]:
    from sqlalchemy import select

    return list(
        session.execute(
            select(models.Review)
            .where(models.Review.analysis_id == analysis_id)
            .order_by(models.Review.created_at)
        )
        .scalars()
        .all()
    )


def finalize_ready(session: Session, *, analysis_id: uuid.UUID) -> tuple[bool, str | None]:
    """finalize 前置检查（§32）：返回 (ready, 原因)。

    ready 条件：存在 validated 报告（可发布为 final）+ critique 非 rejected
    （critique 缺失 / unavailable / pending 视为可通过——unavailable 是独立的
    EXTERNAL CREDENTIAL BLOCKED 状态，不阻止 finalize；rejected 才是 invalid）。
    """
    report = reports_repo.get_latest_report(session, analysis_id)
    if report is None or report.stage != "validated":
        return False, "no validated report to finalize"
    critique = critiques_repo.get_latest_critique(session, analysis_id)
    if critique is not None and critique.validation_status == "rejected":
        return False, "critique validation rejected; cannot finalize (§32)"
    return True, None


def mark_awaiting_review(
    session: Session,
    *,
    analysis_id: uuid.UUID,
    claim_token: uuid.UUID,
    phase: str,
) -> bool:
    """fenced：running -> awaiting_review（Phase 4 终态，清空 lease/claim）。"""
    res = session.execute(
        text(
            """
            UPDATE analyses
               SET status = 'awaiting_review',
                   current_phase = :phase,
                   claim_token = NULL,
                   claimed_by = NULL,
                   lease_expires_at = NULL,
                   last_heartbeat_at = NULL
             WHERE id = :analysis_id
               AND claim_token = :claim_token
               AND status = 'running'
               AND lease_expires_at > clock_timestamp()
            """
        ),
        {"phase": phase, "analysis_id": analysis_id, "claim_token": claim_token},
    )
    assert isinstance(res, CursorResult)
    updated = bool(res.rowcount and res.rowcount > 0)
    session.commit()
    return updated


__all__ = [
    "audit",
    "finalize_ready",
    "insert_review",
    "list_reviews",
    "mark_awaiting_review",
    "transition_analysis",
]

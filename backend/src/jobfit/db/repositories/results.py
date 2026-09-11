"""analysis-scoped 结果写入/读取（Phase 3 §7/§25/§30）。

写入铁律：
1. **每次写入前先做 fencing 断言**（四条件 + `FOR UPDATE`）。0 行 => 调用方必须抛 LeaseLost
   并停止后续业务写入（§7）。
2. 幂等 identity 由 UNIQUE constraint 承担：`INSERT ... ON CONFLICT DO NOTHING`，
   禁止 SELECT-then-INSERT（§25，禁止 TOCTOU）。
3. 所有判定使用 `clock_timestamp()`；不在 Python 侧比较时间。
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from jobfit.core.enums import ScoreKind
from jobfit.db import models
from jobfit.matching.constraints import ConstraintOutcome
from jobfit.matching.score import ScoreResult
from jobfit.matching.skills import SkillMatchOutcome

# 四条件 + 行锁：写入期间阻止 recover_stale 把本行重新入队（clock_timestamp 语义）。
GUARD_LEASE_SQL = text(
    """
    SELECT 1 FROM analyses
     WHERE id = :analysis_id
       AND claim_token = :claim_token
       AND status = 'running'
       AND lease_expires_at > clock_timestamp()
     FOR UPDATE
    """
)

MARK_SUCCEEDED_SQL = text(
    """
    UPDATE analyses
       SET status = 'succeeded',
           current_phase = :phase,
           lease_expires_at = NULL
     WHERE id = :analysis_id
       AND claim_token = :claim_token
       AND status = 'running'
       AND lease_expires_at > clock_timestamp()
    """
)


def guard_lease(session: Session, *, analysis_id: uuid.UUID, claim_token: uuid.UUID) -> bool:
    """fencing 断言：True = 仍持有有效 lease。"""
    row = session.execute(
        GUARD_LEASE_SQL, {"analysis_id": analysis_id, "claim_token": claim_token}
    ).first()
    return row is not None


def mark_succeeded(
    session: Session, *, analysis_id: uuid.UUID, claim_token: uuid.UUID, phase: str
) -> bool:
    """fenced 成功终态；0 行 => 已失去 lease，结果不得被视为有效。"""
    res = session.execute(
        MARK_SUCCEEDED_SQL,
        {"phase": phase, "analysis_id": analysis_id, "claim_token": claim_token},
    )
    assert isinstance(res, CursorResult)
    updated = bool(res.rowcount and res.rowcount > 0)
    session.commit()
    return updated


def upsert_constraint_results(
    session: Session,
    *,
    analysis_id: uuid.UUID,
    ruleset_version: str,
    outcomes: Sequence[ConstraintOutcome],
) -> None:
    if not outcomes:
        return
    rows: list[dict[str, Any]] = [
        {
            "analysis_id": analysis_id,
            "requirement_id": item.requirement_id,
            "constraint_type": item.req_type,
            "result": item.verdict.value,
            "basis": item.basis.value,
            "ruleset_version": ruleset_version,
            "reason_code": item.reason_code,
            "evidence_ids": list(item.evidence_ids),
            "note": item.explanation,
        }
        for item in outcomes
    ]
    session.execute(
        pg_insert(models.HardConstraintResult)
        .values(rows)
        .on_conflict_do_nothing(index_elements=["analysis_id", "requirement_id", "ruleset_version"])
    )
    session.commit()


def upsert_skill_match_results(
    session: Session,
    *,
    analysis_id: uuid.UUID,
    ruleset_version: str,
    outcomes: Sequence[SkillMatchOutcome],
    trace_ids: dict[tuple[str, str], uuid.UUID] | None = None,
) -> None:
    if not outcomes:
        return
    rows: list[dict[str, Any]] = []
    for item in outcomes:
        trace_id = None
        if trace_ids is not None:
            trace_id = trace_ids.get(("skill_match", f"skill:{item.requirement_id}"))
        rows.append(
            {
                "analysis_id": analysis_id,
                "jd_requirement_id": item.requirement_id,
                "resume_skill_id": item.resume_skill_id,
                "status": item.status.value,
                "ruleset_version": ruleset_version,
                "norm_used": item.norm_used,
                "score_contribution": item.credit,
                "evidence_ids": list(item.evidence_ids),
                "trace_id": trace_id,
            }
        )
    session.execute(
        pg_insert(models.SkillMatchResult)
        .values(rows)
        .on_conflict_do_nothing(
            index_elements=["analysis_id", "jd_requirement_id", "ruleset_version"]
        )
    )
    session.commit()


def link_constraint_trace_ids(
    session: Session,
    *,
    analysis_id: uuid.UUID,
    ruleset_version: str,
    outcomes: Sequence[ConstraintOutcome],
    trace_ids: dict[tuple[str, str], uuid.UUID],
) -> None:
    """把 trace_id 回填到 constraint result（trace 行先写，随后关联）。"""
    for item in outcomes:
        trace_id = trace_ids.get(("constraint", f"req:{item.requirement_id}"))
        if trace_id is None:
            continue
        session.execute(
            text(
                """
                UPDATE hard_constraint_results SET trace_id = :trace_id
                 WHERE analysis_id = :analysis_id
                   AND requirement_id = :requirement_id
                   AND ruleset_version = :ruleset_version
                """
            ),
            {
                "trace_id": trace_id,
                "analysis_id": analysis_id,
                "requirement_id": item.requirement_id,
                "ruleset_version": ruleset_version,
            },
        )
    session.commit()


def upsert_score_snapshot(
    session: Session,
    *,
    analysis_id: uuid.UUID,
    score: ScoreResult,
    kind: str = ScoreKind.BASE.value,
) -> None:
    session.execute(
        pg_insert(models.ScoreSnapshot)
        .values(
            analysis_id=analysis_id,
            kind=kind,
            total=score.total,
            per_section=[item.as_dict() for item in score.per_section],
            flags=list(score.flags),
            scoring_version=score.scoring_version,
            hypothesis=None,
        )
        .on_conflict_do_nothing(index_elements=["analysis_id", "kind"])
    )
    session.commit()


# ------------------------------------------------------------------ 读取


def list_constraint_results(
    session: Session, analysis_id: uuid.UUID
) -> list[models.HardConstraintResult]:
    return list(
        session.execute(
            select(models.HardConstraintResult)
            .where(models.HardConstraintResult.analysis_id == analysis_id)
            .order_by(models.HardConstraintResult.constraint_type, models.HardConstraintResult.id)
        )
        .scalars()
        .all()
    )


def list_skill_match_results(
    session: Session, analysis_id: uuid.UUID
) -> list[models.SkillMatchResult]:
    return list(
        session.execute(
            select(models.SkillMatchResult)
            .where(models.SkillMatchResult.analysis_id == analysis_id)
            .order_by(models.SkillMatchResult.jd_requirement_id)
        )
        .scalars()
        .all()
    )


def list_traces(session: Session, analysis_id: uuid.UUID) -> list[models.DecisionTrace]:
    return list(
        session.execute(
            select(models.DecisionTrace)
            .where(models.DecisionTrace.analysis_id == analysis_id)
            .order_by(models.DecisionTrace.decision_type, models.DecisionTrace.decision_key)
        )
        .scalars()
        .all()
    )


def get_score_snapshot(
    session: Session, analysis_id: uuid.UUID, kind: str = ScoreKind.BASE.value
) -> models.ScoreSnapshot | None:
    return session.execute(
        select(models.ScoreSnapshot).where(
            models.ScoreSnapshot.analysis_id == analysis_id,
            models.ScoreSnapshot.kind == kind,
        )
    ).scalar_one_or_none()


def count_analysis_results(session: Session, analysis_id: uuid.UUID) -> dict[str, int]:
    from sqlalchemy import func

    def _count(model: Any, column: Any) -> int:
        return int(
            session.execute(
                select(func.count()).select_from(model).where(column == analysis_id)
            ).scalar_one()
        )

    return {
        "constraints": _count(models.HardConstraintResult, models.HardConstraintResult.analysis_id),
        "skill_matches": _count(models.SkillMatchResult, models.SkillMatchResult.analysis_id),
        "traces": _count(models.DecisionTrace, models.DecisionTrace.analysis_id),
        "scores": _count(models.ScoreSnapshot, models.ScoreSnapshot.analysis_id),
    }


def requeue_analysis(session: Session, analysis_id: uuid.UUID) -> bool:
    """把 failed / succeeded 的行显式重排为 queued（供人工/测试触发的重跑）。

    重跑仍走同一条 claim→fencing 路径，结果写入为幂等 upsert，因此不会产生重复结果行。
    """
    res = session.execute(
        text(
            """
            UPDATE analyses SET status = 'queued', current_phase = 'requeued',
                   claim_token = NULL, claimed_by = NULL,
                   lease_expires_at = NULL, last_heartbeat_at = NULL
             WHERE id = :analysis_id AND status IN ('failed', 'succeeded')
            """
        ),
        {"analysis_id": analysis_id},
    )
    assert isinstance(res, CursorResult)
    updated = bool(res.rowcount and res.rowcount > 0)
    session.commit()
    return updated

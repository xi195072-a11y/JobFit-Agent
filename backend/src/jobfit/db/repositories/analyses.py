"""analysis-scoped / document-scoped 写原语。

fencing 四条件：analysis_id + claim_token + status='running' +
lease_expires_at > clock_timestamp()（architecture.md §4.3 第 2/13 条）。

本模块所有 lease 判定使用 clock_timestamp()；不使用数据库本地时间函数。
禁止在此类短事务内执行 LLM/parser/外部 HTTP。
"""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session

from jobfit.core.enums import AnalysisStatus
from jobfit.core.errors import ReservationFailed
from jobfit.db import models

RESERVE_SQL = """
UPDATE analyses
   SET llm_attempts_used = llm_attempts_used + 1
 WHERE id = :analysis_id
   AND claim_token = :claim_token
   AND status = 'running'
   AND lease_expires_at > clock_timestamp()
   AND llm_attempts_used < :max_llm_attempts
 RETURNING llm_attempts_used
"""

HEARTBEAT_SQL = """
UPDATE analyses
   SET last_heartbeat_at = clock_timestamp(),
       lease_expires_at = clock_timestamp() + make_interval(secs => :ttl_seconds)
 WHERE id = :analysis_id
   AND claim_token = :claim_token
   AND status = 'running'
   AND lease_expires_at > clock_timestamp()
"""

CLAIM_SQL = """
UPDATE analyses
   SET status = 'running',
       claimed_by = :worker_id,
       claim_token = gen_random_uuid(),
       lease_expires_at = clock_timestamp() + make_interval(secs => :ttl_seconds),
       run_attempts = run_attempts + 1
 WHERE id = (
     SELECT id FROM analyses
      WHERE status = 'queued'
      ORDER BY created_at
      FOR UPDATE SKIP LOCKED
      LIMIT 1
 )
 RETURNING id, claim_token, lease_expires_at
"""

RECOVER_STALE_SQL = """
UPDATE analyses
   SET status = 'queued',
       claim_token = NULL,
       claimed_by = NULL,
       last_heartbeat_at = NULL,
       lease_expires_at = NULL,
       requeue_count = requeue_count + 1
 WHERE status = 'running'
   AND lease_expires_at <= clock_timestamp()
 RETURNING id
"""


def claim_next(session: Session, worker_id: str, ttl_seconds: int) -> models.Analysis | None:
    """原子领取一条 queued（SKIP LOCKED），生成新 claim_token 并续租。

    Phase 1 只提供 database primitive；完整 worker loop 属于后续 Phase。
    """
    row = session.execute(
        text(CLAIM_SQL), {"worker_id": worker_id, "ttl_seconds": ttl_seconds}
    ).first()
    if row is None:
        return None
    session.commit()
    analysis = session.get(models.Analysis, row.id)
    assert analysis is not None
    return analysis


def heartbeat(session: Session, analysis_id: uuid.UUID, claim_token: uuid.UUID, ttl_seconds: int) -> bool:
    """续租（四条件）。返回 False = 心跳 0-row => 已失去该 job，必须停止执行。"""
    from sqlalchemy.engine import CursorResult

    res = session.execute(
        text(HEARTBEAT_SQL),
        {"analysis_id": analysis_id, "claim_token": claim_token, "ttl_seconds": ttl_seconds},
    )
    assert isinstance(res, CursorResult)
    updated = bool(res.rowcount and res.rowcount > 0)
    session.commit()
    return updated


def recover_stale(session: Session) -> list[uuid.UUID]:
    """把 lease 已过期的 running 行重排为 queued（竞态见 architecture §3.3）。"""
    rows = session.execute(text(RECOVER_STALE_SQL)).all()
    session.commit()
    return [r.id for r in rows]


def reserve_llm_attempt(
    session: Session,
    *,
    analysis_id: uuid.UUID,
    claim_token: uuid.UUID,
    max_llm_attempts: int,
    phase: str | None = None,
) -> int:
    """Attempt reservation（ADR-018 / architecture §4.4）。

    - 单条原子 guarded UPDATE，独立短事务，成功后立即 commit；
    - 0 行 => 预算耗尽或失去 lease/fencing => ReservationFailed；
    - reservation 不因后续 crash 回滚（此处已提交）；
    - 成功后写 llm_attempt_log（UNIQUE(analysis_id, attempt_no)）；
    - 返回 attempt_no（= 预占后的 llm_attempts_used）。
    """
    row = session.execute(
        text(RESERVE_SQL),
        {
            "analysis_id": analysis_id,
            "claim_token": claim_token,
            "max_llm_attempts": max_llm_attempts,
        },
    ).first()
    if row is None:
        session.rollback()
        raise ReservationFailed(
            f"analysis={analysis_id} reservation failed "
            "(budget exhausted or lease/fencing lost)"
        )
    attempt_no = int(row[0])
    session.add(
        models.LlmAttemptLog(analysis_id=analysis_id, attempt_no=attempt_no, phase=phase)
    )
    session.commit()
    return attempt_no


def create_analysis(
    session: Session,
    *,
    resume_document_id: uuid.UUID,
    jd_document_id: uuid.UUID,
    pipeline_version: str,
    extraction_schema_version: str,
    prompt_version: str | None,
    ruleset_version: str | None,
    scoring_version: str | None,
    llm_model: str,
    embedding_model: str | None,
    config_snapshot: dict,
    idempotency_key: str | None = None,
    resume_profile_id: uuid.UUID | None = None,
    jd_profile_id: uuid.UUID | None = None,
) -> models.Analysis:
    analysis = models.Analysis(
        resume_document_id=resume_document_id,
        jd_document_id=jd_document_id,
        resume_profile_id=resume_profile_id,
        jd_profile_id=jd_profile_id,
        status=AnalysisStatus.QUEUED.value,
        current_phase="queued",
        idempotency_key=idempotency_key,
        pipeline_version=pipeline_version,
        extraction_schema_version=extraction_schema_version,
        prompt_version=prompt_version,
        ruleset_version=ruleset_version,
        scoring_version=scoring_version,
        llm_model=llm_model,
        embedding_model=embedding_model,
        config_snapshot=config_snapshot,
    )
    session.add(analysis)
    session.commit()
    session.refresh(analysis)
    return analysis


def get_analysis(session: Session, analysis_id: uuid.UUID) -> models.Analysis | None:
    return session.get(models.Analysis, analysis_id)


def get_by_idempotency_key(session: Session, idempotency_key: str) -> models.Analysis | None:
    from sqlalchemy import select

    return session.execute(
        select(models.Analysis).where(models.Analysis.idempotency_key == idempotency_key)
    ).scalar_one_or_none()


def list_analyses(
    session: Session,
    *,
    limit: int = 20,
    offset: int = 0,
    status: str | None = None,
) -> tuple[list[models.Analysis], int]:
    """分页列出 analyses（Dashboard / Analysis list）。

    顺序**确定性**：`created_at DESC, id DESC`（同 created_at 时用 id 收敛，
    避免分页时顺序抖动，§45）。
    """
    from sqlalchemy import func, select

    filters = []
    if status is not None:
        filters.append(models.Analysis.status == status)
    total = session.execute(
        select(func.count()).select_from(models.Analysis).where(*filters)
    ).scalar_one()
    rows = list(
        session.execute(
            select(models.Analysis)
            .where(*filters)
            .order_by(models.Analysis.created_at.desc(), models.Analysis.id.desc())
            .limit(limit)
            .offset(offset)
        )
        .scalars()
        .all()
    )
    return rows, int(total)


CLAIM_SPECIFIC_SQL = """
UPDATE analyses
   SET status = 'running',
       claimed_by = :worker_id,
       claim_token = gen_random_uuid(),
       lease_expires_at = clock_timestamp() + make_interval(secs => :ttl_seconds),
       run_attempts = run_attempts + 1
 WHERE id = :analysis_id
   AND status = 'queued'
 RETURNING claim_token
"""

UPDATE_PHASE_SQL = """
UPDATE analyses
   SET current_phase = :phase
 WHERE id = :analysis_id
   AND claim_token = :claim_token
   AND status = 'running'
   AND lease_expires_at > clock_timestamp()
"""


def claim_specific(
    session: Session, *, analysis_id: uuid.UUID, worker_id: str, ttl_seconds: int
) -> uuid.UUID | None:
    """按 id 原子领取一条 queued analysis；0 行 => 已被他人 claim / 不可领取。"""
    row = session.execute(
        text(CLAIM_SPECIFIC_SQL),
        {"analysis_id": analysis_id, "worker_id": worker_id, "ttl_seconds": ttl_seconds},
    ).first()
    session.commit()
    if row is None:
        return None
    token = row[0]
    assert isinstance(token, uuid.UUID)
    return token


CLAIM_PHASE4_SQL = """
UPDATE analyses
   SET status = 'running',
       claimed_by = :worker_id,
       claim_token = gen_random_uuid(),
       lease_expires_at = clock_timestamp() + make_interval(secs => :ttl_seconds)
 WHERE id = :analysis_id
   AND status = 'succeeded'
 RETURNING claim_token
"""


def claim_phase4(
    session: Session, *, analysis_id: uuid.UUID, worker_id: str, ttl_seconds: int
) -> uuid.UUID | None:
    """Phase 4 续接：把已 succeeded 的 analysis 原子领回 running（新 claim_token）。

    用于 API 触发的 critique/report 生成（§39）：succeeded -> running ->（critique/
    report）-> awaiting_review。0 行 => 状态不允许（非 succeeded / 已被并发领取）。
    """
    row = session.execute(
        text(CLAIM_PHASE4_SQL),
        {"analysis_id": analysis_id, "worker_id": worker_id, "ttl_seconds": ttl_seconds},
    ).first()
    session.commit()
    if row is None:
        return None
    token = row[0]
    assert isinstance(token, uuid.UUID)
    return token


def update_phase(
    session: Session, *, analysis_id: uuid.UUID, claim_token: uuid.UUID, phase: str
) -> bool:
    """fenced 进度写入；0 行 => 失去 lease，调用方必须立刻停止执行。"""
    from sqlalchemy.engine import CursorResult

    res = session.execute(
        text(UPDATE_PHASE_SQL),
        {"phase": phase, "analysis_id": analysis_id, "claim_token": claim_token},
    )
    assert isinstance(res, CursorResult)
    updated = bool(res.rowcount and res.rowcount > 0)
    session.commit()
    return updated


MARK_FAILED_SQL = """
UPDATE analyses
   SET status = 'failed',
       current_phase = :phase
 WHERE id = :analysis_id
   AND claim_token = :claim_token
   AND status = 'running'
   AND lease_expires_at > clock_timestamp()
"""


def mark_failed(
    session: Session, *, analysis_id: uuid.UUID, claim_token: uuid.UUID, phase: str
) -> bool:
    """确定性失败终态（fenced）；0 行 => 已失去 lease，不得再写 analysis-scoped 状态。"""
    from sqlalchemy.engine import CursorResult

    res = session.execute(
        text(MARK_FAILED_SQL),
        {"phase": phase, "analysis_id": analysis_id, "claim_token": claim_token},
    )
    assert isinstance(res, CursorResult)
    updated = bool(res.rowcount and res.rowcount > 0)
    session.commit()
    return updated


def fenced_bind_profile(
    session: Session,
    *,
    analysis_id: uuid.UUID,
    claim_token: uuid.UUID,
    profile_kind: str,
    profile_id: uuid.UUID,
) -> bool:
    """把 artifact 绑定到 analyses（analysis-scoped guarded UPDATE，fencing 保护）。

    profile_kind ∈ {"resume", "jd"}。0 行 => 无权（stale/lost）=> False。
    """
    column = "resume_profile_id" if profile_kind == "resume" else "jd_profile_id"
    sql = f"""
    UPDATE analyses SET {column} = :profile_id
     WHERE id = :analysis_id
       AND claim_token = :claim_token
       AND status = 'running'
       AND lease_expires_at > clock_timestamp()
    """
    from sqlalchemy.engine import CursorResult

    res = session.execute(
        text(sql),
        {"profile_id": profile_id, "analysis_id": analysis_id, "claim_token": claim_token},
    )
    assert isinstance(res, CursorResult)
    bound = bool(res.rowcount and res.rowcount > 0)
    session.commit()
    return bound

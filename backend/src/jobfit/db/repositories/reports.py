"""reports 持久化（Phase 4 §22–§25）。

- 每代报告 = 新行：UNIQUE(analysis_id, version) + `ON CONFLICT DO NOTHING`；
- stage 只允许 draft → validated → final 单向推进（final 后不可再改）；
- 读取一律返回最新（version 最大）行。
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from jobfit.core.enums import ReportStage
from jobfit.db import models


def next_report_version(session: Session, analysis_id: uuid.UUID) -> int:
    rows = session.execute(
        select(models.Report.version).where(models.Report.analysis_id == analysis_id)
    ).all()
    return max((int(item[0]) for item in rows), default=0) + 1


def upsert_report(
    session: Session,
    *,
    analysis_id: uuid.UUID,
    version: int,
    stage: str,
    content: dict[str, Any],
    content_md: str,
    meta: dict[str, Any],
    fingerprint: str,
) -> bool:
    """幂等写入报告行；返回 True = 新建，False = 该 version 已存在（复用）。"""
    stmt = (
        pg_insert(models.Report)
        .values(
            analysis_id=analysis_id,
            version=version,
            stage=stage,
            content=content,
            content_md=content_md,
            meta=meta,
            fingerprint=fingerprint,
        )
        .on_conflict_do_nothing(index_elements=["analysis_id", "version"])
    )
    result = session.execute(stmt)
    assert isinstance(result, CursorResult)
    created = bool(result.rowcount and result.rowcount > 0)
    session.commit()
    return created


def get_latest_report(session: Session, analysis_id: uuid.UUID) -> models.Report | None:
    return session.execute(
        select(models.Report)
        .where(models.Report.analysis_id == analysis_id)
        .order_by(models.Report.version.desc(), models.Report.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def get_report(session: Session, analysis_id: uuid.UUID, version: int) -> models.Report | None:
    return session.execute(
        select(models.Report).where(
            models.Report.analysis_id == analysis_id, models.Report.version == version
        )
    ).scalar_one_or_none()


def list_reports(session: Session, analysis_id: uuid.UUID) -> list[models.Report]:
    return list(
        session.execute(
            select(models.Report)
            .where(models.Report.analysis_id == analysis_id)
            .order_by(models.Report.version)
        )
        .scalars()
        .all()
    )


def set_report_stage(
    session: Session,
    *,
    report_id: uuid.UUID,
    stage: str,
    expected_stage: str | None = None,
) -> bool:
    """单向推进 stage（final 不可再改）。0 行 => 非法推进或不存在。"""
    from sqlalchemy import text

    sql = """
        UPDATE reports
           SET stage = :stage,
               published_at = CASE WHEN :stage_is_final THEN clock_timestamp() ELSE published_at END,
               updated_at = clock_timestamp()
         WHERE id = :report_id
           AND stage <> 'final'
    """
    params: dict[str, Any] = {
        "stage": stage,
        "stage_is_final": stage == ReportStage.FINAL.value,
        "report_id": report_id,
    }
    if expected_stage is not None:
        sql += " AND stage = :expected_stage"
        params["expected_stage"] = expected_stage
    result = session.execute(text(sql), params)
    assert isinstance(result, CursorResult)
    updated = bool(result.rowcount and result.rowcount > 0)
    session.commit()
    return updated


def has_finalized_report(session: Session, analysis_id: uuid.UUID) -> bool:
    row = session.execute(
        select(models.Report.id)
        .where(
            models.Report.analysis_id == analysis_id,
            models.Report.stage == ReportStage.FINAL.value,
        )
        .limit(1)
    ).first()
    return row is not None


__all__ = [
    "get_latest_report",
    "get_report",
    "has_finalized_report",
    "list_reports",
    "next_report_version",
    "set_report_stage",
    "upsert_report",
]

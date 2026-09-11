"""critiques 持久化（Phase 4 §20/§21）。

幂等：UNIQUE(analysis_id, fingerprint) + `ON CONFLICT DO NOTHING`；
相同 analysis + critique 配置 + prompt + model => 同一 fingerprint => 不产生重复行。
写入必须由调用方在 fenced 事务内完成（critique service / workflow node 持有 lease）。
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from jobfit.db import models


def upsert_critique(
    session: Session,
    *,
    analysis_id: uuid.UUID,
    version: int,
    status: str,
    model: str,
    provider: str,
    prompt_version: str,
    schema_version: str,
    content: dict[str, Any],
    validation_status: str,
    citations_validated: bool,
    fingerprint: str,
    latency_ms: int | None = None,
    tokens_in: int | None = None,
    tokens_out: int | None = None,
) -> bool:
    """幂等写入 critique；返回 True = 新建，False = 已存在（同 fingerprint 复用）。"""
    stmt = (
        pg_insert(models.Critique)
        .values(
            analysis_id=analysis_id,
            version=version,
            status=status,
            model=model,
            provider=provider,
            prompt_version=prompt_version,
            schema_version=schema_version,
            content=content,
            validation_status=validation_status,
            citations_validated=citations_validated,
            fingerprint=fingerprint,
            latency_ms=latency_ms,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
        )
        .on_conflict_do_nothing(index_elements=["analysis_id", "fingerprint"])
    )
    result = session.execute(stmt)
    session.commit()
    from sqlalchemy.engine import CursorResult

    assert isinstance(result, CursorResult)
    return bool(result.rowcount and result.rowcount > 0)


def next_critique_version(session: Session, analysis_id: uuid.UUID) -> int:
    row = session.execute(
        select(models.Critique.version).where(models.Critique.analysis_id == analysis_id)
    ).all()
    return max((int(item[0]) for item in row), default=0) + 1


def get_latest_critique(
    session: Session, analysis_id: uuid.UUID
) -> models.Critique | None:
    return session.execute(
        select(models.Critique)
        .where(models.Critique.analysis_id == analysis_id)
        .order_by(models.Critique.version.desc(), models.Critique.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def list_critiques(session: Session, analysis_id: uuid.UUID) -> list[models.Critique]:
    return list(
        session.execute(
            select(models.Critique)
            .where(models.Critique.analysis_id == analysis_id)
            .order_by(models.Critique.version)
        )
        .scalars()
        .all()
    )


__all__ = [
    "get_latest_critique",
    "list_critiques",
    "next_critique_version",
    "upsert_critique",
]

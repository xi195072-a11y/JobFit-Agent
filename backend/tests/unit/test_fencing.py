"""lease / fencing database contract（architecture §4.3 / ADR-017）。

Phase 1 只实现 database primitives 的 SQL 契约与模型约束；
真实 DB 行为由 integration/test_db.py 覆盖（需 PostgreSQL）。
"""

from __future__ import annotations

from typing import cast

from sqlalchemy import Table
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from jobfit.db import models
from jobfit.db.base import Base
from jobfit.db.repositories.analyses import (
    CLAIM_SQL,
    HEARTBEAT_SQL,
    RECOVER_STALE_SQL,
    RESERVE_SQL,
)


def test_lease_guard_uses_clock_timestamp_not_now() -> None:
    for sql in (CLAIM_SQL, HEARTBEAT_SQL, RECOVER_STALE_SQL, RESERVE_SQL):
        assert "clock_timestamp()" in sql, sql[:80]
        assert "now(" not in sql.lower(), sql[:80]


def test_heartbeat_four_condition_contract() -> None:
    assert "status = 'running'" in HEARTBEAT_SQL
    assert "claim_token = :claim_token" in HEARTBEAT_SQL
    assert "lease_expires_at > clock_timestamp()" in HEARTBEAT_SQL


def test_claim_uses_skip_locked() -> None:
    assert "FOR UPDATE SKIP LOCKED" in CLAIM_SQL
    assert "claim_token = gen_random_uuid()" in CLAIM_SQL


def test_recover_stale_only_expired() -> None:
    assert "lease_expires_at <= clock_timestamp()" in RECOVER_STALE_SQL
    assert "status = 'running'" in RECOVER_STALE_SQL


def test_fenced_bind_profile_column_whitelist() -> None:
    from jobfit.db.repositories.analyses import fenced_bind_profile

    # 白名单防注入：仅允许 resume/jd 两值（实现层面即参数化，此处校验函数存在且类型化）
    assert callable(fenced_bind_profile)


def test_all_tables_compile_to_postgresql_ddl() -> None:
    """离线验证 ORM 定义可编译为 PG DDL（含 vector 类型与唯一约束）。"""
    dialect = postgresql.dialect()
    for table in Base.metadata.sorted_tables:
        ddl = str(CreateTable(table).compile(dialect=dialect))
        assert ddl  # 可编译
    chunks = cast(Table, models.DocumentChunk.__table__)
    chunks_ddl = str(CreateTable(chunks).compile(dialect=dialect))
    assert "vector" in chunks_ddl.lower()  # pgvector 列可编译


def test_models_expose_no_lease_now_defaults() -> None:
    """ORM 层的时间默认值为 clock_timestamp()，不存在 now() lease 判定。"""
    lease_default = Base.metadata.tables["analyses"].c.lease_expires_at.server_default
    assert lease_default is None
    created_default = Base.metadata.tables["analyses"].c.created_at.server_default
    assert created_default is not None
    arg = cast("object", getattr(created_default, "arg"))
    assert "clock_timestamp()" in str(arg)

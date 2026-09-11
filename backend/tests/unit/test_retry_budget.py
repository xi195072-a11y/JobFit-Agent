"""retry budget（ADR-018）—— attempt reservation 契约与模型约束。"""

from __future__ import annotations

from typing import cast

from sqlalchemy import Table, UniqueConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from jobfit.db import models
from jobfit.db.repositories.analyses import RESERVE_SQL


def test_reservation_before_call_contract() -> None:
    """reservation 是单条 guarded UPDATE：budget 上界 + lease guard + 单调 +1。"""
    assert "SET llm_attempts_used = llm_attempts_used + 1" in RESERVE_SQL
    assert "llm_attempts_used < :max_llm_attempts" in RESERVE_SQL
    assert "status = 'running'" in RESERVE_SQL
    assert "claim_token = :claim_token" in RESERVE_SQL
    assert "lease_expires_at > clock_timestamp()" in RESERVE_SQL
    assert "now(" not in RESERVE_SQL.lower()
    assert "RETURNING llm_attempts_used" in RESERVE_SQL


def test_llm_attempt_log_unique_contract() -> None:
    table = cast(Table, models.LlmAttemptLog.__table__)
    uniques = [c for c in table.constraints if isinstance(c, UniqueConstraint)]
    cols = {tuple(sorted(u.columns.keys())) for u in uniques}
    assert ("analysis_id", "attempt_no") in cols


def test_analyses_budget_columns_present() -> None:
    cols = set(cast(Table, models.Analysis.__table__).columns.keys())
    assert {"llm_attempts_used", "llm_budget_exceeded", "claim_token", "lease_expires_at"} <= cols


def test_attempt_log_ddl_compiles() -> None:
    table = cast(Table, models.LlmAttemptLog.__table__)
    ddl = str(CreateTable(table).compile(dialect=postgresql.dialect()))
    assert "UNIQUE (analysis_id, attempt_no)" in ddl or "uq_llm_attempt_log" in ddl

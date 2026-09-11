"""phase4: critique/report/review lifecycle schema

Revision ID: 0003_phase4_critique_report
Revises: 0002_phase3_analysis_results
Create Date: 2026-09-10

Phase 4 只补齐 HITL/批评链条真正缺失的 schema（不修改 baseline / 0002）：
1. `critiques` 补齐 version / provider / schema_version / validation_status / fingerprint，
   并新增 UNIQUE(analysis_id, fingerprint)（幂等 identity，§20/§21）。
2. `reports` 新增 fingerprint + updated_at；UNIQUE(analysis_id) -> UNIQUE(analysis_id, version)
   （每代报告 = 新行，final 不可覆盖，§25）。
3. `reviews` 新增 version（乐观锁审计）/ from_state / to_state / updated_at（§28/§31）。

analyses.status 的 awaiting_review/finalized/rejected 已在 baseline 的 check 中允许，无需改动。

注：revision id 必须 ≤ 32 字符——Alembic 的 `alembic_version.version_num` 为 VARCHAR(32)，
超长会在写入版本号时触发 `StringDataRightTruncation`。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_phase4_critique_report"
down_revision = "0002_phase3_analysis_results"
branch_labels = None
depends_on = None

_CLOCK = sa.text("clock_timestamp()")


def _add_not_null_column(
    table: str, column: str, type_: sa.types.TypeEngine, *, temporary_default: str
) -> None:
    """对既有表安全添加 NOT NULL 列：先给临时 server_default，再加约束后移除默认值。"""
    op.add_column(
        table,
        sa.Column(column, type_, nullable=False, server_default=sa.text(temporary_default)),
    )
    op.alter_column(table, column, server_default=None)


def upgrade() -> None:
    # ---------------------------------------------------------------- critiques
    _add_not_null_column("critiques", "version", sa.Integer(), temporary_default="1")
    _add_not_null_column("critiques", "provider", sa.String(length=128), temporary_default="''")
    _add_not_null_column(
        "critiques", "schema_version", sa.String(length=64), temporary_default="'critique.v1'"
    )
    _add_not_null_column(
        "critiques", "validation_status", sa.String(length=16), temporary_default="'pending'"
    )
    _add_not_null_column("critiques", "fingerprint", sa.String(length=128), temporary_default="''")
    op.create_unique_constraint(
        "uq_critique_fingerprint", "critiques", ["analysis_id", "fingerprint"]
    )

    # ---------------------------------------------------------------- reports
    _add_not_null_column("reports", "fingerprint", sa.String(length=128), temporary_default="''")
    op.add_column(
        "reports",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=_CLOCK,
        ),
    )
    op.drop_constraint("uq_reports_analysis_id", "reports", type_="unique")
    op.create_unique_constraint("uq_reports_analysis_version", "reports", ["analysis_id", "version"])
    op.create_index("ix_reports_analysis_id", "reports", ["analysis_id"])

    # ---------------------------------------------------------------- reviews
    _add_not_null_column("reviews", "version", sa.Integer(), temporary_default="1")
    op.add_column("reviews", sa.Column("from_state", sa.String(length=32), nullable=True))
    op.add_column("reviews", sa.Column("to_state", sa.String(length=32), nullable=True))
    op.add_column(
        "reviews",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=_CLOCK,
        ),
    )


def downgrade() -> None:
    # ---------------------------------------------------------------- reviews
    op.drop_column("reviews", "updated_at")
    op.drop_column("reviews", "to_state")
    op.drop_column("reviews", "from_state")
    op.drop_column("reviews", "version")

    # ---------------------------------------------------------------- reports
    op.drop_index("ix_reports_analysis_id", table_name="reports")
    op.drop_constraint("uq_reports_analysis_version", "reports", type_="unique")
    op.create_unique_constraint("uq_reports_analysis_id", "reports", ["analysis_id"])
    op.drop_column("reports", "updated_at")
    op.drop_column("reports", "fingerprint")

    # ---------------------------------------------------------------- critiques
    op.drop_constraint("uq_critique_fingerprint", "critiques", type_="unique")
    op.drop_column("critiques", "fingerprint")
    op.drop_column("critiques", "validation_status")
    op.drop_column("critiques", "schema_version")
    op.drop_column("critiques", "provider")
    op.drop_column("critiques", "version")

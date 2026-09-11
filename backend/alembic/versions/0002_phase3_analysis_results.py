"""phase3: deterministic analysis result identity

Revision ID: 0002_phase3_analysis_results
Revises: 0001_baseline
Create Date: 2026-09-10

Phase 3 只新增"确定性分析结果"真正缺失的 schema：
1. `analyses.status` 增加确定性成功终态 `succeeded`（Phase 3 §5 lifecycle）。
2. `hard_constraint_results` 增加 constraint_type / ruleset_version / reason_code，
   并把幂等 identity 扩展为 (analysis_id, requirement_id, ruleset_version)（§12/§25）。
3. `skill_match_results` 增加 ruleset_version + UNIQUE(analysis_id, jd_requirement_id, ruleset_version)。
4. `decision_traces` 增加 UNIQUE(analysis_id, decision_type, decision_key)：重放必须收敛到同一行。
5. `score_snapshots` 增加 UNIQUE(analysis_id, kind)：重放不重复累积。

不修改 baseline；本 migration 可 downgrade 回 baseline 状态。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_phase3_analysis_results"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None

_STATUSES_WITH_SUCCEEDED = (
    "status IN ('queued','running','succeeded','awaiting_review','finalized','rejected','failed')"
)
_STATUSES_PHASE2 = "status IN ('queued','running','awaiting_review','finalized','rejected','failed')"


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
    # ---------------------------------------------------------------- analyses.status
    op.drop_constraint("status_allowed", "analyses", type_="check")
    op.create_check_constraint("status_allowed", "analyses", _STATUSES_WITH_SUCCEEDED)

    # ---------------------------------------------------------------- hard_constraint_results
    _add_not_null_column(
        "hard_constraint_results", "constraint_type", sa.String(length=32), temporary_default="'other'"
    )
    _add_not_null_column(
        "hard_constraint_results", "ruleset_version", sa.String(length=128), temporary_default="''"
    )
    _add_not_null_column(
        "hard_constraint_results", "reason_code", sa.String(length=64), temporary_default="'UNSPECIFIED'"
    )
    op.drop_constraint("uq_hard_constraint_result", "hard_constraint_results", type_="unique")
    op.create_unique_constraint(
        "uq_hard_constraint_result",
        "hard_constraint_results",
        ["analysis_id", "requirement_id", "ruleset_version"],
    )

    # ---------------------------------------------------------------- skill_match_results
    _add_not_null_column(
        "skill_match_results", "ruleset_version", sa.String(length=128), temporary_default="''"
    )
    op.create_unique_constraint(
        "uq_skill_match_result",
        "skill_match_results",
        ["analysis_id", "jd_requirement_id", "ruleset_version"],
    )

    # ---------------------------------------------------------------- decision_traces
    op.create_unique_constraint(
        "uq_decision_trace",
        "decision_traces",
        ["analysis_id", "decision_type", "decision_key"],
    )

    # ---------------------------------------------------------------- score_snapshots
    op.create_unique_constraint(
        "uq_score_snapshot",
        "score_snapshots",
        ["analysis_id", "kind"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_score_snapshot", "score_snapshots", type_="unique")
    op.drop_constraint("uq_decision_trace", "decision_traces", type_="unique")

    op.drop_constraint("uq_skill_match_result", "skill_match_results", type_="unique")
    op.drop_column("skill_match_results", "ruleset_version")

    op.drop_constraint("uq_hard_constraint_result", "hard_constraint_results", type_="unique")
    op.drop_column("hard_constraint_results", "reason_code")
    op.drop_column("hard_constraint_results", "ruleset_version")
    op.drop_column("hard_constraint_results", "constraint_type")
    op.create_unique_constraint(
        "uq_hard_constraint_result",
        "hard_constraint_results",
        ["analysis_id", "requirement_id"],
    )

    op.drop_constraint("status_allowed", "analyses", type_="check")
    op.create_check_constraint("status_allowed", "analyses", _STATUSES_PHASE2)

"""Report service（Phase 4 §22–§24）：确定性装配 + 校验 + 持久化。

- 只使用 DB 中的确定性结果 + validated critique（§23）；
- 发布前必须 ReportValidator 通过（§24），失败 => stage=draft（audit 留档），不得发布；
- 写入前 fencing：worker 路径（claim_token）走 guard_lease；API 路径（无 claim）
  走 analysis 状态 guard + UNIQUE(analysis_id, version) 幂等。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from jobfit.core.enums import ReportStage
from jobfit.core.errors import LeaseLost, NotFound, ValidationFailed
from jobfit.db import models
from jobfit.db.repositories import analyses as analyses_repo
from jobfit.db.repositories import reports as reports_repo
from jobfit.db.repositories import results as results_repo
from jobfit.observability.logging import get_logger
from jobfit.reports.builder import build_report, load_report_inputs
from jobfit.reports.validator import ReportValidator

_LOG = get_logger(name="jobfit.reports")

API_REPORT_STATES = ("succeeded", "awaiting_review")


@dataclass
class ReportOutcome:
    report: models.Report | None = None
    valid: bool = False
    issues: list[str] = field(default_factory=list)
    reused: bool = False


def _guard_api_state(session: Session, analysis_id: uuid.UUID) -> models.Analysis:
    analysis = analyses_repo.get_analysis(session, analysis_id)
    if analysis is None:
        raise NotFound(f"analysis {analysis_id} not found")
    if analysis.status not in API_REPORT_STATES:
        raise ValidationFailed(
            f"report generation requires analysis in {API_REPORT_STATES}, got {analysis.status}"
        )
    if reports_repo.has_finalized_report(session, analysis_id):
        raise ValidationFailed("finalized report exists; finalized reports are immutable (§25)")
    return analysis


def build_and_validate_report(
    session_factory: sessionmaker[Session],
    *,
    analysis_id: uuid.UUID,
    claim_token: uuid.UUID | None = None,
) -> ReportOutcome:
    """构建 + 校验 + 持久化一份报告。

    - claim_token 非空（worker）：先 guard_lease（fenced）；
    - claim_token 为空（API）：状态 guard（succeeded/awaiting_review 且无 final 报告）。
    """
    with session_factory() as session:
        if claim_token is not None:
            if not results_repo.guard_lease(session, analysis_id=analysis_id, claim_token=claim_token):
                session.rollback()
                raise LeaseLost(
                    f"lease lost before report (analysis={analysis_id}); stale worker 不得写 report"
                )
        else:
            _guard_api_state(session, analysis_id)

        inputs = load_report_inputs(session, analysis_id=analysis_id)
        built = build_report(inputs)
        validation = ReportValidator().validate(session, built=built, analysis_id=analysis_id)

        version = reports_repo.next_report_version(session, analysis_id)
        stage = ReportStage.VALIDATED.value if validation.valid else ReportStage.DRAFT.value
        meta: dict[str, Any] = {
            "report_builder_version": built.content.get("meta", {}).get("report_builder_version"),
            "validation": {
                "valid": validation.valid,
                "issues": validation.issues,
            },
        }
        created = reports_repo.upsert_report(
            session,
            analysis_id=analysis_id,
            version=version,
            stage=stage,
            content=built.content,
            content_md=built.content_md,
            meta=meta,
            fingerprint=built.fingerprint,
        )
        row = reports_repo.get_latest_report(session, analysis_id)
        assert row is not None
    _LOG.info(
        "report_built",
        analysis_id=str(analysis_id),
        version=row.version,
        stage=row.stage,
        valid=validation.valid,
        issues=len(validation.issues),
        reused=not created,
    )
    return ReportOutcome(report=row, valid=validation.valid, issues=validation.issues, reused=not created)


__all__ = ["ReportOutcome", "build_and_validate_report"]

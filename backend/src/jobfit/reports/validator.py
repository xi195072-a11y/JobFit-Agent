"""ReportValidator（Phase 4 §24）：报告发布前的确定性一致性防线。

对 `BuiltReport.content` 与 **DB 最新状态** 交叉校验：
1. hard constraint 数值与数据库一致；2. score 与 score_snapshot 一致；
3. skill match 与 DB 一致；4. unknown 保留；5. citation 可解析；
6. citation 属于当前 analysis；7. 不存在 fabricated evidence；
8. 不存在 cross-analysis reference；9. 报告没有未经支持的新硬事实。

本模块**只读**，从不修改任何表；失败 => 报告不得发布（stage 不得为 validated）。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from jobfit.db import models
from jobfit.reports.builder import ReportInputs, load_report_inputs


@dataclass(frozen=True)
class ReportValidationResult:
    valid: bool
    issues: list[str] = field(default_factory=list)


def _section_by_type(content: dict[str, Any], kind: str) -> dict[str, Any] | None:
    for section in content.get("sections", []):
        if section.get("type") == kind:
            return section
    return None


def _evidence_chunk_scope(session: Session, analysis_id: uuid.UUID) -> set[str]:
    """当前 analysis 唯一合法的证据池：绑定 resume profile 的 parsed_document 下全部 chunk id。

    证据一律来自简历侧文档（Phase 3 语义）；不在该作用域内的 chunk id
    一律视为 fabricated / cross-analysis。
    """
    from jobfit.db.repositories import analyses as analyses_repo

    analysis = analyses_repo.get_analysis(session, analysis_id)
    if analysis is None or analysis.resume_profile_id is None:
        return set()
    profile = session.get(models.ResumeProfile, analysis.resume_profile_id)
    if profile is None or profile.parsed_document_id is None:
        return set()
    rows = session.execute(
        select(models.DocumentChunk.id).where(
            models.DocumentChunk.parsed_document_id == profile.parsed_document_id
        )
    ).all()
    return {str(row.id) for row in rows}


class ReportValidator:
    def validate(
        self, session: Session, *, built: Any, analysis_id: uuid.UUID
    ) -> ReportValidationResult:
        """`built` 为 reports/builder.BuiltReport（或等价的 content dict）。"""
        content = built.content if hasattr(built, "content") else built
        issues: list[str] = []
        fresh = load_report_inputs(session, analysis_id=analysis_id)
        scope = _evidence_chunk_scope(session, analysis_id)

        issues.extend(self._check_constraints(content, fresh))
        issues.extend(self._check_skills(content, fresh))
        issues.extend(self._check_score(content, fresh))
        issues.extend(self._check_unknowns(content, fresh))
        issues.extend(self._check_evidence(content, fresh, scope, analysis_id))
        issues.extend(self._check_critique_inclusion(content, fresh, analysis_id))

        deduped: list[str] = []
        for issue in issues:
            if issue not in deduped:
                deduped.append(issue)
        return ReportValidationResult(valid=not deduped, issues=deduped)

    # ------------------------------------------------------------------ 1/2/3

    def _check_constraints(self, content: dict[str, Any], fresh: ReportInputs) -> list[str]:
        issues: list[str] = []
        section = _section_by_type(content, "hard_constraints")
        if section is None:
            return ["report 缺少 hard_constraints section"]
        expected = {
            (str(row.requirement_id), row.result, row.basis, row.reason_code)
            for row in fresh.constraints
        }
        got = {
            (
                str(row.get("requirement_id")),
                row.get("result"),
                row.get("basis"),
                row.get("reason_code"),
            )
            for row in section.get("rows", [])
        }
        if got != expected:
            issues.append(
                "hard_constraints 与 DB 不一致（要求数值/结论必须逐条一致，报告不得篡改确定性结果）"
            )
        return issues

    def _check_skills(self, content: dict[str, Any], fresh: ReportInputs) -> list[str]:
        issues: list[str] = []
        section = _section_by_type(content, "skills")
        if section is None:
            return ["report 缺少 skills section"]
        expected = {
            (str(row.jd_requirement_id), row.status, row.score_contribution)
            for row in fresh.skill_matches
        }
        got = {
            (
                str(row.get("jd_requirement_id")),
                row.get("status"),
                row.get("score_contribution"),
            )
            for row in section.get("rows", [])
        }
        if got != expected:
            issues.append("skills 与 DB 不一致（技能结论以确定性结果为准）")
        return issues

    def _check_score(self, content: dict[str, Any], fresh: ReportInputs) -> list[str]:
        issues: list[str] = []
        section = _section_by_type(content, "score")
        if section is None:
            return ["report 缺少 score section"]
        got = section.get("content", {})
        if got.get("total") != fresh.score.total:
            issues.append(f"score.total 与 score_snapshot 不一致（{got.get('total')} != {fresh.score.total}）")
        if got.get("scoring_version") != fresh.score.scoring_version:
            issues.append("score.scoring_version 与 score_snapshot 不一致")
        if list(got.get("flags") or []) != list(fresh.score.flags or []):
            issues.append("score.flags 与 score_snapshot 不一致")
        return issues

    # ------------------------------------------------------------------ 4

    def _check_unknowns(self, content: dict[str, Any], fresh: ReportInputs) -> list[str]:
        issues: list[str] = []
        section = _section_by_type(content, "unknowns")
        unknown_ids = {str(row.requirement_id) for row in fresh.constraints if row.result == "UNKNOWN"}
        if unknown_ids and section is None:
            return ["存在 UNKNOWN 硬条件但 report 缺少 unknowns section（UNKNOWN 必须保留）"]
        got_ids = {str(row.get("requirement_id")) for row in (section or {}).get("rows", [])}
        if got_ids != unknown_ids:
            issues.append(
                f"unknowns 未完整保留 UNKNOWN（expected={sorted(unknown_ids)}, got={sorted(got_ids)}）"
            )
        return issues

    # ------------------------------------------------------------------ 5/6/7/8

    def _check_evidence(
        self,
        content: dict[str, Any],
        fresh: ReportInputs,
        scope: set[str],
        analysis_id: uuid.UUID,
    ) -> list[str]:
        issues: list[str] = []
        section = _section_by_type(content, "evidence")
        if section is None:
            return ["report 缺少 evidence section"]
        referenced: set[str] = set()
        for row in section.get("rows", []):
            chunk_id = str(row.get("source_chunk_id") or "")
            if not chunk_id:
                issues.append("evidence section 存在缺失 source_chunk_id 的行")
                continue
            referenced.add(chunk_id)
            if row.get("resolvable") is False:
                issues.append(f"evidence {chunk_id} 不可解析（引用不存在的 source）")
            elif chunk_id not in scope:
                issues.append(
                    f"evidence {chunk_id} 不属于当前 analysis 的证据池"
                    "（cross-analysis 或 fabricated citation）"
                )
        # 报告证据 section 之外（trace 链）引用也必须属于当前 analysis
        for trace in fresh.traces:
            for ring in (trace.chain or {}).get("evidence", []):
                chunk_id = ring.get("source_chunk_id")
                if chunk_id and chunk_id not in scope:
                    issues.append(
                        f"trace 引用的 chunk {chunk_id} 不在当前 analysis 证据池"
                        "（cross-analysis reference）"
                    )
        return issues

    # ------------------------------------------------------------------ 9

    def _check_critique_inclusion(
        self, content: dict[str, Any], fresh: ReportInputs, analysis_id: uuid.UUID
    ) -> list[str]:
        issues: list[str] = []
        included_kinds = {
            section.get("type") for section in content.get("sections", [])
        }
        critique_derived = included_kinds & {"strengths", "gaps", "risks", "critique_unknowns"}
        if not critique_derived:
            return issues
        critique = fresh.critique
        if critique is None:
            issues.append("report 包含 critique 派生分区，但 DB 无 critique 记录")
            return issues
        if not critique.citations_validated:
            issues.append(
                f"report 包含 critique 派生分区，但 critique validation_status="
                f"{critique.validation_status}（rejected/unavailable 不得进入报告正文）"
            )
        return issues


__all__ = ["ReportValidationResult", "ReportValidator"]

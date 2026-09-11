"""Evidence-Grounded Report Builder（Phase 4 §22/§23）。

**确定性装配** + **已校验 critique**，不是 LLM 直接生成整份报告：
- score / hard constraints / skill matches 一律来自 DB（authoritative）；
- critique 只能作为**已验证的解释层**（validated critique；rejected/unavailable 不进入正文）。
- 报告块结构遵循 architecture §6.3：summary / hard_constraints / skills / evidence /
  strengths / gaps / risks / unknowns / score / critique / next_actions / review_status。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from jobfit.core.enums import ReportStage
from jobfit.db import models
from jobfit.db.repositories import critiques as critiques_repo
from jobfit.db.repositories import results as results_repo
from jobfit.matching import trace as trace_mod
from jobfit.reports.fingerprint import REPORT_BUILDER_VERSION, report_fingerprint


def _gate_from_flags(flags: list[str]) -> str:
    for flag in flags:
        if flag.startswith("GATE:"):
            return flag.split(":", 1)[1]
    return "unknown"


def _evidence_refs(
    evidence_index: dict[str, dict[str, Any]], evidence_ids: list[Any]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for chunk_id in dict.fromkeys(str(item) for item in evidence_ids):
        entry = evidence_index.get(chunk_id)
        if entry is None:
            rows.append({"source_chunk_id": chunk_id, "resolvable": False})
        else:
            rows.append({**entry, "resolvable": True})
    return rows


@dataclass
class ReportInputs:
    """build 所需的 DB 快照（由 report service 装配；validator 会与 DB 交叉校验）。"""

    analysis_id: uuid.UUID
    constraints: list[models.HardConstraintResult]
    skill_matches: list[models.SkillMatchResult]
    score: models.ScoreSnapshot
    traces: list[models.DecisionTrace]
    critique: models.Critique | None
    evidence_index: dict[str, dict[str, Any]]
    pipeline_version: str
    ruleset_version: str | None
    scoring_version: str | None
    prompt_version: str | None
    llm_model: str | None
    embedding_model: str | None


def load_report_inputs(
    session: Session, *, analysis_id: uuid.UUID, include_evidence_text: bool = True
) -> ReportInputs:
    from jobfit.db.repositories import analyses as analyses_repo

    analysis = analyses_repo.get_analysis(session, analysis_id)
    if analysis is None:
        raise ValueError(f"analysis {analysis_id} not found")
    constraints = results_repo.list_constraint_results(session, analysis_id)
    skill_matches = results_repo.list_skill_match_results(session, analysis_id)
    score = results_repo.get_score_snapshot(session, analysis_id)
    if score is None:
        raise ValueError(f"analysis {analysis_id} has no score snapshot; cannot build report")
    traces = results_repo.list_traces(session, analysis_id)
    critique = critiques_repo.get_latest_critique(session, analysis_id)

    ids: list[str] = []
    for row in constraints:
        ids.extend(str(item) for item in (row.evidence_ids or []))
    for row in skill_matches:
        ids.extend(str(item) for item in (row.evidence_ids or []))
    for trace in traces:
        for ring in (trace.chain or {}).get("evidence", []):
            chunk_id = ring.get("source_chunk_id")
            if chunk_id:
                ids.append(str(chunk_id))
    index = trace_mod.load_evidence_index(session, ids)

    return ReportInputs(
        analysis_id=analysis_id,
        constraints=constraints,
        skill_matches=skill_matches,
        score=score,
        traces=traces,
        critique=critique,
        evidence_index=index,
        pipeline_version=analysis.pipeline_version,
        ruleset_version=analysis.ruleset_version,
        scoring_version=analysis.scoring_version,
        prompt_version=analysis.prompt_version,
        llm_model=analysis.llm_model,
        embedding_model=analysis.embedding_model,
    )


@dataclass
class BuiltReport:
    content: dict[str, Any] = field(default_factory=dict)
    content_md: str = ""
    fingerprint: str = ""


def _unknown_rows(inputs: ReportInputs) -> list[dict[str, Any]]:
    return [
        {
            "requirement_id": str(row.requirement_id),
            "constraint_type": row.constraint_type,
            "reason_code": row.reason_code,
            "note": row.note,
        }
        for row in inputs.constraints
        if row.result == "UNKNOWN"
    ]


def _critique_section(inputs: ReportInputs) -> dict[str, Any]:
    critique = inputs.critique
    if critique is None:
        return {"status": "NONE"}
    if critique.status == "unavailable":
        return {"status": "UNAVAILABLE", "reason": "external_credential_blocked"}
    return {
        "status": critique.status,
        "validation_status": critique.validation_status,
        "citations_validated": critique.citations_validated,
        "model": critique.model,
        "prompt_version": critique.prompt_version,
        "content": critique.content,
    }


def _next_actions(inputs: ReportInputs) -> list[dict[str, Any]]:
    """确定性"下一步建议"：基于 gaps/unknowns/缺失技能生成（无 LLM）。

    规则有限且可枚举，报告中的建议不得引入未经支持的硬事实（ReportValidator §24-9）。
    """
    actions: list[dict[str, Any]] = []
    for row in inputs.constraints:
        if row.result == "UNKNOWN":
            actions.append(
                {
                    "kind": "provide_evidence",
                    "requirement_id": str(row.requirement_id),
                    "constraint_type": row.constraint_type,
                    "action": "补充该硬条件的证据或人工说明（当前为 UNKNOWN）",
                }
            )
    for row in inputs.skill_matches:
        if row.status == "unknown":
            actions.append(
                {
                    "kind": "provide_evidence",
                    "requirement_id": str(row.jd_requirement_id),
                    "action": "补充技能证据（当前证据不足，为 UNKNOWN）",
                }
            )
    return actions


def build_report(inputs: ReportInputs) -> BuiltReport:
    flags = list(inputs.score.flags or [])
    gate = _gate_from_flags(flags)
    evidence_ids_union: list[str] = []
    for row in inputs.constraints:
        evidence_ids_union.extend(str(item) for item in (row.evidence_ids or []))
    for row in inputs.skill_matches:
        evidence_ids_union.extend(str(item) for item in (row.evidence_ids or []))

    sections: list[dict[str, Any]] = [
        {
            "type": "summary",
            "content": {
                "analysis_id": str(inputs.analysis_id),
                "gate": gate,
                "score_total": inputs.score.total,
                "constraint_count": len(inputs.constraints),
                "skill_match_count": len(inputs.skill_matches),
                "pipeline_version": inputs.pipeline_version,
            },
        },
        {
            "type": "hard_constraints",
            "rows": [
                {
                    "requirement_id": str(row.requirement_id),
                    "constraint_type": row.constraint_type,
                    "result": row.result,
                    "basis": row.basis,
                    "reason_code": row.reason_code,
                    "evidence_ids": list(row.evidence_ids or []),
                    "note": row.note,
                }
                for row in inputs.constraints
            ],
        },
        {
            "type": "skills",
            "rows": [
                {
                    "jd_requirement_id": str(row.jd_requirement_id),
                    "status": row.status,
                    "norm_used": row.norm_used,
                    "score_contribution": row.score_contribution,
                    "evidence_ids": list(row.evidence_ids or []),
                }
                for row in inputs.skill_matches
            ],
        },
        {
            "type": "evidence",
            "rows": _evidence_refs(inputs.evidence_index, evidence_ids_union),
        },
        {
            "type": "unknowns",
            "rows": _unknown_rows(inputs),
        },
        {
            "type": "score",
            "content": {
                "total": inputs.score.total,
                "gate": gate,
                "scoring_version": inputs.score.scoring_version,
                "flags": flags,
                "per_section": list(inputs.score.per_section or []),
            },
        },
        {"type": "critique", "content": _critique_section(inputs)},
        {"type": "next_actions", "rows": _next_actions(inputs)},
        {"type": "review_status", "content": {"stage": ReportStage.DRAFT.value}},
    ]

    # critique 的已校验分区（只允许 validated critique 进入正文；rejected 仅留状态）
    if inputs.critique is not None and inputs.critique.citations_validated:
        content = dict(inputs.critique.content or {})
        sections.append({"type": "strengths", "rows": content.get("strengths", [])})
        sections.append({"type": "gaps", "rows": content.get("gaps", [])})
        sections.append({"type": "risks", "rows": content.get("risks", [])})
        sections.append(
            {
                "type": "critique_unknowns",
                "rows": content.get("unknown_acknowledgements", []),
            }
        )

    meta = {
        "report_builder_version": REPORT_BUILDER_VERSION,
        "report_version": 1,
        "analysis_id": str(inputs.analysis_id),
        "versions": {
            "pipeline_version": inputs.pipeline_version,
            "ruleset_version": inputs.ruleset_version,
            "scoring_version": inputs.scoring_version,
            "prompt_version": inputs.prompt_version,
        },
        "models": {
            "llm_model": inputs.llm_model,
            "embedding_model": inputs.embedding_model,
        },
        "critique": (
            {
                "fingerprint": inputs.critique.fingerprint,
                "validation_status": inputs.critique.validation_status,
                "citations_validated": inputs.critique.citations_validated,
            }
            if inputs.critique is not None
            else None
        ),
        "stages": ["draft", "validated", "final"],
    }

    content = {"sections": sections, "meta": meta}
    md = _render_markdown(sections, meta)
    fingerprint = report_fingerprint(
        analysis_id=inputs.analysis_id,
        constraints=inputs.constraints,
        skill_matches=inputs.skill_matches,
        score=inputs.score,
        critique_fingerprint=inputs.critique.fingerprint if inputs.critique is not None else None,
    )
    return BuiltReport(content=content, content_md=md, fingerprint=fingerprint)


def _render_markdown(sections: list[dict[str, Any]], meta: dict[str, Any]) -> str:
    lines: list[str] = ["# JobFit 匹配报告", ""]
    for section in sections:
        kind = section["type"]
        if kind == "summary":
            c = section["content"]
            lines.append(f"## 摘要（gate={c['gate']}, score={c['score_total']}）")
        elif kind == "hard_constraints":
            lines.append("## 硬性条件")
            for row in section["rows"]:
                lines.append(f"- {row['constraint_type']}: **{row['result']}** ({row['reason_code']})")
        elif kind == "skills":
            lines.append("## 技能匹配")
            for row in section["rows"]:
                lines.append(
                    f"- {row['norm_used'] or row['jd_requirement_id']}: {row['status']}"
                    f" (contribution={row['score_contribution']})"
                )
        elif kind == "unknowns":
            lines.append("## 未知项（UNKNOWN）")
            for row in section["rows"]:
                lines.append(f"- {row['constraint_type']} ({row['requirement_id']})")
        elif kind == "score":
            c = section["content"]
            lines.append(f"## 评分（total={c['total']}, gate={c['gate']}）")
        elif kind == "critique":
            c = section["content"]
            if c.get("citations_validated"):
                lines.append("## Critique（已校验）")
                lines.append(str(c.get("content", {}).get("overall_assessment", "")))
            else:
                lines.append(f"## Critique（{c.get('status', 'NONE')}）")
        elif kind in {"strengths", "gaps", "risks"}:
            lines.append(f"## {kind}")
            for row in section["rows"]:
                lines.append(f"- [{row.get('claim_type')}] {row.get('claim')}")
        elif kind == "next_actions":
            lines.append("## 建议下一步")
            for row in section["rows"]:
                lines.append(f"- {row['action']}")
        lines.append("")
    lines.append("---")
    lines.append(f"_report_builder={meta['report_builder_version']}_")
    return "\n".join(lines)


__all__ = [
    "BuiltReport",
    "REPORT_BUILDER_VERSION",
    "ReportInputs",
    "build_report",
    "load_report_inputs",
]

# mypy: disable-error-code="arg-type,attr-defined,assignment,union-attr"
"""unit: Report Builder 确定性装配（Phase 4 §22/§23）——不依赖 DB。

- score / hard constraints / skills 一律来自输入快照（authoritative）；
- critique 只有 validated（citations_validated=True）才进入正文分区；
- UNKNOWN 必须保留；next_actions 是确定性规则。
"""

from __future__ import annotations

import uuid

from jobfit.core.enums import ReportStage
from jobfit.db import models
from jobfit.reports.builder import (
    REPORT_BUILDER_VERSION,
    ReportInputs,
    build_report,
)

ANALYSIS = uuid.uuid4()
REQ_MET = uuid.uuid4()
REQ_UNKNOWN = uuid.uuid4()


def _inputs(*, critique: models.Critique | None = None) -> ReportInputs:
    return ReportInputs(
        analysis_id=ANALYSIS,
        constraints=[
            models.HardConstraintResult(
                requirement_id=REQ_MET,
                constraint_type="degree",
                result="MET",
                basis="deterministic",
                ruleset_version="r:1",
                reason_code="DEGREE_AT_LEAST_MET",
                evidence_ids=[uuid.uuid4()],
                note=None,
            ),
            models.HardConstraintResult(
                requirement_id=REQ_UNKNOWN,
                constraint_type="security_clearance",
                result="UNKNOWN",
                basis="deterministic",
                ruleset_version="r:1",
                reason_code="UNSUPPORTED_REQUIREMENT_TYPE",
                evidence_ids=[],
                note="schema 未建模类别",
            ),
        ],
        skill_matches=[
            models.SkillMatchResult(
                jd_requirement_id=uuid.uuid4(),
                status="matched",
                ruleset_version="r:1",
                norm_used="python",
                score_contribution=40.0,
                evidence_ids=[uuid.uuid4()],
            ),
            models.SkillMatchResult(
                jd_requirement_id=uuid.uuid4(),
                status="unknown",
                ruleset_version="r:1",
                norm_used="kubernetes",
                score_contribution=0.0,
                evidence_ids=[],
            ),
        ],
        score=models.ScoreSnapshot(
            analysis_id=ANALYSIS,
            kind="base",
            total=82.5,
            per_section=[{"section": "skills", "score": 40.0}],
            flags=["GATE:PASS"],
            scoring_version="s:0.2.0",
        ),
        traces=[],
        critique=critique,
        evidence_index={},
        pipeline_version="test-pipeline-1",
        ruleset_version="r:1",
        scoring_version="s:0.2.0",
        prompt_version="h:abc",
        llm_model="test-deterministic",
        embedding_model="hash-ngram-v1",
    )


def _critique_row(*, citations_validated: bool, validation_status: str, status: str = "ok") -> models.Critique:
    return models.Critique(
        analysis_id=ANALYSIS,
        version=1,
        status=status,
        provider="deepseek",
        model="test-deterministic",
        prompt_version="h:abc",
        schema_version="critique.v1",
        content={
            "overall_assessment": "总体匹配良好。",
            "strengths": [
                {
                    "category": "strength",
                    "claim": "学历满足岗位要求。",
                    "claim_type": "supported",
                    "evidence_refs": [{"source_chunk_id": str(uuid.uuid4())}],
                }
            ],
            "gaps": [],
            "risks": [],
            "unknown_acknowledgements": [str(REQ_UNKNOWN)],
        },
        validation_status=validation_status,
        citations_validated=citations_validated,
        fingerprint="cr:abc",
    )


def _section_types(built) -> set[str]:
    return {section["type"] for section in built.content["sections"]}


def test_build_report_without_critique() -> None:
    built = build_report(_inputs())
    kinds = _section_types(built)
    # 无 critique：不得出现 critique 派生分区
    assert kinds == {
        "summary",
        "hard_constraints",
        "skills",
        "evidence",
        "unknowns",
        "score",
        "critique",
        "next_actions",
        "review_status",
    }
    critique_section = next(s for s in built.content["sections"] if s["type"] == "critique")
    assert critique_section["content"]["status"] == "NONE"
    assert built.fingerprint.startswith("rp:")
    assert built.content["meta"]["report_builder_version"] == REPORT_BUILDER_VERSION
    assert "JobFit 匹配报告" in built.content_md


def test_build_report_with_rejected_critique_omits_derived_sections() -> None:
    critique = _critique_row(citations_validated=False, validation_status="rejected")
    built = build_report(_inputs(critique=critique))
    kinds = _section_types(built)
    assert "strengths" not in kinds
    assert "gaps" not in kinds
    assert "risks" not in kinds
    section = next(s for s in built.content["sections"] if s["type"] == "critique")
    assert section["content"]["validation_status"] == "rejected"


def test_build_report_with_validated_critique_includes_derived_sections() -> None:
    critique = _critique_row(citations_validated=True, validation_status="validated")
    built = build_report(_inputs(critique=critique))
    kinds = _section_types(built)
    assert {"strengths", "gaps", "risks", "critique_unknowns"} <= kinds
    strengths = next(s for s in built.content["sections"] if s["type"] == "strengths")
    assert strengths["rows"][0]["claim"] == "学历满足岗位要求。"


def test_unknown_constraints_are_preserved() -> None:
    built = build_report(_inputs())
    unknowns = next(s for s in built.content["sections"] if s["type"] == "unknowns")
    ids = {row["requirement_id"] for row in unknowns["rows"]}
    assert ids == {str(REQ_UNKNOWN)}
    # hard_constraints 原样保留 UNKNOWN 结论
    constraints = next(s for s in built.content["sections"] if s["type"] == "hard_constraints")
    by_id = {row["requirement_id"]: row for row in constraints["rows"]}
    assert by_id[str(REQ_UNKNOWN)]["result"] == "UNKNOWN"


def test_next_actions_are_deterministic_rules() -> None:
    built = build_report(_inputs())
    actions = next(s for s in built.content["sections"] if s["type"] == "next_actions")
    kinds = {row["kind"] for row in actions["rows"]}
    assert kinds == {"provide_evidence"}  # 只包含确定性建议，无 LLM 自由文本
    assert len(actions["rows"]) == 2  # 1 个 UNKNOWN 硬条件 + 1 个 UNKNOWN 技能


def test_review_status_starts_draft() -> None:
    built = build_report(_inputs())
    status = next(s for s in built.content["sections"] if s["type"] == "review_status")
    assert status["content"]["stage"] == ReportStage.DRAFT.value


def test_markdown_contains_key_sections() -> None:
    critique = _critique_row(citations_validated=True, validation_status="validated")
    md = build_report(_inputs(critique=critique)).content_md
    for heading in ("## 硬性条件", "## 技能匹配", "## 评分", "## strengths", "## 建议下一步"):
        assert heading in md

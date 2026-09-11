"""Critique Pydantic schema（Phase 4 §6/§13）。

LLM 输出必须经 provider → structured output → 本 schema 校验后才可进入 domain layer
（ADR-002）。claim 的事实类别（ClaimType）与 Phase 3 的 TRUE/FALSE/UNKNOWN **不是一回事**：
- SUPPORTED = 有确定性证据直接支撑的 claim；
- INFERENTIAL = LLM 推断（**不是事实**，不得写入 hard_constraint_results 等确定性表）；
- UNCERTAIN = 证据不足，对应 Phase 3 的 UNKNOWN（必须保留，不得改写成确定结论）；
- CONTRADICTED = 与确定性结果/证据矛盾（触发 EXTRACTION_REVIEW_REQUIRED 类提示，不得自动修正）。

禁止 free text → json.loads → blind persistence；一切经 Pydantic v2。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from jobfit.core.enums import ClaimType, CritiqueCategory

SCHEMA_VERSION = "critique.v1"


class CritiqueEvidenceRef(BaseModel):
    """claim 的 citation：必须命中当前 analysis 的决策链/证据池（Phase 4 §9/§10）。

    至少一个引用字段非空；全部为空 = 无 citation = validator 拒绝。
    跨 analysis 引用（trace_id/source_chunk_id 不属于当前 analysis）由 CitationValidator 拒绝。
    """

    model_config = ConfigDict(extra="forbid")

    trace_id: str | None = None
    source_chunk_id: str | None = None
    evidence_id: str | None = None
    span_char_start: int | None = Field(default=None, ge=0)
    span_char_end: int | None = Field(default=None, ge=0)
    excerpt_sha256: str | None = None

    @model_validator(mode="after")
    def _at_least_one_reference(self) -> "CritiqueEvidenceRef":
        if not (self.trace_id or self.source_chunk_id or self.evidence_id):
            raise ValueError("evidence reference must carry at least trace_id/source_chunk_id/evidence_id")
        return self

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)


class CritiquePoint(BaseModel):
    """一个 critique 观点（strength/gap/risk/ambiguity/question 之一）。"""

    model_config = ConfigDict(extra="forbid")

    category: CritiqueCategory
    claim: str = Field(min_length=1)
    claim_type: ClaimType = ClaimType.SUPPORTED
    evidence_refs: list[CritiqueEvidenceRef] = Field(default_factory=list)

    @model_validator(mode="after")
    def _evidence_required_for_supported(self) -> "CritiquePoint":
        # SUPPORTED 必须携带 citation（Phase 4 §9：factual claim 必须有 evidence reference）。
        if self.claim_type == ClaimType.SUPPORTED and not self.evidence_refs:
            raise ValueError("SUPPORTED claim requires at least one evidence_ref")
        return self

    def as_dict(self) -> dict[str, Any]:
        return {
            "category": self.category.value,
            "claim": self.claim,
            "claim_type": self.claim_type.value,
            "evidence_refs": [ref.as_dict() for ref in self.evidence_refs],
        }


class CritiqueObservation(BaseModel):
    """对单个 requirement（硬条件/技能）的观察；requirement_id 必须属于当前 analysis。"""

    model_config = ConfigDict(extra="forbid")

    requirement_id: str = Field(min_length=1)
    observation: str = Field(min_length=1)
    claim_type: ClaimType = ClaimType.SUPPORTED
    evidence_refs: list[CritiqueEvidenceRef] = Field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "requirement_id": self.requirement_id,
            "observation": self.observation,
            "claim_type": self.claim_type.value,
            "evidence_refs": [ref.as_dict() for ref in self.evidence_refs],
        }


class CritiqueSchema(BaseModel):
    """LLM critique 的正式结构化输出（Phase 4 §6）。"""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = SCHEMA_VERSION
    overall_assessment: str = Field(min_length=1)
    strengths: list[CritiquePoint] = Field(default_factory=list)
    gaps: list[CritiquePoint] = Field(default_factory=list)
    risks: list[CritiquePoint] = Field(default_factory=list)
    ambiguities: list[CritiquePoint] = Field(default_factory=list)
    questions: list[CritiquePoint] = Field(default_factory=list)
    constraint_observations: list[CritiqueObservation] = Field(default_factory=list)
    skill_observations: list[CritiqueObservation] = Field(default_factory=list)
    # 显式列出被保留的 UNKNOWN（Phase 4 §14）：LLM 只能 acknowledge，不能改写。
    unknown_acknowledgements: list[str] = Field(default_factory=list)

    def all_points(self) -> list[CritiquePoint]:
        return [
            *self.strengths,
            *self.gaps,
            *self.risks,
            *self.ambiguities,
            *self.questions,
        ]

    def all_observations(self) -> list[CritiqueObservation]:
        return [*self.constraint_observations, *self.skill_observations]

    def all_evidence_refs(self) -> list[CritiqueEvidenceRef]:
        refs: list[CritiqueEvidenceRef] = []
        for point in self.all_points():
            refs.extend(point.evidence_refs)
        for obs in self.all_observations():
            refs.extend(obs.evidence_refs)
        return refs

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "overall_assessment": self.overall_assessment,
            "strengths": [p.as_dict() for p in self.strengths],
            "gaps": [p.as_dict() for p in self.gaps],
            "risks": [p.as_dict() for p in self.risks],
            "ambiguities": [p.as_dict() for p in self.ambiguities],
            "questions": [p.as_dict() for p in self.questions],
            "constraint_observations": [o.as_dict() for o in self.constraint_observations],
            "skill_observations": [o.as_dict() for o in self.skill_observations],
            "unknown_acknowledgements": list(self.unknown_acknowledgements),
        }

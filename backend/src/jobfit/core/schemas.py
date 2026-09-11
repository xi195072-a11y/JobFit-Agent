"""Pydantic v2 domain schemas（Core Contracts）。

所有跨层数据（抽取结果 / 匹配结果 / LLM 输出）都必须经这些 model 校验，
任意 dict 不得直接进入 domain layer（ADR-002）。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from jobfit.core.enums import (
    ConstraintBasis,
    DocumentKind,
    RequirementType,
    SkillMatchStatus,
    Verdict,
)

# ---------------------------------------------------------------- schema 版本


class SchemaVersions(BaseModel):
    """domain schema 版本契约（ADR-019）。"""

    resume: str = "resume.v1"
    jd: str = "jd.v1"
    report: str = "report.v1"

    def extraction_versions(self) -> dict[str, str]:
        return {"resume": self.resume, "jd": self.jd}


def combined_extraction_schema(v: SchemaVersions) -> str:
    """analyses.extraction_schema_version 采用复合串（profile 各自单独存分版本）。"""
    return f"{v.resume}|{v.jd}"


def strip_artifact_meta(full_dump: Mapping[str, Any]) -> dict[str, Any]:
    """剥离 artifact 内部元数据（`full_dump["_meta"]`）后再做领域 schema 校验。

    profile 行多存 ``_meta``（fingerprint / prompt_version / parser_version 等），
    而领域 schema 是 ``extra="forbid"``，因此读取时必须先剥离。
    """
    return {key: value for key, value in full_dump.items() if key != "_meta"}


# ---------------------------------------------------------------- 引用与证据


class SourceAnchor(BaseModel):
    """文本锚点：定位到 parsed artifact 内的原文窗口。"""

    doc_kind: DocumentKind
    parsed_document_id: str
    page: int | None = None
    char_start: int = Field(ge=0)
    char_end: int = Field(ge=0)

    model_config = ConfigDict(use_enum_values=False)


class Evidence(BaseModel):
    """证据引用。text 必须是 PII-safe excerpt（ADR-020）。"""

    evidence_id: str
    source_chunk_id: str
    excerpt: str = Field(min_length=0)
    span_char_start: int = Field(ge=0)
    span_char_end: int = Field(ge=0)
    excerpt_sha256: str | None = None


# ---------------------------------------------------------------- resume


class ResumeEducation(BaseModel):
    school: str | None = None
    degree: str | None = None
    major: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    gpa: float | None = None
    honor: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    anchors: list[SourceAnchor] = Field(default_factory=list)


class ResumeExperience(BaseModel):
    company: str | None = None
    title: str | None = None
    location: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    bullets: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    anchors: list[SourceAnchor] = Field(default_factory=list)


class ResumeSkill(BaseModel):
    skill_raw: str
    skill_norm: str | None = None
    category: str | None = None
    proficiency: str | None = None
    claimed_only: bool = False
    evidence_ids: list[str] = Field(default_factory=list)


class ResumeProfile(BaseModel):
    """简历抽取结果（immutable extraction artifact 的 domain 视图）。"""

    SCHEMA_VERSION: ClassVar[str] = "resume.v1"

    document_id: str
    parsed_document_id: str
    name: str | None = None
    phone: str | None = None
    email: str | None = None
    location: str | None = None
    education: list[ResumeEducation] = Field(default_factory=list)
    experiences: list[ResumeExperience] = Field(default_factory=list)
    skills: list[ResumeSkill] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    extraction_warnings: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- jd


class JDRequirement(BaseModel):
    SCHEMA_VERSION: ClassVar[str] = "jd.v1"

    req_type: RequirementType
    operator: str
    value: dict[str, Any] = Field(default_factory=dict)
    weight: float = Field(default=1.0)
    is_hard: bool
    source_text: str | None = None
    anchors: list[SourceAnchor] = Field(default_factory=list)


class JDProfile(BaseModel):
    """JD 抽取结果（immutable extraction artifact 的 domain 视图）。"""

    SCHEMA_VERSION: ClassVar[str] = "jd.v1"

    document_id: str
    parsed_document_id: str
    company: str | None = None
    role_title: str | None = None
    location: str | None = None
    requirements: list[JDRequirement] = Field(default_factory=list)
    preferred_qualifications: list[JDRequirement] = Field(default_factory=list)
    responsibilities: list[str] = Field(default_factory=list)
    extraction_warnings: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- 匹配/评分输出

class HardConstraintResult(BaseModel):
    requirement_id: str
    req_type: RequirementType
    rule_id: str | None = None
    result: Verdict
    basis: ConstraintBasis
    evidence_ids: list[str] = Field(default_factory=list)
    note: str | None = None
    trace_id: str | None = None


class SkillMatch(BaseModel):
    jd_requirement_id: str
    resume_skill_id: str | None = None
    status: SkillMatchStatus
    norm_used: str | None = None
    score_contribution: float = Field(default=0.0)
    evidence_ids: list[str] = Field(default_factory=list)
    trace_id: str | None = None


class ScoreSection(BaseModel):
    section: str
    weight: float = Field(default=0.0)
    points: float = Field(default=0.0)
    max_points: float = Field(default=0.0)
    detail: dict[str, Any] = Field(default_factory=dict)


class ScoreBreakdown(BaseModel):
    total: float = Field(default=0.0)
    per_section: list[ScoreSection] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)  # e.g. "N items UNKNOWN pending"


# ---------------------------------------------------------------- LLM critique

class CritiqueSuggestion(BaseModel):
    suggestion_id: str
    severity: str = Field(pattern="^(high|medium|low)$")
    summary: str
    evidence_ids: list[str] = Field(default_factory=list)
    state: Verdict = Verdict.UNKNOWN
    action: str


class Critique(BaseModel):
    """LLM critique 输出契约。引用只能来自证据池（由 citation validator 复核）。"""

    overall_alignment: str = Field(pattern="^(strong|moderate|weak)$")
    alignment_reason: str
    suggestions: list[CritiqueSuggestion] = Field(default_factory=list)
    model: str
    prompt_version: str | None = None


# ---------------------------------------------------------------- pipeline 版本

class PipelineVersions(BaseModel):
    """一次分析固化的版本快照（architecture.md §4.5 / ADR-019）。"""

    pipeline_version: str
    extraction_schema_version: str
    prompt_version: str | None = None
    ruleset_version: str | None = None
    scoring_version: str | None = None
    llm_model: str
    embedding_model: str | None = None
    config_snapshot: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------- 报告/错误

class ReportSection(BaseModel):
    type: str
    content: dict[str, Any] = Field(default_factory=dict)


class Report(BaseModel):
    SCHEMA_VERSION: ClassVar[str] = "report.v1"

    analysis_id: str
    stage: str = Field(pattern="^(draft|final)$")
    sections: list[ReportSection] = Field(default_factory=list)
    versions: PipelineVersions | None = None


class NodeError(BaseModel):
    node: str
    severity: str = Field(pattern="^(fatal|degradable)$")
    message: str
    raised_at: datetime | None = None

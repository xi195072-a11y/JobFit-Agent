"""API 响应 DTO。不直接泄露内部模型字段，secret 永远不出现。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class DocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: str
    original_filename: str
    mime_type: str
    size_bytes: int
    sha256: str
    created_at: datetime


class AnalysisRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    resume_document_id: uuid.UUID
    jd_document_id: uuid.UUID
    resume_profile_id: uuid.UUID | None = None
    jd_profile_id: uuid.UUID | None = None
    status: str
    current_phase: str | None = None
    idempotency_key: str | None = None
    pipeline_version: str
    extraction_schema_version: str
    prompt_version: str | None = None
    ruleset_version: str | None = None
    scoring_version: str | None = None
    llm_model: str | None = None
    embedding_model: str | None = None
    llm_attempts_used: int = 0
    llm_budget_exceeded: bool = False
    created_at: datetime
    updated_at: datetime
    reused: bool = False


class AnalysisCreate(BaseModel):
    resume_document_id: uuid.UUID
    jd_document_id: uuid.UUID
    idempotency_key: str | None = None
    # 显式绑定（Phase 3 §5）：提供即校验归属并写入；不提供则保持 NULL，由执行期绑定。
    resume_profile_id: uuid.UUID | None = None
    jd_profile_id: uuid.UUID | None = None


class UploadError(BaseModel):
    code: str
    message: str


class Envelope(BaseModel):
    data: object | None = None
    error: UploadError | None = None


# ---------------------------------------------------------------- Phase 2

class ParseResultRead(BaseModel):
    document_id: uuid.UUID
    parsed_document_id: uuid.UUID
    parser_version: str
    actual_kind: str
    chunk_count: int
    chunks_created: int
    reused_artifact: bool


class ArtifactSummary(BaseModel):
    kind: str
    artifact_id: uuid.UUID
    version: str
    content_sha256: str
    created_at: datetime
    extra: dict[str, int] = {}


class DocumentArtifactsRead(BaseModel):
    document_id: uuid.UUID
    artifacts: list[ArtifactSummary]


class ExtractionRunRead(BaseModel):
    analysis_id: uuid.UUID
    status: str
    resume_profile_id: uuid.UUID | None = None
    jd_profile_id: uuid.UUID | None = None
    resume_fingerprint: str | None = None
    jd_fingerprint: str | None = None
    errors: list[str] = []


# ---------------------------------------------------------------- Phase 3

class AnalysisRunRead(BaseModel):
    analysis_id: uuid.UUID
    status: str
    gate: str | None = None
    score_total: float | None = None
    constraint_count: int = 0
    skill_match_count: int = 0
    trace_count: int = 0
    flags: list[str] = []
    errors: list[str] = []


class AnalysisRunRequest(BaseModel):
    """requeue=True 时允许对 failed/succeeded 的 analysis 显式重跑（幂等 upsert，不产生重复结果）。"""

    requeue: bool = False


class ConstraintResultRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    requirement_id: uuid.UUID
    constraint_type: str
    result: str
    basis: str
    ruleset_version: str
    reason_code: str
    evidence_ids: list[uuid.UUID] = []
    trace_id: uuid.UUID | None = None
    note: str | None = None


class SkillMatchResultRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    jd_requirement_id: uuid.UUID
    resume_skill_id: uuid.UUID | None = None
    status: str
    ruleset_version: str
    norm_used: str | None = None
    score_contribution: float = 0.0
    evidence_ids: list[uuid.UUID] = []
    trace_id: uuid.UUID | None = None


class DecisionTraceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    decision_type: str
    decision_key: str
    chain: dict


class ScoreSectionRead(BaseModel):
    section: str
    weight: float
    applied_weight: float
    score: float | None
    status: str
    items_total: int
    items_determinable: int
    detail: dict = {}


class ScoreSnapshotRead(BaseModel):
    analysis_id: uuid.UUID
    kind: str
    total: float
    gate: str
    scoring_version: str | None = None
    flags: list[str] = []
    per_section: list[ScoreSectionRead] = []


class RetrievalQuery(BaseModel):
    """检索输入：query + candidate document scope + top_k + 可选 filter / anchor gate。"""

    query: str = Field(min_length=1)
    parsed_document_ids: list[uuid.UUID] = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=50)
    method: str = Field(default="auto", pattern="^(auto|vector|lexical)$")
    page: int | None = Field(default=None, ge=1)
    # anchor gate：命中必须字面包含其中任一词（确定性精度闸门；空 = 不设闸门）
    anchor_terms: list[str] = []
    min_similarity: float = 0.0
    min_lexical_score: float = 0.0


class RetrievalHitRead(BaseModel):
    source_chunk_id: uuid.UUID
    document_id: uuid.UUID
    parsed_document_id: uuid.UUID
    chunk_index: int
    page: int | None = None
    char_start: int
    char_end: int
    score: float
    retrieval_method: str


class RetrievalResponse(BaseModel):
    query: str
    retrieval_method: str
    embedding_model: str | None = None
    scope_size: int
    hits: list[RetrievalHitRead] = []


# ---------------------------------------------------------------- Phase 4

class CritiqueRead(BaseModel):
    """critique artifact（PII-safe：content 只含已校验的结构化 critique）。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    analysis_id: uuid.UUID
    version: int
    status: str  # ok | unavailable
    provider: str
    model: str
    prompt_version: str | None = None
    schema_version: str
    validation_status: str  # pending | validated | rejected
    citations_validated: bool
    fingerprint: str
    content: dict = {}
    latency_ms: int | None = None
    created_at: datetime


class ReportRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    analysis_id: uuid.UUID
    version: int
    stage: str  # draft | validated | final
    content: dict = {}
    content_md: str
    meta: dict = {}
    fingerprint: str
    published_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class Phase4RunRead(BaseModel):
    """POST /analyses/{id}/critique 触发 phase4 管线（critique -> report -> awaiting_review）。"""

    analysis_id: uuid.UUID
    status: str
    critique_status: str | None = None
    critique_validation: str | None = None
    report_version: int | None = None
    report_stage: str | None = None
    errors: list[str] = []


class ReportBuildRead(BaseModel):
    analysis_id: uuid.UUID
    version: int
    stage: str
    valid: bool
    reused: bool
    issues: list[str] = []


class ReviewRequest(BaseModel):
    """HITL review action（§28/§29）。decision ∈ approve | reject | request_changes。"""

    decision: str = Field(pattern="^(approve|reject|request_changes)$")
    comments: str | None = None
    reviewed_by: str | None = None
    overrides: dict | None = None


class ReviewRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    analysis_id: uuid.UUID
    decision: str
    comments: str | None = None
    overrides: dict | None = None
    reviewed_by: str | None = None
    from_state: str | None = None
    to_state: str | None = None
    version: int
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------- Phase 5（产品化）

class AnalysisListRead(BaseModel):
    """analysis 分页列表（Dashboard / `/jobs`）。顺序确定性，见 repositories.list_analyses。"""

    items: list[AnalysisRead]
    total: int
    limit: int
    offset: int


class ProfileSummaryRead(BaseModel):
    """可复用的 immutable profile artifact（Analysis 创建页候选）。

    **PII-safe**：只含 id / document 引用 / 版本五元组 / 提取时间，
    绝不含姓名、电话、邮箱或任何简历原文（§7/§8/§41）。
    """

    profile_id: uuid.UUID
    kind: str
    document_id: uuid.UUID
    parsed_document_id: uuid.UUID
    reference: str
    pipeline_version: str
    extraction_schema_version: str
    prompt_version: str
    llm_model: str
    extracted_at: datetime
    warning_count: int = 0


class ProfileListRead(BaseModel):
    kind: str
    items: list[ProfileSummaryRead]
    total: int


class EvidenceReferenceRead(BaseModel):
    """evidence 被谁引用（确定性可解释，非自然语言）。"""

    kind: str  # constraint | skill | trace
    ref_id: str
    label: str | None = None
    tier: str | None = None


class EvidenceRefRead(BaseModel):
    """PII-safe 证据引用：chunk 定位 + hash + 引用关系，**不含原文**（§13/§41）。"""

    source_chunk_id: uuid.UUID
    char_start: int = 0
    char_end: int = 0
    page: int | None = None
    span_sha256: str = ""
    resolvable: bool = True
    referenced_by: list[EvidenceReferenceRead] = []


class EvidenceListRead(BaseModel):
    analysis_id: uuid.UUID
    items: list[EvidenceRefRead]
    total: int


class ApiErrorBody(BaseModel):
    code: str
    message: str
    details: Any | None = None
    request_id: str | None = None
    retryable: bool = False


class ApiErrorRead(BaseModel):
    """统一错误响应（Phase 5 §46）。`detail` 保留 Phase 1–4 语义以兼容既有客户端。"""

    detail: Any
    error: ApiErrorBody

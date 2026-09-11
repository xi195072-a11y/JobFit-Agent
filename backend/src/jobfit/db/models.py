"""SQLAlchemy models — 严格实现 architecture v0.2.3 数据 ownership。

Document-scoped immutable artifacts：
  documents / parsed_documents / document_chunks / resume_profiles /
  resume_education / resume_experiences / resume_skills /
  jd_profiles / jd_requirements
  —— 只 INSERT + 只读复用，禁止 UPDATE/overwrite（唯一键冲突即复用）。

Analysis-scoped artifacts：
  analyses（任务队列 + 版本快照 + artifact 显式绑定）与其结果表
  （hard_constraint_results / skill_match_results / decision_traces /
   score_snapshots / critiques / reports / reviews / job_runs /
   llm_attempt_log / audit_log）。
  —— 写入必须受 claim fencing 四条件约束（db/repositories/analyses.py）。

时间语义统一由数据库 clock_timestamp() 承担；本文件不含任何数据库本地时间函数参与 lease 判定。
"""

from __future__ import annotations

import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)

from jobfit.db.base import Base

_CLOCK = text("clock_timestamp()")


def _pk_uuid() -> uuid.UUID:
    return uuid.uuid4()


def _audit_cols() -> tuple[Column, Column]:
    return (
        Column(
            "created_at",
            DateTime(timezone=True),
            nullable=False,
            server_default=_CLOCK,
        ),
        Column(
            "updated_at",
            DateTime(timezone=True),
            nullable=False,
            server_default=_CLOCK,
            onupdate=_CLOCK,
        ),
    )


def _created_col() -> Column:
    """仅 created_at（architecture §5.2 中大多数表只有 created_at）。"""
    return Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=_CLOCK,
    )


def _event_ts_col() -> Column:
    return Column("at", DateTime(timezone=True), nullable=False, server_default=_CLOCK)


# ================================================================ document-scoped

class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint("kind IN ('resume', 'jd')", name="kind_allowed"),
        UniqueConstraint("sha256", name="uq_documents_sha256"),
    )

    id = Column(Uuid, primary_key=True, default=_pk_uuid, server_default=text("gen_random_uuid()"))
    kind = Column(String(16), nullable=False)
    original_filename = Column(String(512), nullable=False)
    mime_type = Column(String(128), nullable=False)
    size_bytes = Column(Integer, nullable=False)
    sha256 = Column(String(64), nullable=False)
    storage_path = Column(String(1024), nullable=False)
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    created_at, updated_at = _audit_cols()


class ParsedDocument(Base):
    __tablename__ = "parsed_documents"

    id = Column(Uuid, primary_key=True, default=_pk_uuid, server_default=text("gen_random_uuid()"))
    document_id = Column(Uuid, ForeignKey("documents.id"), nullable=False)
    parser_version = Column(String(64), nullable=False)
    text = Column(Text, nullable=False)
    pages = Column(JSON, nullable=False)
    content_sha256 = Column(String(64), nullable=False)
    created_at = _created_col()

    __table_args__ = (
        UniqueConstraint("document_id", "parser_version", name="uq_parsed_document_version"),
        Index("ix_parsed_documents_document_id", "document_id"),
    )


class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    id = Column(Uuid, primary_key=True, default=_pk_uuid, server_default=text("gen_random_uuid()"))
    parsed_document_id = Column(Uuid, ForeignKey("parsed_documents.id"), nullable=False)
    chunk_index = Column(Integer, nullable=False)
    content = Column(Text, nullable=False)
    page = Column(Integer, nullable=True)
    char_start = Column(Integer, nullable=False)
    char_end = Column(Integer, nullable=False)
    span_sha256 = Column(String(64), nullable=False)
    embedding = Column(Vector(1024), nullable=True)  # pgvector, V1 启用
    embedding_model = Column(String(128), nullable=True)

    __table_args__ = (
        UniqueConstraint("parsed_document_id", "chunk_index", name="uq_document_chunk_index"),
        Index("ix_document_chunks_parsed_document_id", "parsed_document_id"),
    )


class ResumeProfile(Base):
    """immutable extraction artifact；五元组版本唯一。"""

    __tablename__ = "resume_profiles"

    id = Column(Uuid, primary_key=True, default=_pk_uuid, server_default=text("gen_random_uuid()"))
    document_id = Column(Uuid, ForeignKey("documents.id"), nullable=False)
    parsed_document_id = Column(Uuid, ForeignKey("parsed_documents.id"), nullable=False)
    pipeline_version = Column(String(128), nullable=False)
    extraction_schema_version = Column(String(64), nullable=False)
    prompt_version = Column(String(128), nullable=False)
    llm_model = Column(String(128), nullable=False)
    full_dump = Column(JSON, nullable=False)
    name_sha256 = Column(String(64), nullable=True)
    phone_sha256 = Column(String(64), nullable=True)
    email_sha256 = Column(String(64), nullable=True)
    extraction_warnings = Column(JSON, nullable=False, default=list)
    extracted_at = Column(DateTime(timezone=True), nullable=False, server_default=_CLOCK)

    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "pipeline_version",
            "extraction_schema_version",
            "prompt_version",
            "llm_model",
            name="uq_resume_profile_fingerprint",
        ),
        Index("ix_resume_profiles_document_id", "document_id"),
    )


class ResumeEducation(Base):
    __tablename__ = "resume_education"
    id = Column(Uuid, primary_key=True, default=_pk_uuid, server_default=text("gen_random_uuid()"))
    profile_id = Column(Uuid, ForeignKey("resume_profiles.id"), nullable=False)
    school = Column(String(512), nullable=True)
    degree = Column(String(128), nullable=True)
    major = Column(String(256), nullable=True)
    start_date = Column(DateTime(timezone=True), nullable=True)
    end_date = Column(DateTime(timezone=True), nullable=True)
    gpa = Column(Float, nullable=True)
    honor = Column(String(256), nullable=True)
    bullet_evidence_ids = Column(JSON, nullable=False, default=list)
    anchors = Column(JSON, nullable=False, default=list)
    created_at = Column("created_at", DateTime(timezone=True), nullable=False, server_default=_CLOCK)

    __table_args__ = (Index("ix_resume_education_profile_id", "profile_id"),)


class ResumeExperience(Base):
    __tablename__ = "resume_experiences"
    id = Column(Uuid, primary_key=True, default=_pk_uuid, server_default=text("gen_random_uuid()"))
    profile_id = Column(Uuid, ForeignKey("resume_profiles.id"), nullable=False)
    company = Column(String(512), nullable=True)
    title = Column(String(256), nullable=True)
    location = Column(String(256), nullable=True)
    start_date = Column(DateTime(timezone=True), nullable=True)
    end_date = Column(DateTime(timezone=True), nullable=True)
    bullets = Column(JSON, nullable=False, default=list)
    bullet_evidence_ids = Column(JSON, nullable=False, default=list)
    anchors = Column(JSON, nullable=False, default=list)
    created_at = _created_col()

    __table_args__ = (Index("ix_resume_experiences_profile_id", "profile_id"),)


class ResumeSkill(Base):
    __tablename__ = "resume_skills"
    id = Column(Uuid, primary_key=True, default=_pk_uuid, server_default=text("gen_random_uuid()"))
    profile_id = Column(Uuid, ForeignKey("resume_profiles.id"), nullable=False)
    skill_raw = Column(String(256), nullable=False)
    skill_norm = Column(String(256), nullable=True)
    category = Column(String(128), nullable=True)
    proficiency = Column(String(128), nullable=True)
    claimed_only = Column(Boolean, nullable=False, default=False)
    evidence_ids = Column(JSON, nullable=False, default=list)

    __table_args__ = (
        Index("ix_resume_skills_profile_id", "profile_id"),
        Index("ix_resume_skills_norm", "skill_norm"),
    )


class JDProfile(Base):
    """immutable extraction artifact；五元组版本唯一。"""

    __tablename__ = "jd_profiles"

    id = Column(Uuid, primary_key=True, default=_pk_uuid, server_default=text("gen_random_uuid()"))
    document_id = Column(Uuid, ForeignKey("documents.id"), nullable=False)
    parsed_document_id = Column(Uuid, ForeignKey("parsed_documents.id"), nullable=False)
    pipeline_version = Column(String(128), nullable=False)
    extraction_schema_version = Column(String(64), nullable=False)
    prompt_version = Column(String(128), nullable=False)
    llm_model = Column(String(128), nullable=False)
    full_dump = Column(JSON, nullable=False)
    extraction_warnings = Column(JSON, nullable=False, default=list)
    extracted_at = Column(DateTime(timezone=True), nullable=False, server_default=_CLOCK)

    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "pipeline_version",
            "extraction_schema_version",
            "prompt_version",
            "llm_model",
            name="uq_jd_profile_fingerprint",
        ),
        Index("ix_jd_profiles_document_id", "document_id"),
    )


class JDRequirement(Base):
    __tablename__ = "jd_requirements"
    id = Column(Uuid, primary_key=True, default=_pk_uuid, server_default=text("gen_random_uuid()"))
    profile_id = Column(Uuid, ForeignKey("jd_profiles.id"), nullable=False)
    req_type = Column(String(32), nullable=False)
    operator = Column(String(32), nullable=False)
    value = Column(JSON, nullable=False, default=dict)
    weight = Column(Float, nullable=False, default=1.0)
    is_hard = Column(Boolean, nullable=False)
    source_text = Column(Text, nullable=True)
    anchors = Column(JSON, nullable=False, default=list)

    __table_args__ = (
        Index("ix_jd_requirements_profile_id", "profile_id"),
        Index("ix_jd_requirements_type", "profile_id", "req_type"),
    )


# ================================================================ analysis-scoped

class Analysis(Base):
    """任务队列 + 版本快照 + artifact 显式绑定（v0.2.3）。

    lease 字段：lease_expires_at / last_heartbeat_at 判定一律使用
    clock_timestamp()（见 db/repositories/analyses.py）。
    """

    __tablename__ = "analyses"

    id = Column(Uuid, primary_key=True, default=_pk_uuid, server_default=text("gen_random_uuid()"))
    resume_document_id = Column(Uuid, ForeignKey("documents.id"), nullable=False)
    jd_document_id = Column(Uuid, ForeignKey("documents.id"), nullable=False)
    resume_profile_id = Column(Uuid, ForeignKey("resume_profiles.id"), nullable=True)
    jd_profile_id = Column(Uuid, ForeignKey("jd_profiles.id"), nullable=True)
    status = Column(String(32), nullable=False)
    current_phase = Column(String(64), nullable=True)
    idempotency_key = Column(String(128), nullable=True)
    graph_thread_id = Column(String(128), nullable=True)

    claimed_by = Column(String(256), nullable=True)
    claim_token = Column(Uuid, nullable=True)
    lease_expires_at = Column(DateTime(timezone=True), nullable=True)
    last_heartbeat_at = Column(DateTime(timezone=True), nullable=True)
    run_attempts = Column(Integer, nullable=False, default=0)
    requeue_count = Column(Integer, nullable=False, default=0)
    next_command = Column(JSON, nullable=True)

    pipeline_version = Column(String(128), nullable=False)
    extraction_schema_version = Column(String(64), nullable=False)
    prompt_version = Column(String(128), nullable=True)
    ruleset_version = Column(String(128), nullable=True)
    scoring_version = Column(String(128), nullable=True)
    llm_model = Column(String(128), nullable=True)
    embedding_model = Column(String(128), nullable=True)
    config_snapshot = Column(JSON, nullable=True)

    llm_attempts_used = Column(Integer, nullable=False, default=0)
    llm_budget_exceeded = Column(Boolean, nullable=False, default=False)
    created_at, updated_at = _audit_cols()

    __table_args__ = (
        # Phase 3 引入确定性的成功终态 `succeeded`（确定性引擎完成）。
        # awaiting_review/finalized/rejected 仍属 HITL 阶段（ADR-009，Phase 4+）。
        CheckConstraint(
            "status IN ('queued','running','succeeded','awaiting_review','finalized','rejected','failed')",
            name="status_allowed",
        ),
        Index("ix_analyses_queued", "status", "created_at", postgresql_where=text("status = 'queued'")),
        Index("ix_analyses_lease", "status", "lease_expires_at"),
        UniqueConstraint("idempotency_key", name="uq_analyses_idempotency_key"),
        UniqueConstraint("graph_thread_id", name="uq_analyses_graph_thread_id"),
    )


class HardConstraintResult(Base):
    __tablename__ = "hard_constraint_results"
    __table_args__ = (
        # 幂等 identity = (analysis_id, requirement_id, ruleset_version)：
        # 同一 analysis 在同一 ruleset 下对同一 requirement 只允许一条结果（Phase 3 §25）。
        UniqueConstraint(
            "analysis_id",
            "requirement_id",
            "ruleset_version",
            name="uq_hard_constraint_result",
        ),
        Index("ix_hard_constraint_results_analysis_id", "analysis_id"),
    )

    id = Column(Uuid, primary_key=True, default=_pk_uuid, server_default=text("gen_random_uuid()"))
    analysis_id = Column(Uuid, ForeignKey("analyses.id"), nullable=False)
    requirement_id = Column(Uuid, ForeignKey("jd_requirements.id"), nullable=False)
    constraint_type = Column(String(32), nullable=False)  # jd_requirements.req_type 快照
    result = Column(String(16), nullable=False)  # MET | NOT_MET | UNKNOWN
    basis = Column(String(16), nullable=False)  # deterministic | llm_extracted | human
    ruleset_version = Column(String(128), nullable=False)
    reason_code = Column(String(64), nullable=False)  # deterministic reason code（非自然语言）
    evidence_ids = Column(JSON, nullable=False, default=list)
    note = Column(Text, nullable=True)
    reviewer_override = Column(String(16), nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    trace_id = Column(Uuid, nullable=True)
    created_at = _created_col()


class SkillMatchResult(Base):
    __tablename__ = "skill_match_results"
    __table_args__ = (
        UniqueConstraint(
            "analysis_id",
            "jd_requirement_id",
            "ruleset_version",
            name="uq_skill_match_result",
        ),
        Index("ix_skill_match_results_analysis_id", "analysis_id"),
    )

    id = Column(Uuid, primary_key=True, default=_pk_uuid, server_default=text("gen_random_uuid()"))
    analysis_id = Column(Uuid, ForeignKey("analyses.id"), nullable=False)
    jd_requirement_id = Column(Uuid, ForeignKey("jd_requirements.id"), nullable=False)
    resume_skill_id = Column(Uuid, ForeignKey("resume_skills.id"), nullable=True)
    status = Column(String(16), nullable=False)
    ruleset_version = Column(String(128), nullable=False)
    norm_used = Column(String(256), nullable=True)
    score_contribution = Column(Float, nullable=False, default=0.0)
    evidence_ids = Column(JSON, nullable=False, default=list)
    trace_id = Column(Uuid, nullable=True)
    created_at = _created_col()


class DecisionTrace(Base):
    __tablename__ = "decision_traces"
    __table_args__ = (
        # 重放同一 analysis 时 trace 必须收敛到同一行（deterministic，不重复累积）。
        UniqueConstraint(
            "analysis_id",
            "decision_type",
            "decision_key",
            name="uq_decision_trace",
        ),
        Index("ix_decision_traces_analysis", "analysis_id", "decision_type"),
    )

    id = Column(Uuid, primary_key=True, default=_pk_uuid, server_default=text("gen_random_uuid()"))
    analysis_id = Column(Uuid, ForeignKey("analyses.id"), nullable=False)
    decision_type = Column(String(32), nullable=False)
    decision_key = Column(String(512), nullable=False)
    chain = Column(JSON, nullable=False)
    created_at = _created_col()


class ScoreSnapshot(Base):
    __tablename__ = "score_snapshots"
    __table_args__ = (
        UniqueConstraint("analysis_id", "kind", name="uq_score_snapshot"),
        Index("ix_score_snapshots_analysis_id", "analysis_id"),
    )

    id = Column(Uuid, primary_key=True, default=_pk_uuid, server_default=text("gen_random_uuid()"))
    analysis_id = Column(Uuid, ForeignKey("analyses.id"), nullable=False)
    kind = Column(String(16), nullable=False)  # base | counterfactual
    total = Column(Float, nullable=False, default=0.0)
    per_section = Column(JSON, nullable=False, default=list)
    flags = Column(JSON, nullable=False, default=list)
    scoring_version = Column(String(128), nullable=True)
    hypothesis = Column(JSON, nullable=True)
    created_at = _created_col()


class Critique(Base):
    """critique artifact（Phase 4 §20/§21）。

    - 幂等 identity：UNIQUE(analysis_id, fingerprint)，同配置重跑不产生重复行；
    - `status` = 是否成功产出（ok/unavailable），与 `validation_status`（pending/
      validated/rejected）独立；
    - 只读 artifact：写入后不改（新版本=新行，version 递增）。
    """

    __tablename__ = "critiques"
    __table_args__ = (
        UniqueConstraint("analysis_id", "fingerprint", name="uq_critique_fingerprint"),
        Index("ix_critiques_analysis_id", "analysis_id"),
    )

    id = Column(Uuid, primary_key=True, default=_pk_uuid, server_default=text("gen_random_uuid()"))
    analysis_id = Column(Uuid, ForeignKey("analyses.id"), nullable=False)
    version = Column(Integer, nullable=False, default=1)
    status = Column(String(16), nullable=False)  # ok | unavailable
    provider = Column(String(128), nullable=False)
    model = Column(String(128), nullable=False)
    prompt_version = Column(String(128), nullable=True)
    schema_version = Column(String(64), nullable=False)
    content = Column(JSON, nullable=False)
    validation_status = Column(String(16), nullable=False)  # pending | validated | rejected
    citations_validated = Column(Boolean, nullable=False, default=False)
    fingerprint = Column(String(128), nullable=False)
    latency_ms = Column(Integer, nullable=True)
    tokens_in = Column(Integer, nullable=True)
    tokens_out = Column(Integer, nullable=True)
    created_at = _created_col()


class LlmAttemptLog(Base):
    __tablename__ = "llm_attempt_log"

    id = Column(Uuid, primary_key=True, default=_pk_uuid, server_default=text("gen_random_uuid()"))
    analysis_id = Column(Uuid, ForeignKey("analyses.id"), nullable=False)
    attempt_no = Column(Integer, nullable=False)
    phase = Column(String(64), nullable=True)
    ts = Column(DateTime(timezone=True), nullable=False, server_default=_CLOCK)

    __table_args__ = (
        UniqueConstraint("analysis_id", "attempt_no", name="uq_llm_attempt_log"),
        Index("ix_llm_attempt_log_analysis_id", "analysis_id"),
    )


class Report(Base):
    """Evidence-Grounded Report（Phase 4 §22–§25）。

    - 每代生成 = 新行（UNIQUE(analysis_id, version)），final 版本不可覆盖；
    - stage：draft → validated → final（final 后不可再改）；
    - fingerprint 固化 analysis + builder + 确定性结果快照 + critique fingerprint。
    """

    __tablename__ = "reports"

    id = Column(Uuid, primary_key=True, default=_pk_uuid, server_default=text("gen_random_uuid()"))
    analysis_id = Column(Uuid, ForeignKey("analyses.id"), nullable=False)
    version = Column(Integer, nullable=False, default=1)
    stage = Column(String(16), nullable=False)  # draft | validated | final
    content = Column(JSON, nullable=False)
    content_md = Column(Text, nullable=False)
    meta = Column(JSON, nullable=False, default=dict)
    fingerprint = Column(String(128), nullable=False)
    published_at = Column(DateTime(timezone=True), nullable=True)
    created_at, updated_at = _audit_cols()

    __table_args__ = (
        UniqueConstraint("analysis_id", "version", name="uq_reports_analysis_version"),
        Index("ix_reports_analysis_id", "analysis_id"),
    )


class Review(Base):
    """HITL review 记录（Phase 4 §27–§31）。

    - 每次 review action = 新行（append-only audit）；
    - `from_state`/`to_state` 记录状态转移（谁/何时/把什么改成什么），详情进 audit_log；
    - 并发安全由 analyses 上的**条件 UPDATE** 承担（一次只有一个 reviewer 生效），
      `version` 供乐观锁审计；不存 reviewer 密码/凭据。
    """

    __tablename__ = "reviews"
    __table_args__ = (Index("ix_reviews_analysis_id", "analysis_id"),)

    id = Column(Uuid, primary_key=True, default=_pk_uuid, server_default=text("gen_random_uuid()"))
    analysis_id = Column(Uuid, ForeignKey("analyses.id"), nullable=False)
    decision = Column(String(32), nullable=False)  # approve | request_changes | reject
    comments = Column(Text, nullable=True)
    overrides = Column(JSON, nullable=True)
    reviewed_by = Column(String(256), nullable=True)
    from_state = Column(String(32), nullable=True)
    to_state = Column(String(32), nullable=True)
    version = Column(Integer, nullable=False, default=1)
    created_at, updated_at = _audit_cols()


class JobRun(Base):
    __tablename__ = "job_runs"
    __table_args__ = (Index("ix_job_runs_analysis_id", "analysis_id"),)

    id = Column(Uuid, primary_key=True, default=_pk_uuid, server_default=text("gen_random_uuid()"))
    analysis_id = Column(Uuid, ForeignKey("analyses.id"), nullable=False)
    event = Column(String(64), nullable=False)
    payload = Column(JSON, nullable=True)
    created_at = _created_col()


class AuditLog(Base):
    __tablename__ = "audit_log"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    actor = Column(String(256), nullable=False)
    action = Column(String(128), nullable=False)
    entity_id = Column(Uuid, nullable=True)
    detail = Column(JSON, nullable=True)
    at = _event_ts_col()

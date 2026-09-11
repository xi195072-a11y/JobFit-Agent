"""baseline: JobFit Agent Phase 1 schema (architecture v0.2.3)

Revision ID: 0001_baseline
Revises:
Create Date: 2026-09-09

依赖：PostgreSQL 需启用 pgvector（CREATE EXTENSION vector）。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None

_CLOCK = sa.text("clock_timestamp()")
_GEN_UUID = sa.text("gen_random_uuid()")


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # ------------------------------------------------------------ document-scoped
    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(), server_default=_GEN_UUID, primary_key=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("original_filename", sa.String(length=512), nullable=False),
        sa.Column("mime_type", sa.String(length=128), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_path", sa.String(length=1024), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_CLOCK, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_CLOCK, nullable=False),
        sa.CheckConstraint("kind IN ('resume', 'jd')", name="kind_allowed"),
        sa.UniqueConstraint("sha256", name="uq_documents_sha256"),
    )

    op.create_table(
        "parsed_documents",
        sa.Column("id", sa.Uuid(), server_default=_GEN_UUID, primary_key=True),
        sa.Column("document_id", sa.Uuid(), sa.ForeignKey("documents.id"), nullable=False),
        sa.Column("parser_version", sa.String(length=64), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("pages", sa.JSON(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_CLOCK, nullable=False),
        sa.UniqueConstraint("document_id", "parser_version", name="uq_parsed_document_version"),
    )
    op.create_index("ix_parsed_documents_document_id", "parsed_documents", ["document_id"])

    op.create_table(
        "document_chunks",
        sa.Column("id", sa.Uuid(), server_default=_GEN_UUID, primary_key=True),
        sa.Column("parsed_document_id", sa.Uuid(), sa.ForeignKey("parsed_documents.id"), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("char_start", sa.Integer(), nullable=False),
        sa.Column("char_end", sa.Integer(), nullable=False),
        sa.Column("span_sha256", sa.String(length=64), nullable=False),
        sa.Column("embedding", Vector(1024), nullable=True),
        sa.Column("embedding_model", sa.String(length=128), nullable=True),
        sa.UniqueConstraint("parsed_document_id", "chunk_index", name="uq_document_chunk_index"),
    )
    op.create_index("ix_document_chunks_parsed_document_id", "document_chunks", ["parsed_document_id"])

    op.create_table(
        "resume_profiles",
        sa.Column("id", sa.Uuid(), server_default=_GEN_UUID, primary_key=True),
        sa.Column("document_id", sa.Uuid(), sa.ForeignKey("documents.id"), nullable=False),
        sa.Column("parsed_document_id", sa.Uuid(), sa.ForeignKey("parsed_documents.id"), nullable=False),
        sa.Column("pipeline_version", sa.String(length=128), nullable=False),
        sa.Column("extraction_schema_version", sa.String(length=64), nullable=False),
        sa.Column("prompt_version", sa.String(length=128), nullable=False),
        sa.Column("llm_model", sa.String(length=128), nullable=False),
        sa.Column("full_dump", sa.JSON(), nullable=False),
        sa.Column("name_sha256", sa.String(length=64), nullable=True),
        sa.Column("phone_sha256", sa.String(length=64), nullable=True),
        sa.Column("email_sha256", sa.String(length=64), nullable=True),
        sa.Column("extraction_warnings", sa.JSON(), nullable=False),
        sa.Column("extracted_at", sa.DateTime(timezone=True), server_default=_CLOCK, nullable=False),
        sa.UniqueConstraint(
            "document_id",
            "pipeline_version",
            "extraction_schema_version",
            "prompt_version",
            "llm_model",
            name="uq_resume_profile_fingerprint",
        ),
    )
    op.create_index("ix_resume_profiles_document_id", "resume_profiles", ["document_id"])

    op.create_table(
        "resume_education",
        sa.Column("id", sa.Uuid(), server_default=_GEN_UUID, primary_key=True),
        sa.Column("profile_id", sa.Uuid(), sa.ForeignKey("resume_profiles.id"), nullable=False),
        sa.Column("school", sa.String(length=512), nullable=True),
        sa.Column("degree", sa.String(length=128), nullable=True),
        sa.Column("major", sa.String(length=256), nullable=True),
        sa.Column("start_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("gpa", sa.Float(), nullable=True),
        sa.Column("honor", sa.String(length=256), nullable=True),
        sa.Column("bullet_evidence_ids", sa.JSON(), nullable=False),
        sa.Column("anchors", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_CLOCK, nullable=False),
    )
    op.create_index("ix_resume_education_profile_id", "resume_education", ["profile_id"])

    op.create_table(
        "resume_experiences",
        sa.Column("id", sa.Uuid(), server_default=_GEN_UUID, primary_key=True),
        sa.Column("profile_id", sa.Uuid(), sa.ForeignKey("resume_profiles.id"), nullable=False),
        sa.Column("company", sa.String(length=512), nullable=True),
        sa.Column("title", sa.String(length=256), nullable=True),
        sa.Column("location", sa.String(length=256), nullable=True),
        sa.Column("start_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("bullets", sa.JSON(), nullable=False),
        sa.Column("bullet_evidence_ids", sa.JSON(), nullable=False),
        sa.Column("anchors", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_CLOCK, nullable=False),
    )
    op.create_index("ix_resume_experiences_profile_id", "resume_experiences", ["profile_id"])

    op.create_table(
        "resume_skills",
        sa.Column("id", sa.Uuid(), server_default=_GEN_UUID, primary_key=True),
        sa.Column("profile_id", sa.Uuid(), sa.ForeignKey("resume_profiles.id"), nullable=False),
        sa.Column("skill_raw", sa.String(length=256), nullable=False),
        sa.Column("skill_norm", sa.String(length=256), nullable=True),
        sa.Column("category", sa.String(length=128), nullable=True),
        sa.Column("proficiency", sa.String(length=128), nullable=True),
        sa.Column("claimed_only", sa.Boolean(), nullable=False),
        sa.Column("evidence_ids", sa.JSON(), nullable=False),
    )
    op.create_index("ix_resume_skills_profile_id", "resume_skills", ["profile_id"])
    op.create_index("ix_resume_skills_norm", "resume_skills", ["skill_norm"])

    op.create_table(
        "jd_profiles",
        sa.Column("id", sa.Uuid(), server_default=_GEN_UUID, primary_key=True),
        sa.Column("document_id", sa.Uuid(), sa.ForeignKey("documents.id"), nullable=False),
        sa.Column("parsed_document_id", sa.Uuid(), sa.ForeignKey("parsed_documents.id"), nullable=False),
        sa.Column("pipeline_version", sa.String(length=128), nullable=False),
        sa.Column("extraction_schema_version", sa.String(length=64), nullable=False),
        sa.Column("prompt_version", sa.String(length=128), nullable=False),
        sa.Column("llm_model", sa.String(length=128), nullable=False),
        sa.Column("full_dump", sa.JSON(), nullable=False),
        sa.Column("extraction_warnings", sa.JSON(), nullable=False),
        sa.Column("extracted_at", sa.DateTime(timezone=True), server_default=_CLOCK, nullable=False),
        sa.UniqueConstraint(
            "document_id",
            "pipeline_version",
            "extraction_schema_version",
            "prompt_version",
            "llm_model",
            name="uq_jd_profile_fingerprint",
        ),
    )
    op.create_index("ix_jd_profiles_document_id", "jd_profiles", ["document_id"])

    op.create_table(
        "jd_requirements",
        sa.Column("id", sa.Uuid(), server_default=_GEN_UUID, primary_key=True),
        sa.Column("profile_id", sa.Uuid(), sa.ForeignKey("jd_profiles.id"), nullable=False),
        sa.Column("req_type", sa.String(length=32), nullable=False),
        sa.Column("operator", sa.String(length=32), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("weight", sa.Float(), nullable=False),
        sa.Column("is_hard", sa.Boolean(), nullable=False),
        sa.Column("source_text", sa.Text(), nullable=True),
        sa.Column("anchors", sa.JSON(), nullable=False),
    )
    op.create_index("ix_jd_requirements_profile_id", "jd_requirements", ["profile_id"])
    op.create_index("ix_jd_requirements_type", "jd_requirements", ["profile_id", "req_type"])

    # ------------------------------------------------------------ analysis-scoped
    op.create_table(
        "analyses",
        sa.Column("id", sa.Uuid(), server_default=_GEN_UUID, primary_key=True),
        sa.Column("resume_document_id", sa.Uuid(), sa.ForeignKey("documents.id"), nullable=False),
        sa.Column("jd_document_id", sa.Uuid(), sa.ForeignKey("documents.id"), nullable=False),
        sa.Column("resume_profile_id", sa.Uuid(), sa.ForeignKey("resume_profiles.id"), nullable=True),
        sa.Column("jd_profile_id", sa.Uuid(), sa.ForeignKey("jd_profiles.id"), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("current_phase", sa.String(length=64), nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("graph_thread_id", sa.String(length=128), nullable=True),
        sa.Column("claimed_by", sa.String(length=256), nullable=True),
        sa.Column("claim_token", sa.Uuid(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("run_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("requeue_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_command", sa.JSON(), nullable=True),
        sa.Column("pipeline_version", sa.String(length=128), nullable=False),
        sa.Column("extraction_schema_version", sa.String(length=64), nullable=False),
        sa.Column("prompt_version", sa.String(length=128), nullable=True),
        sa.Column("ruleset_version", sa.String(length=128), nullable=True),
        sa.Column("scoring_version", sa.String(length=128), nullable=True),
        sa.Column("llm_model", sa.String(length=128), nullable=True),
        sa.Column("embedding_model", sa.String(length=128), nullable=True),
        sa.Column("config_snapshot", sa.JSON(), nullable=True),
        sa.Column("llm_attempts_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("llm_budget_exceeded", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_CLOCK, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_CLOCK, nullable=False),
        sa.CheckConstraint(
            "status IN ('queued','running','awaiting_review','finalized','rejected','failed')",
            name="status_allowed",
        ),
        sa.UniqueConstraint("idempotency_key", name="uq_analyses_idempotency_key"),
        sa.UniqueConstraint("graph_thread_id", name="uq_analyses_graph_thread_id"),
    )
    op.create_index("ix_analyses_queued", "analyses", ["status", "created_at"],
                    postgresql_where=sa.text("status = 'queued'"))
    op.create_index("ix_analyses_lease", "analyses", ["status", "lease_expires_at"])

    op.create_table(
        "hard_constraint_results",
        sa.Column("id", sa.Uuid(), server_default=_GEN_UUID, primary_key=True),
        sa.Column("analysis_id", sa.Uuid(), sa.ForeignKey("analyses.id"), nullable=False),
        sa.Column("requirement_id", sa.Uuid(), sa.ForeignKey("jd_requirements.id"), nullable=False),
        sa.Column("result", sa.String(length=16), nullable=False),
        sa.Column("basis", sa.String(length=16), nullable=False),
        sa.Column("evidence_ids", sa.JSON(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("reviewer_override", sa.String(length=16), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("trace_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_CLOCK, nullable=False),
        sa.UniqueConstraint("analysis_id", "requirement_id", name="uq_hard_constraint_result"),
    )
    op.create_index("ix_hard_constraint_results_analysis_id", "hard_constraint_results", ["analysis_id"])

    op.create_table(
        "skill_match_results",
        sa.Column("id", sa.Uuid(), server_default=_GEN_UUID, primary_key=True),
        sa.Column("analysis_id", sa.Uuid(), sa.ForeignKey("analyses.id"), nullable=False),
        sa.Column("jd_requirement_id", sa.Uuid(), sa.ForeignKey("jd_requirements.id"), nullable=False),
        sa.Column("resume_skill_id", sa.Uuid(), sa.ForeignKey("resume_skills.id"), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("norm_used", sa.String(length=256), nullable=True),
        sa.Column("score_contribution", sa.Float(), nullable=False),
        sa.Column("evidence_ids", sa.JSON(), nullable=False),
        sa.Column("trace_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_CLOCK, nullable=False),
    )
    op.create_index("ix_skill_match_results_analysis_id", "skill_match_results", ["analysis_id"])

    op.create_table(
        "decision_traces",
        sa.Column("id", sa.Uuid(), server_default=_GEN_UUID, primary_key=True),
        sa.Column("analysis_id", sa.Uuid(), sa.ForeignKey("analyses.id"), nullable=False),
        sa.Column("decision_type", sa.String(length=32), nullable=False),
        sa.Column("decision_key", sa.String(length=512), nullable=False),
        sa.Column("chain", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_CLOCK, nullable=False),
    )
    op.create_index("ix_decision_traces_analysis", "decision_traces",
                    ["analysis_id", "decision_type"])

    op.create_table(
        "score_snapshots",
        sa.Column("id", sa.Uuid(), server_default=_GEN_UUID, primary_key=True),
        sa.Column("analysis_id", sa.Uuid(), sa.ForeignKey("analyses.id"), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("total", sa.Float(), nullable=False),
        sa.Column("per_section", sa.JSON(), nullable=False),
        sa.Column("flags", sa.JSON(), nullable=False),
        sa.Column("scoring_version", sa.String(length=128), nullable=True),
        sa.Column("hypothesis", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_CLOCK, nullable=False),
    )
    op.create_index("ix_score_snapshots_analysis_id", "score_snapshots", ["analysis_id"])

    op.create_table(
        "critiques",
        sa.Column("id", sa.Uuid(), server_default=_GEN_UUID, primary_key=True),
        sa.Column("analysis_id", sa.Uuid(), sa.ForeignKey("analyses.id"), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("prompt_version", sa.String(length=128), nullable=True),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("citations_validated", sa.Boolean(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("tokens_in", sa.Integer(), nullable=True),
        sa.Column("tokens_out", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_CLOCK, nullable=False),
    )
    op.create_index("ix_critiques_analysis_id", "critiques", ["analysis_id"])

    op.create_table(
        "llm_attempt_log",
        sa.Column("id", sa.Uuid(), server_default=_GEN_UUID, primary_key=True),
        sa.Column("analysis_id", sa.Uuid(), sa.ForeignKey("analyses.id"), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("phase", sa.String(length=64), nullable=True),
        sa.Column("ts", sa.DateTime(timezone=True), server_default=_CLOCK, nullable=False),
        sa.UniqueConstraint("analysis_id", "attempt_no", name="uq_llm_attempt_log"),
    )
    op.create_index("ix_llm_attempt_log_analysis_id", "llm_attempt_log", ["analysis_id"])

    op.create_table(
        "reports",
        sa.Column("id", sa.Uuid(), server_default=_GEN_UUID, primary_key=True),
        sa.Column("analysis_id", sa.Uuid(), sa.ForeignKey("analyses.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(length=16), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("content_md", sa.Text(), nullable=False),
        sa.Column("meta", sa.JSON(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_CLOCK, nullable=False),
        sa.UniqueConstraint("analysis_id", name="uq_reports_analysis_id"),
    )

    op.create_table(
        "reviews",
        sa.Column("id", sa.Uuid(), server_default=_GEN_UUID, primary_key=True),
        sa.Column("analysis_id", sa.Uuid(), sa.ForeignKey("analyses.id"), nullable=False),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("comments", sa.Text(), nullable=True),
        sa.Column("overrides", sa.JSON(), nullable=True),
        sa.Column("reviewed_by", sa.String(length=256), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_CLOCK, nullable=False),
    )
    op.create_index("ix_reviews_analysis_id", "reviews", ["analysis_id"])

    op.create_table(
        "job_runs",
        sa.Column("id", sa.Uuid(), server_default=_GEN_UUID, primary_key=True),
        sa.Column("analysis_id", sa.Uuid(), sa.ForeignKey("analyses.id"), nullable=False),
        sa.Column("event", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_CLOCK, nullable=False),
    )
    op.create_index("ix_job_runs_analysis_id", "job_runs", ["analysis_id"])

    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("actor", sa.String(length=256), nullable=False),
        sa.Column("action", sa.String(length=128), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=True),
        sa.Column("detail", sa.JSON(), nullable=True),
        sa.Column("at", sa.DateTime(timezone=True), server_default=_CLOCK, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("audit_log")
    op.drop_table("job_runs")
    op.drop_table("reviews")
    op.drop_table("reports")
    op.drop_table("llm_attempt_log")
    op.drop_table("critiques")
    op.drop_table("score_snapshots")
    op.drop_table("decision_traces")
    op.drop_table("skill_match_results")
    op.drop_table("hard_constraint_results")
    op.drop_table("analyses")
    op.drop_table("jd_requirements")
    op.drop_table("jd_profiles")
    op.drop_table("resume_skills")
    op.drop_table("resume_experiences")
    op.drop_table("resume_education")
    op.drop_table("resume_profiles")
    op.drop_table("document_chunks")
    op.drop_table("parsed_documents")
    op.drop_table("documents")
    op.execute("DROP EXTENSION IF EXISTS vector")

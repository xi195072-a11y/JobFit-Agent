"""typed graph state（禁止用任意 dict 作为核心 state）。

Phase 2 与 Phase 3 共用**同一个** WorkflowState（architecture §4.2 的 GraphState）：
抽取阶段写入 artifact 绑定，分析阶段追加确定性结论摘要。
大对象（constraint/skill/score 的完整结构）保存在图构建期的 RunContext，
state 只保留可观测摘要与诊断字段。
"""

from __future__ import annotations

from typing import TypedDict


class WorkflowState(TypedDict, total=False):
    # 身份
    analysis_id: str
    claim_token: str
    worker_id: str
    # 版本/模型（来自 analyses 行快照）
    pipeline_version: str
    llm_model: str
    # 工作产物（document / artifact 绑定）
    resume_document_id: str
    jd_document_id: str
    resume_parsed_document_id: str | None
    jd_parsed_document_id: str | None
    resume_profile_id: str | None
    jd_profile_id: str | None
    resume_fingerprint: str | None
    jd_fingerprint: str | None
    resume_created: bool
    jd_created: bool
    # Phase 3 确定性结论摘要
    gate: str
    score_total: float
    constraint_count: int
    skill_match_count: int
    trace_count: int
    analysis_flags: list[str]
    # Phase 4 续接摘要（critique / report / awaiting_review）
    critique_status: str | None
    critique_validation: str | None
    critique_reused: bool
    critique_fingerprint: str | None
    report_version: int | None
    report_stage: str | None
    # 进度与错误（结构化）
    phase: str
    errors: list[str]


def initial_state(
    *,
    analysis_id: str,
    claim_token: str,
    worker_id: str,
    pipeline_version: str,
    llm_model: str,
    resume_document_id: str,
    jd_document_id: str,
) -> WorkflowState:
    return WorkflowState(
        analysis_id=analysis_id,
        claim_token=claim_token,
        worker_id=worker_id,
        pipeline_version=pipeline_version,
        llm_model=llm_model,
        resume_document_id=resume_document_id,
        jd_document_id=jd_document_id,
        resume_parsed_document_id=None,
        jd_parsed_document_id=None,
        resume_profile_id=None,
        jd_profile_id=None,
        resume_fingerprint=None,
        jd_fingerprint=None,
        resume_created=False,
        jd_created=False,
        phase="start",
        errors=[],
    )

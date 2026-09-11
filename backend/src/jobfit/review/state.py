"""HITL Review 状态机（Phase 4 §27–§30）。

只允许合法 transitions（非法转移直接拒绝，禁止任意 API 把 running/queued 直接改成终态）：

    awaiting_review --approve--> finalized      （要求 report validated + critique 非 rejected）
    awaiting_review --reject--> rejected
    awaiting_review --request_changes--> queued （重新执行，新 run/version，旧结果留审计）
    rejected        --requeue--> queued          （新 execution）

terminal：
    finalized 不可再转移（finalized report immutable，§25/§29）。
"""

from __future__ import annotations

from jobfit.core.enums import AnalysisStatus, ReviewDecision

# (from_state, decision) -> to_state；不在表内 => 非法转移。
TRANSITIONS: dict[tuple[str, str], str] = {
    (AnalysisStatus.AWAITING_REVIEW.value, ReviewDecision.APPROVE.value): AnalysisStatus.FINALIZED.value,
    (AnalysisStatus.AWAITING_REVIEW.value, ReviewDecision.REJECT.value): AnalysisStatus.REJECTED.value,
    (
        AnalysisStatus.AWAITING_REVIEW.value,
        ReviewDecision.REQUEST_CHANGES.value,
    ): AnalysisStatus.QUEUED.value,
    # 显式 requeue：rejected -> queued（§33：重新分析 = 新 execution，旧结果保留审计）
    (AnalysisStatus.REJECTED.value, ReviewDecision.REQUEST_CHANGES.value): AnalysisStatus.QUEUED.value,
}

# finalize 前置条件（§32）：critique 为 rejected 时禁止 finalize（unavailable 不阻止）。
FINALIZE_FORBIDDEN_CRITIQUE = "rejected"


def allowed_transition(from_state: str, decision: str) -> str | None:
    """返回目标状态；None = 非法转移。"""
    return TRANSITIONS.get((from_state, decision))


__all__ = ["FINALIZE_FORBIDDEN_CRITIQUE", "TRANSITIONS", "allowed_transition"]

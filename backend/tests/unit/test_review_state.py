# mypy: disable-error-code="arg-type,attr-defined,assignment,union-attr"
"""unit: HITL Review 状态机（Phase 4 §27–§30）——纯函数，不依赖 DB。

只允许合法 transitions；非法转移 / 终态转移 => None（由服务层抛 ValidationFailed）。
"""

from __future__ import annotations

from jobfit.core.enums import AnalysisStatus, ReviewDecision
from jobfit.review.state import allowed_transition


def test_legal_transitions() -> None:
    assert (
        allowed_transition(AnalysisStatus.AWAITING_REVIEW.value, ReviewDecision.APPROVE.value)
        == AnalysisStatus.FINALIZED.value
    )
    assert (
        allowed_transition(AnalysisStatus.AWAITING_REVIEW.value, ReviewDecision.REJECT.value)
        == AnalysisStatus.REJECTED.value
    )
    assert (
        allowed_transition(AnalysisStatus.AWAITING_REVIEW.value, ReviewDecision.REQUEST_CHANGES.value)
        == AnalysisStatus.QUEUED.value
    )
    # 显式 requeue：rejected -> queued（§33）
    assert (
        allowed_transition(AnalysisStatus.REJECTED.value, ReviewDecision.REQUEST_CHANGES.value)
        == AnalysisStatus.QUEUED.value
    )


def test_illegal_transitions_return_none() -> None:
    assert (
        allowed_transition(AnalysisStatus.QUEUED.value, ReviewDecision.APPROVE.value) is None
    )
    assert (
        allowed_transition(AnalysisStatus.RUNNING.value, ReviewDecision.REJECT.value) is None
    )
    assert (
        allowed_transition(AnalysisStatus.SUCCEEDED.value, ReviewDecision.APPROVE.value) is None
    )
    assert (
        allowed_transition(AnalysisStatus.FAILED.value, ReviewDecision.REQUEST_CHANGES.value) is None
    )


def test_terminal_states_are_frozen() -> None:
    # finalized 不可再转移（finalized report immutable，§25/§29）
    assert (
        allowed_transition(AnalysisStatus.FINALIZED.value, ReviewDecision.APPROVE.value) is None
    )
    assert (
        allowed_transition(AnalysisStatus.FINALIZED.value, ReviewDecision.REJECT.value) is None
    )
    assert (
        allowed_transition(AnalysisStatus.FINALIZED.value, ReviewDecision.REQUEST_CHANGES.value)
        is None
    )


def test_rejected_cannot_be_directly_approved_or_rejected() -> None:
    assert (
        allowed_transition(AnalysisStatus.REJECTED.value, ReviewDecision.APPROVE.value) is None
    )
    assert (
        allowed_transition(AnalysisStatus.REJECTED.value, ReviewDecision.REJECT.value) is None
    )

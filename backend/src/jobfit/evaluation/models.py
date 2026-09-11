"""Golden Evaluation 报告模型（Pydantic，机器可读 + 可渲染）。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class MetricSummary(BaseModel):
    """单个指标的确定性统计（pass 数 / 声明该指标的 case 数）。"""

    name: str
    description: str
    passed: int
    total: int
    rate: float


class CaseResult(BaseModel):
    """单个 golden case 的判定结果。"""

    name: str
    passed: bool
    declared_metrics: list[str] = Field(default_factory=list)
    metrics: dict[str, bool] = Field(default_factory=dict)
    reasons: list[str] = Field(default_factory=list)
    junit_status: str  # passed | failed | error | skipped | not_run


class EvaluationReport(BaseModel):
    """Phase 5 §31 要求的完整评测报告。"""

    schema_version: str = "phase5-evaluation.v1"
    generated_at: datetime
    golden_dir: str
    junit_xml: str
    metrics_source: str
    ran_pytest: bool

    total_cases: int
    passed: int
    failed: int

    junit_tests: int
    junit_failures: int
    junit_errors: int
    junit_skipped: int

    metrics: list[MetricSummary] = Field(default_factory=list)
    cases: list[CaseResult] = Field(default_factory=list)

    overall_passed: bool

    def summary_line(self) -> str:
        return (
            f"golden cases: total={self.total_cases} passed={self.passed} failed={self.failed}; "
            f"junit: tests={self.junit_tests} failures={self.junit_failures} "
            f"errors={self.junit_errors} skipped={self.junit_skipped}"
        )


__all__ = ["CaseResult", "EvaluationReport", "MetricSummary"]

"""Deterministic Golden Evaluation（Phase 5 §28–§31）。

**入口**：`python -m jobfit.evaluation`

职责：

1. 运行 `tests/golden/` 的全部 golden cases（subprocess 调 pytest，exit code 权威）；
2. 读取 test session 写出的机器可读指标（`JOBFIT_GOLDEN_METRICS_PATH`）；
3. 渲染 `phase5-evaluation.json` 与 `phase5-evaluation.md`。

**不**调用任何 LLM，**不**让模型自评（§54）：所有指标由 golden 测试中的确定性代码计算。
本包不 import tests/，也不提供任何 production fake provider（ADR-029/§51）。
"""

from __future__ import annotations

from jobfit.evaluation.models import CaseResult, EvaluationReport, MetricSummary
from jobfit.evaluation.report import render_json, render_markdown
from jobfit.evaluation.runner import (
    DEFAULT_METRIC_NAMES,
    build_report,
    load_golden_metrics,
    parse_junit,
    run_golden_cases,
)

__all__ = [
    "DEFAULT_METRIC_NAMES",
    "CaseResult",
    "EvaluationReport",
    "MetricSummary",
    "build_report",
    "load_golden_metrics",
    "parse_junit",
    "render_json",
    "render_markdown",
    "run_golden_cases",
]

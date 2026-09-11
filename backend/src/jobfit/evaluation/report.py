"""Golden Evaluation 报告渲染（JSON + Markdown，Phase 5 §31）。

只做**确定性渲染**：所有数字来自 `EvaluationReport`（由代码计算），
不含任何"模型自评"文案。
"""

from __future__ import annotations

import json

from jobfit.evaluation.models import EvaluationReport


def render_json(report: EvaluationReport) -> str:
    return json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n"


def render_markdown(report: EvaluationReport) -> str:
    lines: list[str] = [
        "# Phase 5 — Golden Evaluation Report",
        "",
        f"- schema: `{report.schema_version}`",
        f"- generated_at (UTC): `{report.generated_at.isoformat()}`",
        f"- golden dir: `{report.golden_dir}`",
        f"- metrics source: `{report.metrics_source}`",
        f"- ran pytest: `{report.ran_pytest}`",
        "",
        "## Totals",
        "",
        "| 指标 | 值 |",
        "| --- | --- |",
        f"| total cases | {report.total_cases} |",
        f"| passed | {report.passed} |",
        f"| failed | {report.failed} |",
        f"| junit tests | {report.junit_tests} |",
        f"| junit failures | {report.junit_failures} |",
        f"| junit errors | {report.junit_errors} |",
        f"| junit skipped | {report.junit_skipped} |",
        f"| overall | {'PASS' if report.overall_passed else 'FAIL'} |",
        "",
        "## Metrics（由确定性代码计算，非 LLM 自评）",
        "",
        "| metric | passed/total | rate | 语义 |",
        "| --- | --- | --- | --- |",
    ]
    for metric in report.metrics:
        lines.append(
            f"| `{metric.name}` | {metric.passed}/{metric.total} | "
            f"{(metric.rate * 100):.1f}% | {metric.description} |"
        )
    lines += [
        "",
        "## Cases",
        "",
        "| case | passed | junit | declared metrics | reasons |",
        "| --- | --- | --- | --- | --- |",
    ]
    for case in report.cases:
        reasons = "<br>".join(case.reasons) if case.reasons else "—"
        lines.append(
            f"| `{case.name}` | {'yes' if case.passed else 'no'} | {case.junit_status} | "
            f"{', '.join(case.declared_metrics)} | {reasons} |"
        )
    lines += [
        "",
        "> 说明：本报告不包含模型质量自评；全部指标均由 `tests/golden/` 中的确定性断言计算。",
        "> 未运行 live DeepSeek（无 `DEEPSEEK_API_KEY`）时，LLM 相关结论为 EXTERNAL CREDENTIAL BLOCKED，",
        "> 绝不记为 PASS（ADR-021/ADR-039）。",
        "",
    ]
    return "\n".join(lines)


__all__ = ["render_json", "render_markdown"]

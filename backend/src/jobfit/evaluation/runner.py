"""Golden Evaluation runner（Phase 5 §28）。

只用 stdlib + pydantic：

- `run_golden_cases`：以 subprocess 运行 `tests/golden`（exit code 权威，§52 精神）；
- `parse_junit`：解析 JUnit XML（tests/failures/errors/skipped + 每个 case 状态）；
- `load_golden_metrics`：读取 golden 测试写出的确定性指标；
- `build_report`：汇总为 `EvaluationReport`。

**不** import tests/、**不**调用 LLM、**不**做模型自评（§54）。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jobfit.evaluation.models import CaseResult, EvaluationReport, MetricSummary

#: 受控指标集合（§29）；新增指标必须同时更新 golden case 声明与此处。
DEFAULT_METRIC_NAMES: dict[str, str] = {
    "hard_constraint_exact_agreement": "gate / verdict 集合 / 约束条数与预期逐项一致",
    "skill_match_agreement": "技能匹配状态与预期逐项一致",
    "unknown_preservation": "UNKNOWN 完整保留（不写成 FALSE，也不被 LLM 改写）",
    "citation_validity": "citation 校验结论与预期一致（fabricated / 跨 analysis 一律 rejected）",
    "report_invariant_validity": "报告 stage 与分区不变量成立（含 final 不可变）",
    "decision_trace_completeness": "每条约束都有 trace_id，且 trace 链非空",
    "review_lifecycle": "HITL 状态机转移、终态与审计留痕正确",
}

_CASE_PATTERN = re.compile(r"^test_golden_case\[(?P<name>.+)\]$")


def run_golden_cases(
    *,
    golden_dir: Path,
    junit_xml: Path,
    metrics_path: Path,
    cwd: Path | None = None,
) -> int:
    """运行 golden suite；返回 pytest exit code（0 = 全部通过）。"""
    junit_xml.parent.mkdir(parents=True, exist_ok=True)
    metrics_path.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["JOBFIT_GOLDEN_METRICS_PATH"] = str(metrics_path)
    completed = subprocess.run(  # noqa: S603 - 固定 argv，无 shell
        [
            sys.executable,
            "-m",
            "pytest",
            str(golden_dir),
            "-p",
            "no:cacheprovider",
            "-q",
            f"--junitxml={junit_xml}",
        ],
        cwd=str(cwd) if cwd is not None else None,
        env=env,
        check=False,
    )
    return int(completed.returncode)


def parse_junit(junit_xml: Path) -> dict[str, Any]:
    """解析 JUnit XML => 汇总计数 + `{case_name: status}`。"""
    if not junit_xml.is_file():
        return {"tests": 0, "failures": 0, "errors": 0, "skipped": 0, "cases": {}}
    root = ET.parse(junit_xml).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    cases: dict[str, str] = {}
    for suite in suites:
        for key in totals:
            totals[key] += int(suite.attrib.get(key, 0))
        for testcase in suite.iter("testcase"):
            name = testcase.attrib.get("name", "")
            if testcase.find("failure") is not None:
                status = "failed"
            elif testcase.find("error") is not None:
                status = "error"
            elif testcase.find("skipped") is not None:
                status = "skipped"
            else:
                status = "passed"
            match = _CASE_PATTERN.match(name)
            if match:
                cases[match.group("name")] = status
    return {**totals, "cases": cases}


def load_golden_metrics(metrics_path: Path) -> dict[str, dict[str, Any]]:
    """读取 golden 测试写出的指标（`{case_name: {...}}`）。"""
    if not metrics_path.is_file():
        return {}
    payload = json.loads(metrics_path.read_text(encoding="utf-8"))
    return {entry["name"]: entry for entry in payload.get("cases", [])}


def build_report(
    *,
    golden_dir: Path,
    junit_xml: Path,
    metrics_path: Path,
    ran_pytest: bool,
    junit: dict[str, Any],
    metrics: dict[str, dict[str, Any]],
    metric_names: dict[str, str] | None = None,
) -> EvaluationReport:
    """把 JUnit 结果 + 确定性指标汇总为报告。"""
    names = metric_names or DEFAULT_METRIC_NAMES
    case_status: dict[str, str] = junit.get("cases", {})

    case_results: list[CaseResult] = []
    for name, entry in metrics.items():
        declared = list(entry.get("declared_metrics", []))
        measured = {key: bool(value) for key, value in (entry.get("metrics") or {}).items()}
        passed = bool(entry.get("passed")) and all(measured.get(key, False) for key in declared)
        case_results.append(
            CaseResult(
                name=name,
                passed=passed,
                declared_metrics=declared,
                metrics=measured,
                reasons=list(entry.get("reasons", [])),
                junit_status=case_status.get(name, "not_run"),
            )
        )

    metric_summaries: list[MetricSummary] = []
    for metric, description in names.items():
        applicable = [case for case in case_results if metric in case.declared_metrics]
        passed_count = sum(1 for case in applicable if case.metrics.get(metric, False))
        total = len(applicable)
        metric_summaries.append(
            MetricSummary(
                name=metric,
                description=description,
                passed=passed_count,
                total=total,
                rate=(passed_count / total) if total else 0.0,
            )
        )

    total_cases = len(case_results)
    passed_cases = sum(1 for case in case_results if case.passed)
    failed_cases = total_cases - passed_cases
    overall = (
        failed_cases == 0
        and int(junit.get("failures", 0)) == 0
        and int(junit.get("errors", 0)) == 0
        and total_cases > 0
    )

    return EvaluationReport(
        generated_at=datetime.now(timezone.utc),
        golden_dir=str(golden_dir),
        junit_xml=str(junit_xml),
        metrics_source=str(metrics_path),
        ran_pytest=ran_pytest,
        total_cases=total_cases,
        passed=passed_cases,
        failed=failed_cases,
        junit_tests=int(junit.get("tests", 0)),
        junit_failures=int(junit.get("failures", 0)),
        junit_errors=int(junit.get("errors", 0)),
        junit_skipped=int(junit.get("skipped", 0)),
        metrics=metric_summaries,
        cases=sorted(case_results, key=lambda case: case.name),
        overall_passed=overall,
    )


__all__ = [
    "DEFAULT_METRIC_NAMES",
    "build_report",
    "load_golden_metrics",
    "parse_junit",
    "run_golden_cases",
]

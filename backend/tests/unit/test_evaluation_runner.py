"""unit: Phase 5 evaluation runner（§28–§31）——纯函数，不依赖 DB。

验证：JUnit 解析、指标聚合、报告渲染确定性、指标集合受控。
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from jobfit.evaluation.models import EvaluationReport
from jobfit.evaluation.report import render_json, render_markdown
from jobfit.evaluation.runner import DEFAULT_METRIC_NAMES, build_report, parse_junit

_JUNIT = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    '<testsuites name="pytest tests">'
    '<testsuite name="pytest" errors="0" failures="1" skipped="1" tests="3" time="1.0">'
    '<testcase classname="tests.golden.test_golden_evaluation" '
    'name="test_golden_case[perfect_match]" />'
    '<testcase classname="tests.golden.test_golden_evaluation" '
    'name="test_golden_case[unknown]">'
    '<failure message="boom">trace</failure></testcase>'
    '<testcase classname="tests.golden.test_golden_evaluation" '
    'name="test_golden_dataset_covers_required_scenarios">'
    '<skipped message="no db" /></testcase>'
    "</testsuite></testsuites>\n"
)


def _write(tmp_path: Path, name: str, content: str) -> Path:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


def test_parse_junit_extracts_totals_and_case_status(tmp_path: Path) -> None:
    junit = _write(tmp_path, "junit.xml", _JUNIT)
    parsed = parse_junit(junit)
    assert parsed["tests"] == 3
    assert parsed["failures"] == 1
    assert parsed["skipped"] == 1
    assert parsed["cases"] == {"perfect_match": "passed", "unknown": "failed"}


def test_parse_junit_missing_file_is_empty(tmp_path: Path) -> None:
    parsed = parse_junit(tmp_path / "absent.xml")
    assert parsed == {"tests": 0, "failures": 0, "errors": 0, "skipped": 0, "cases": {}}


def _metrics_payload() -> dict:
    return {
        "perfect_match": {
            "declared_metrics": ["hard_constraint_exact_agreement", "citation_validity"],
            "metrics": {"hard_constraint_exact_agreement": True, "citation_validity": True},
            "passed": True,
            "reasons": [],
        },
        "citation_failure": {
            "declared_metrics": ["citation_validity"],
            "metrics": {"citation_validity": False},
            "passed": False,
            "reasons": ["citation contract mismatch"],
        },
    }


def test_build_report_aggregates_metrics_deterministically(tmp_path: Path) -> None:
    junit = _write(tmp_path, "junit.xml", _JUNIT)
    report = build_report(
        golden_dir=tmp_path,
        junit_xml=junit,
        metrics_path=tmp_path / "metrics.json",
        ran_pytest=True,
        junit=parse_junit(junit),
        metrics=_metrics_payload(),
    )
    assert report.total_cases == 2
    assert report.passed == 1
    assert report.failed == 1
    assert report.overall_passed is False

    by_name = {metric.name: metric for metric in report.metrics}
    # citation_validity 覆盖两个 case，1 通过 => rate 0.5
    assert by_name["citation_validity"].total == 2
    assert by_name["citation_validity"].passed == 1
    assert by_name["citation_validity"].rate == 0.5
    # 未被任何 case 声明的指标 total=0，rate=0（不虚增分母）
    assert by_name["review_lifecycle"].total == 0
    assert by_name["review_lifecycle"].rate == 0.0

    failed_case = next(case for case in report.cases if case.name == "citation_failure")
    assert failed_case.junit_status == "not_run"
    assert failed_case.reasons


def test_report_rendering_is_deterministic_and_machine_readable(tmp_path: Path) -> None:
    report = EvaluationReport(
        generated_at=datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
        golden_dir="tests/golden",
        junit_xml="junit.xml",
        metrics_source="metrics.json",
        ran_pytest=True,
        total_cases=1,
        passed=1,
        failed=0,
        junit_tests=1,
        junit_failures=0,
        junit_errors=0,
        junit_skipped=0,
        metrics=[],
        cases=[],
        overall_passed=True,
    )
    first = render_json(report)
    assert first == render_json(report)
    assert '"total_cases": 1' in first

    markdown = render_markdown(report)
    assert markdown == render_markdown(report)
    assert "PASS" in markdown
    # 明确声明非模型自评（§54）
    assert "非 LLM 自评" in markdown


def test_metric_names_cover_required_metrics() -> None:
    required = {
        "hard_constraint_exact_agreement",
        "skill_match_agreement",
        "unknown_preservation",
        "citation_validity",
        "report_invariant_validity",
        "decision_trace_completeness",
    }
    assert required <= set(DEFAULT_METRIC_NAMES)
    assert all(DEFAULT_METRIC_NAMES.values())

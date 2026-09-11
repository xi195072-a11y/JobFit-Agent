"""`python -m jobfit.evaluation` —— Phase 5 golden evaluation 入口（§28/§31）。

用法：

```
python -m jobfit.evaluation                       # 跑 golden + 渲染 JSON/MD
python -m jobfit.evaluation --no-run              # 只渲染已有 artifacts
python -m jobfit.evaluation --out-json X.json --out-md X.md
```

退出码：0 = 全部 case 通过（且 JUnit 无 failure/error）；1 = 有失败/错误；2 = 参数或环境问题。
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

from jobfit.evaluation.report import render_json, render_markdown
from jobfit.evaluation.runner import (
    build_report,
    load_golden_metrics,
    parse_junit,
    run_golden_cases,
)

#: __file__ = <repo>/backend/src/jobfit/evaluation/__main__.py
_REPO_ROOT = Path(__file__).resolve().parents[4]
_BACKEND_ROOT = Path(__file__).resolve().parents[3]


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m jobfit.evaluation")
    parser.add_argument(
        "--golden-dir",
        type=Path,
        default=_BACKEND_ROOT / "tests" / "golden",
        help="golden suite 目录（默认 backend/tests/golden）",
    )
    parser.add_argument(
        "--out-json",
        type=Path,
        default=_REPO_ROOT / "phase5-evaluation.json",
        help="JSON 报告输出路径",
    )
    parser.add_argument(
        "--out-md",
        type=Path,
        default=_REPO_ROOT / "phase5-evaluation.md",
        help="Markdown 报告输出路径",
    )
    parser.add_argument("--junit-xml", type=Path, default=None, help="JUnit XML 路径（默认临时文件）")
    parser.add_argument("--metrics", type=Path, default=None, help="指标 sidecar 路径（默认临时文件）")
    parser.add_argument(
        "--no-run",
        action="store_true",
        help="不运行 pytest，仅渲染已有 junit/metrics（需显式提供 --junit-xml/--metrics）",
    )
    parser.add_argument("--cwd", type=Path, default=_BACKEND_ROOT, help="pytest 工作目录")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    golden_dir: Path = args.golden_dir
    if not golden_dir.is_dir():
        print(f"[evaluation] golden dir not found: {golden_dir}", file=sys.stderr)
        return 2

    tmp_dir = Path(tempfile.mkdtemp(prefix="jobfit-eval-"))
    junit_xml: Path = args.junit_xml or (tmp_dir / "golden-junit.xml")
    metrics_path: Path = args.metrics or (tmp_dir / "golden-metrics.json")

    ran_pytest = False
    if not args.no_run:
        exit_code = run_golden_cases(
            golden_dir=golden_dir,
            junit_xml=junit_xml,
            metrics_path=metrics_path,
            cwd=args.cwd,
        )
        ran_pytest = True
    else:
        exit_code = 0
        if args.junit_xml is None or args.metrics is None:
            print(
                "[evaluation] --no-run requires explicit --junit-xml and --metrics",
                file=sys.stderr,
            )
            return 2

    junit = parse_junit(junit_xml)
    metrics = load_golden_metrics(metrics_path)
    report = build_report(
        golden_dir=golden_dir,
        junit_xml=junit_xml,
        metrics_path=metrics_path,
        ran_pytest=ran_pytest,
        junit=junit,
        metrics=metrics,
    )

    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(render_json(report), encoding="utf-8")
    args.out_md.write_text(render_markdown(report), encoding="utf-8")

    print(f"[evaluation] {report.summary_line()}")
    print(f"[evaluation] json -> {args.out_json}")
    print(f"[evaluation] md   -> {args.out_md}")
    print(f"[evaluation] overall: {'PASS' if report.overall_passed else 'FAIL'}")

    if not report.overall_passed:
        return 1
    return 0 if exit_code == 0 else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

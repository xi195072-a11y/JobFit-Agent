"""最小性能基线（Phase 6 §24）—— 不引入 benchmark 框架。

测量（少量迭代，合成数据）：
  1. API health latency
  2. analyses list latency
  3. deterministic analysis path（upload + create + run）
  4. critique + report generation
  5. frontend initial page（HTTP GET）

用法：
    python scripts/performance_baseline.py --api http://127.0.0.1:8010 --frontend http://127.0.0.1:3000
输出：控制台摘要 + phase6-performance.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import uuid
from pathlib import Path

try:
    import httpx
except ModuleNotFoundError:  # pragma: no cover
    print("[perf] httpx 未安装；请用 backend venv 运行")
    raise SystemExit(2) from None

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "backend" / "tests" / "fixtures"
OUT_PATH = REPO_ROOT / "phase6-performance.json"


def _stats(samples: list[float]) -> dict[str, float]:
    if not samples:
        return {"count": 0, "p50_ms": 0.0, "p95_ms": 0.0, "max_ms": 0.0}
    ordered = sorted(samples)
    p95_index = min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))
    return {
        "count": len(samples),
        "p50_ms": round(statistics.median(ordered), 2),
        "p95_ms": round(ordered[p95_index], 2),
        "max_ms": round(ordered[-1], 2),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="performance_baseline")
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--frontend", default=None)
    parser.add_argument("--iterations", type=int, default=10)
    args = parser.parse_args(argv)

    api = args.api.rstrip("/")
    results: dict[str, object] = {"api": api, "iterations": args.iterations}

    with httpx.Client(base_url=api, timeout=120.0) as client:
        # 1) health / list
        health: list[float] = []
        listing: list[float] = []
        for _ in range(args.iterations):
            start = time.perf_counter()
            client.get("/health")
            health.append((time.perf_counter() - start) * 1000)
            start = time.perf_counter()
            client.get("/analyses", params={"limit": 5})
            listing.append((time.perf_counter() - start) * 1000)
        results["health"] = _stats(health)
        results["analyses_list"] = _stats(listing)

        # 2/3/4) full deterministic + phase4 path（单次，含 LLM 替代 provider）
        token = uuid.uuid4().hex[:8]
        start = time.perf_counter()
        with (FIXTURES / "resume_match.txt").open("rb") as rh, (FIXTURES / "jd_match.txt").open("rb") as jh:
            resume = client.post("/documents", data={"kind": "resume"}, files={"file": ("resume_match.txt", rh, "text/plain")})
            jd = client.post("/documents", data={"kind": "jd"}, files={"file": ("jd_match.txt", jh, "text/plain")})
        created = client.post(
            "/analyses",
            json={
                "resume_document_id": resume.json()["id"],
                "jd_document_id": jd.json()["id"],
                "idempotency_key": f"perf-{token}",
            },
        )
        analysis_id = created.json()["id"]

        run_start = time.perf_counter()
        run = client.post(f"/analyses/{analysis_id}/run", json={})
        results["deterministic_path_ms"] = round((time.perf_counter() - run_start) * 1000, 2)
        results["deterministic_path_status"] = run.status_code
        results["upload_and_create_ms"] = round((run_start - start) * 1000, 2)

        if run.status_code == 200:
            critique_start = time.perf_counter()
            critique = client.post(f"/analyses/{analysis_id}/critique")
            results["critique_and_report_ms"] = round((time.perf_counter() - critique_start) * 1000, 2)
            results["critique_status"] = critique.status_code
        else:
            results["critique_and_report_ms"] = None
            results["critique_status"] = run.status_code
            results["note"] = "deterministic path blocked（无凭证）；critique/report 未测"

    if args.frontend:
        page: list[float] = []
        with httpx.Client(timeout=60.0) as web:
            for _ in range(min(5, args.iterations)):
                start = time.perf_counter()
                response = web.get(args.frontend.rstrip("/") + "/")
                page.append((time.perf_counter() - start) * 1000)
                assert response.status_code == 200, response.status_code
        results["frontend_initial_page"] = _stats(page)

    OUT_PATH.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))
    print(f"[perf] written -> {OUT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

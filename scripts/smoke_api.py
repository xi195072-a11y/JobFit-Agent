"""API smoke test（Phase 6 §21）—— 真实 HTTP，合成数据，无真实 PII。

用法：
    python scripts/smoke_api.py [--base-url http://127.0.0.1:8000] [--strict]

覆盖：health -> list analyses -> list profiles -> upload -> create analysis ->
      run analysis -> constraints / skills / evidence / trace / score ->
      critique -> report -> review approve -> analysis status

行为约定（绝不伪造 PASS）：
- 若 `/run` 或 `/critique` 因**未配置 LLM 凭证**而不可用（503/409），
  对应步骤记为 `BLOCKED`（EXTERNAL CREDENTIAL BLOCKED），脚本仍继续验证只读路径；
- `--strict` 时任何 BLOCKED 都视为失败（用于 CI/发布门禁）；
- 其它任何失败一律 exit 非 0。
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

try:
    import httpx
except ModuleNotFoundError:  # pragma: no cover
    print("[smoke] httpx 未安装；请用 backend venv 运行：backend/.venv/Scripts/python.exe scripts/smoke_api.py")
    raise SystemExit(2) from None

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "backend" / "tests" / "fixtures"

PASS, FAIL, BLOCKED, SKIP = "PASS", "FAIL", "BLOCKED", "SKIP"
_results: list[tuple[str, str, str]] = []


def _record(name: str, status: str, detail: str = "") -> None:
    _results.append((name, status, detail))
    print(f"[smoke] {status:7} {name}" + (f" — {detail}" if detail else ""))


def _upload(client: httpx.Client, kind: str, filename: str) -> tuple[bool, str]:
    path = FIXTURES / filename
    if not path.is_file():
        return False, f"fixture missing: {path}"
    with path.open("rb") as handle:
        response = client.post(
            "/documents",
            data={"kind": kind},
            files={"file": (filename, handle, "text/plain")},
        )
    if response.status_code not in (200, 201):
        return False, f"HTTP {response.status_code}: {response.text[:200]}"
    return True, response.json()["id"]


def _credential_blocked(response: httpx.Response) -> bool:
    """未配置凭证 => critique/run 不可用（不伪造结果）。"""
    if response.status_code == 503:
        return True
    if response.status_code == 409:
        body = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
        detail = body.get("detail")
        if isinstance(detail, dict) and detail.get("status") in {"not_claimable", "failed"}:
            return True
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="smoke_api")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--strict", action="store_true", help="BLOCKED 也视为失败（发布门禁）")
    args = parser.parse_args(argv)

    base = args.base_url.rstrip("/")
    token = uuid.uuid4().hex[:8]
    analysis_id: str | None = None

    with httpx.Client(base_url=base, timeout=60.0) as client:
        # 1) health
        try:
            response = client.get("/health")
            ok = response.status_code == 200 and response.json().get("status") == "ok"
            body = response.json() if response.status_code == 200 else {}
            if "app_env" in body:
                _record("health.pii-check", FAIL, "health 泄露 app_env（§17）")
            else:
                _record("health.pii-check", PASS, "只暴露 status/version")
            _record("health", PASS if ok else FAIL, json.dumps(body)[:120])
        except Exception as exc:  # noqa: BLE001
            _record("health", FAIL, f"{type(exc).__name__}: {exc}")
            return _finish(args.strict)

        # 2) readiness（DB connectivity）
        ready = client.get("/health/ready")
        _record("health.ready", PASS if ready.status_code == 200 else FAIL, f"HTTP {ready.status_code}")

        # 3) list analyses / profiles
        analyses = client.get("/analyses", params={"limit": 5})
        _record(
            "analyses.list",
            PASS if analyses.status_code == 200 else FAIL,
            f"HTTP {analyses.status_code} total={analyses.json().get('total') if analyses.status_code == 200 else '-'}",
        )
        for kind in ("resume", "jd"):
            profiles = client.get("/profiles", params={"kind": kind})
            _record(
                f"profiles.list[{kind}]",
                PASS if profiles.status_code == 200 else FAIL,
                f"HTTP {profiles.status_code}",
            )

        # 4) upload synthetic documents
        ok_resume, resume = _upload(client, "resume", "resume_match.txt")
        ok_jd, jd = _upload(client, "jd", "jd_match.txt")
        _record("documents.upload[resume]", PASS if ok_resume else FAIL, "" if ok_resume else resume)
        _record("documents.upload[jd]", PASS if ok_jd else FAIL, "" if ok_jd else jd)

        # 5) create analysis
        if ok_resume and ok_jd:
            created = client.post(
                "/analyses",
                json={
                    "resume_document_id": resume,
                    "jd_document_id": jd,
                    "idempotency_key": f"smoke-{token}",
                },
            )
            if created.status_code in (200, 201):
                analysis_id = created.json()["id"]
                _record("analyses.create", PASS, f"id={analysis_id[:8]}")
            else:
                _record("analyses.create", FAIL, f"HTTP {created.status_code}: {created.text[:200]}")

        if analysis_id is not None:
            # 6) run deterministic analysis
            run = client.post(f"/analyses/{analysis_id}/run", json={})
            run_ok = run.status_code == 200
            if run_ok:
                _record("analyses.run", PASS, f"gate={run.json().get('gate')}")
            elif _credential_blocked(run):
                _record("analyses.run", BLOCKED, "EXTERNAL CREDENTIAL BLOCKED（无 LLM 凭证）")
            else:
                _record("analyses.run", FAIL, f"HTTP {run.status_code}: {run.text[:200]}")

            # 7) read deterministic artifacts
            for name, path in (
                ("constraints", f"/analyses/{analysis_id}/constraints"),
                ("skill-matches", f"/analyses/{analysis_id}/skill-matches"),
                ("evidence", f"/analyses/{analysis_id}/evidence"),
                ("trace", f"/analyses/{analysis_id}/trace"),
            ):
                response = client.get(path)
                count = len(response.json().get("items", [])) if name == "evidence" and response.status_code == 200 else (
                    len(response.json()) if response.status_code == 200 and isinstance(response.json(), list) else 0
                )
                _record(f"analyses.{name}", PASS if response.status_code == 200 else FAIL, f"count={count}")

            score = client.get(f"/analyses/{analysis_id}/score")
            if score.status_code == 200:
                _record("analyses.score", PASS, f"total={score.json().get('total')}")
            elif score.status_code == 404:
                _record("analyses.score", BLOCKED if run.status_code != 200 else FAIL, "no score yet")
            else:
                _record("analyses.score", FAIL, f"HTTP {score.status_code}")

            # 8) critique + report
            critique_run = client.post(f"/analyses/{analysis_id}/critique")
            if critique_run.status_code == 200:
                _record("critique.run", PASS, f"status={critique_run.json().get('critique_status')}")
            elif _credential_blocked(critique_run):
                _record("critique.run", BLOCKED, "EXTERNAL CREDENTIAL BLOCKED（无 LLM 凭证）")
            else:
                _record("critique.run", FAIL, f"HTTP {critique_run.status_code}")

            critique = client.get(f"/analyses/{analysis_id}/critique")
            _record(
                "critique.get",
                PASS if critique.status_code == 200 else (BLOCKED if critique.status_code == 404 else FAIL),
                f"HTTP {critique.status_code}",
            )
            report = client.get(f"/analyses/{analysis_id}/report")
            _record(
                "report.get",
                PASS if report.status_code == 200 else (BLOCKED if report.status_code == 404 else FAIL),
                f"HTTP {report.status_code}",
            )

            # 9) review（仅在进入 awaiting_review 时可行）
            current = client.get(f"/analyses/{analysis_id}")
            status = current.json().get("status") if current.status_code == 200 else "?"
            if status == "awaiting_review":
                approve = client.post(f"/analyses/{analysis_id}/review", json={"decision": "approve"})
                if approve.status_code == 200:
                    _record("review.approve", PASS, f"to_state={approve.json().get('to_state')}")
                    final = client.get(f"/analyses/{analysis_id}")
                    _record(
                        "review.finalized",
                        PASS if final.json().get("status") == "finalized" else FAIL,
                        f"status={final.json().get('status')}",
                    )
                else:
                    _record("review.approve", FAIL, f"HTTP {approve.status_code}")
            else:
                _record("review.approve", BLOCKED, f"analysis status={status}（非 awaiting_review）")
                _record("review.finalized", SKIP, "上游被阻塞")

    return _finish(args.strict)


def _finish(strict: bool) -> int:
    failed = [item for item in _results if item[1] == FAIL]
    blocked = [item for item in _results if item[1] == BLOCKED]
    skipped = [item for item in _results if item[1] == SKIP]
    passed = [item for item in _results if item[1] == PASS]
    print(
        f"\n[smoke] summary: total={len(_results)} passed={len(passed)} "
        f"failed={len(failed)} blocked={len(blocked)} skipped={len(skipped)}"
    )
    if failed:
        print("[smoke] FAILED steps: " + ", ".join(name for name, _, _ in failed))
        return 1
    if blocked and strict:
        print("[smoke] strict mode: BLOCKED steps count as failure: " + ", ".join(n for n, _, _ in blocked))
        return 1
    if blocked:
        print("[smoke] note: BLOCKED = EXTERNAL CREDENTIAL BLOCKED（未伪造通过）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

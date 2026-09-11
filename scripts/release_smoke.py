"""Release smoke（Phase 6 §60）—— env / DB / API / frontend / golden 五检查。

用法：
    python scripts/release_smoke.py [--api http://127.0.0.1:8000] [--frontend http://127.0.0.1:3000]

退出码：0 = 全部通过；非 0 = 有失败（**不**允许失败但 exit 0）。
只用 stdlib，可用任意 Python 3.11+ 运行。
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parents[1]
GOLDEN_CASES = REPO_ROOT / "backend" / "tests" / "golden" / "cases" / "golden_cases.json"
GOLDEN_EXPECTED = REPO_ROOT / "backend" / "tests" / "golden" / "expected" / "golden_expected.json"
ENV_EXAMPLE = REPO_ROOT / ".env.example"

REQUIRED_ENV_KEYS = (
    "DATABASE_URL",
    "TEST_DATABASE_URL",
    "STORAGE_DIR",
    "CONFIG_DIR",
    "CORS_ALLOW_ORIGINS",
    "EMBEDDING_MODEL",
)

results: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    status = "PASS" if ok else "FAIL"
    results.append((name, status, detail))
    print(f"[release-smoke] {status:4} {name}" + (f" — {detail}" if detail else ""))
    return ok


def info(name: str, detail: str) -> None:
    results.append((name, "INFO", detail))
    print(f"[release-smoke] INFO {name} — {detail}")


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def http_get(url: str, timeout: float = 10.0) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310 - 固定本机 URL
            return response.status, response.read(4096).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, ""
    except Exception as exc:  # noqa: BLE001
        return 0, f"{type(exc).__name__}: {exc}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="release_smoke")
    parser.add_argument("--api", default=os.getenv("JOBFIT_SMOKE_API", "http://127.0.0.1:8000"))
    parser.add_argument("--frontend", default=os.getenv("JOBFIT_SMOKE_FRONTEND", "http://127.0.0.1:3000"))
    parser.add_argument("--db-host", default=None, help="默认从 DATABASE_URL 解析")
    args = parser.parse_args(argv)

    # ---------------------------------------------------------- 1) env contract
    example = read_env_file(ENV_EXAMPLE)
    missing = [key for key in REQUIRED_ENV_KEYS if key not in example]
    check(".env.example 契约完整", not missing, f"missing={missing}" if missing else f"{len(REQUIRED_ENV_KEYS)} keys")

    env_file = read_env_file(REPO_ROOT / "backend" / ".env")
    effective = {**env_file, **{k: v for k, v in os.environ.items() if k in REQUIRED_ENV_KEYS or k == "DEEPSEEK_API_KEY"}}
    database_url = effective.get("DATABASE_URL", "")
    check("DATABASE_URL 已配置", bool(database_url), database_url.split("@")[-1] if database_url else "missing")

    api_key = effective.get("DEEPSEEK_API_KEY", "")
    if api_key and api_key.lower() not in {"", "sk-xxxx"}:
        info("DEEPSEEK_API_KEY", "已配置（长度已隐去；本次 smoke 不调用 live API）")
    else:
        info("DEEPSEEK_API_KEY", "未配置 => EXTERNAL CREDENTIAL BLOCKED（不影响确定性链路）")

    # ---------------------------------------------------------- 2) database
    parsed = urlparse(database_url.replace("postgresql+psycopg://", "postgresql://")) if database_url else None
    host = args.db_host or (parsed.hostname if parsed else "localhost")
    port = (parsed.port if parsed and parsed.port else 5432)
    try:
        with socket.create_connection((host, port), timeout=5):
            check("database.tcp", True, f"{host}:{port}")
    except Exception as exc:  # noqa: BLE001
        check("database.tcp", False, f"{host}:{port} {type(exc).__name__}")

    # ---------------------------------------------------------- 3) API
    status, body = http_get(f"{args.api.rstrip('/')}/health")
    health_ok = status == 200
    if health_ok:
        try:
            payload = json.loads(body)
            health_ok = payload.get("status") == "ok" and bool(payload.get("version"))
            info("api.health", f"version={payload.get('version')} pipeline={payload.get('pipeline_version')}")
        except json.JSONDecodeError:
            health_ok = False
    check("api.health", health_ok, f"HTTP {status}")

    status, body = http_get(f"{args.api.rstrip('/')}/health/ready")
    ready_ok = status == 200
    check("api.ready", ready_ok, f"HTTP {status}" + ("" if ready_ok else f" {body[:80]}"))

    status, _ = http_get(f"{args.api.rstrip('/')}/analyses?limit=1")
    check("api.analyses.list", status == 200, f"HTTP {status}")

    status, _ = http_get(f"{args.api.rstrip('/')}/openapi.json")
    check("api.openapi", status == 200, f"HTTP {status}")

    # ---------------------------------------------------------- 4) frontend
    status, body = http_get(args.frontend.rstrip("/"), timeout=15.0)
    frontend_ok = status == 200 and "JobFit" in body
    check("frontend.dashboard", frontend_ok, f"HTTP {status}")

    # ---------------------------------------------------------- 5) golden dataset
    cases_ok = GOLDEN_CASES.is_file() and GOLDEN_EXPECTED.is_file()
    count = 0
    if cases_ok:
        cases = json.loads(GOLDEN_CASES.read_text(encoding="utf-8"))
        expected = json.loads(GOLDEN_EXPECTED.read_text(encoding="utf-8"))
        count = len(cases)
        names_match = {c["name"] for c in cases} == set(expected)
        cases_ok = count == 11 and names_match
    check("golden.cases", cases_ok, f"count={count} (expected 11)")

    # ---------------------------------------------------------- summary
    failed = [name for name, status, _ in results if status == "FAIL"]
    print(
        f"\n[release-smoke] summary: total={len(results)} "
        f"pass={sum(1 for _, s, _ in results if s == 'PASS')} "
        f"fail={len(failed)} info={sum(1 for _, s, _ in results if s == 'INFO')}"
    )
    if failed:
        print("[release-smoke] FAILED: " + ", ".join(failed))
        return 1
    print("[release-smoke] OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())

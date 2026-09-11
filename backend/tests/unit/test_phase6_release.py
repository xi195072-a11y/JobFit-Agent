# mypy: disable-error-code="arg-type,attr-defined,assignment,union-attr"
"""unit: Phase 6 release gate 静态契约（§6/§10/§38/§41/§47/§53/§61）。

全部为可证伪的静态/离线检查（不依赖 DB、不调用网络、不调用 live LLM）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from fastapi.testclient import TestClient

from jobfit.core.version import app_version
from jobfit.main import create_app

REPO_ROOT = Path(__file__).resolve().parents[3]
ENV_EXAMPLE = REPO_ROOT / ".env.example"
GITIGNORE = REPO_ROOT / ".gitignore"
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
LICENSE = REPO_ROOT / "LICENSE"
SMOKE_API = REPO_ROOT / "scripts" / "smoke_api.py"
RELEASE_SMOKE = REPO_ROOT / "scripts" / "release_smoke.py"
PYPROJECT = REPO_ROOT / "backend" / "pyproject.toml"

REQUIRED_ENV_KEYS = (
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_DB",
    "DATABASE_URL",
    "TEST_DATABASE_URL",
    "CORS_ALLOW_ORIGINS",
    "DEEPSEEK_API_KEY",
    "EMBEDDING_MODEL",
)


# ---------------------------------------------------------------- version（§38）


def test_app_version_matches_pyproject() -> None:
    """package/pyproject/README/health 的版本不得互相矛盾（§38）。"""
    match = re.search(r'^version\s*=\s*"([^"]+)"', PYPROJECT.read_text(encoding="utf-8"), re.MULTILINE)
    assert match is not None, "pyproject.toml 缺少 version"
    assert app_version() == match.group(1)


def test_health_exposes_version_without_environment_or_secrets() -> None:
    """§17：/health 只暴露 status/version；不泄露 environment / DB / 凭证。"""
    client = TestClient(create_app())
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["version"] == app_version()
    assert set(body) <= {"status", "version", "pipeline_version"}
    assert "app_env" not in body
    raw = json.dumps(body).lower()
    for leaked in ("password", "secret", "api_key", "postgresql", "token"):
        assert leaked not in raw


# ---------------------------------------------------------------- env contract（§6）


def test_env_example_documents_full_contract() -> None:
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    missing = [key for key in REQUIRED_ENV_KEYS if f"{key}=" not in text]
    assert missing == [], f".env.example 缺少: {missing}"
    # 禁止真实密钥/密钥样式占位符进入模板（默认留空 => EXTERNAL CREDENTIAL BLOCKED）
    assert not re.search(r"sk-", text)
    assert "DEEPSEEK_API_KEY=" in text


# ---------------------------------------------------------------- repo hygiene（§48/§49）


def test_gitignore_excludes_secrets_and_generated_artifacts() -> None:
    text = GITIGNORE.read_text(encoding="utf-8")
    for pattern in (".env", "node_modules/", "__pycache__/", ".venv/", ".next/", "phase*-junit*.xml", "*.log"):
        assert pattern in text, f".gitignore 缺少 {pattern}"
    # 发布证据必须保留（不被 ignore 掉）
    assert "!phase6-evaluation.json" in text


def test_license_present() -> None:
    text = LICENSE.read_text(encoding="utf-8")
    assert text.startswith("MIT License")


def test_release_scripts_present_and_non_trivial() -> None:
    for path in (SMOKE_API, RELEASE_SMOKE):
        assert path.is_file(), f"missing {path}"
        assert len(path.read_text(encoding="utf-8").splitlines()) > 40
    # 失败必须导致非 0 退出（不伪装 PASS）
    release_text = RELEASE_SMOKE.read_text(encoding="utf-8")
    assert "return 1" in release_text and "sys.exit(main())" in release_text
    smoke_text = SMOKE_API.read_text(encoding="utf-8")
    assert "EXTERNAL CREDENTIAL BLOCKED" in smoke_text


# ---------------------------------------------------------------- CI（§10–§13/§53/§61）


def test_ci_workflow_is_present_and_deterministic() -> None:
    text = CI_WORKFLOW.read_text(encoding="utf-8")
    # 真实 pgvector service（禁止全 mock DB）
    assert "pgvector/pgvector:pg16" in text
    # migration + 真实 DB
    assert "alembic upgrade head" in text and "alembic check" in text
    # 四类检查齐备
    for needle in ("ruff check", "mypy", "pytest tests --junitxml", "npm run typecheck", "npm run lint", "playwright"):
        assert needle in text, f"CI 缺少: {needle}"
    # 失败必须 fail workflow（检查真实 YAML key，而不是注释文字）
    assert "continue-on-error:" not in text
    # 不依赖 live credential（不得把 key 作为 env 传入，也不得读取 secrets）
    assert "DEEPSEEK_API_KEY:" not in text
    assert "secrets." not in text
    # artifacts
    assert "actions/upload-artifact" in text
    assert "phase6-junit.xml" in text


def test_ci_uses_supported_versions() -> None:
    text = CI_WORKFLOW.read_text(encoding="utf-8")
    assert 'PYTHON_VERSION: "3.12"' in text
    assert 'NODE_VERSION: "24"' in text


# ---------------------------------------------------------------- frontend packaging（§30/§35）


def test_no_dangerous_frontend_patterns() -> None:
    """§35：前端不得出现 dangerouslySetInnerHTML / eval / new Function。"""
    offenders: list[str] = []
    for path in (REPO_ROOT / "frontend").rglob("*"):
        if path.suffix not in {".ts", ".tsx"} or "node_modules" in path.parts or ".next" in path.parts:
            continue
        source = path.read_text(encoding="utf-8")
        for token in ("dangerouslySetInnerHTML", "eval(", "new Function("):
            if token in source:
                offenders.append(f"{path.name}: {token}")
    assert offenders == []

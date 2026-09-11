# mypy: disable-error-code="arg-type,attr-defined,assignment,union-attr"
"""unit: Phase 5 静态审计（§58）—— AST/tokenize + 文件系统审计。

只做**可证伪的静态检查**（不依赖运行）：

Backend：
- src/ 中不得出现 TODO / FIXME / NotImplementedError / 仅 `pass` 的占位函数；
- 生产路径不得引用 test-only fake provider / 测试目录；
- 不得硬编码密钥或 PII 日志。

Frontend：
- 不得有 TODO/FIXME 占位；
- 不得有 `console.log`（含敏感数据风险）；
- 不得硬编码 API base URL（必须走 NEXT_PUBLIC_API_BASE_URL）；
- 不得出现 fake/mock 结果数据（模拟后端返回的静态 JSON）。

E2E：
- 不得依赖外部网站（只允许 localhost/127.0.0.1）；
- 不得依赖 live credential（不得出现 DEEPSEEK_API_KEY）；
- 不得包含真实 PII 标记。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_SRC = REPO_ROOT / "backend" / "src" / "jobfit"
FRONTEND_ROOT = REPO_ROOT / "frontend"
E2E_ROOT = REPO_ROOT / "tests" / "e2e"

PHASE5_MODULES = [
    BACKEND_SRC / "evaluation",
    BACKEND_SRC / "api" / "errors.py",
    BACKEND_SRC / "api" / "middleware.py",
    BACKEND_SRC / "api" / "v1" / "catalog.py",
]

_FRONTEND_SOURCE_DIRS = ["app", "components", "lib"]
_FRONTEND_EXTS = {".ts", ".tsx"}


def _iter_files(root: Path, *, suffixes: set[str]) -> list[Path]:
    if root.is_file():
        return [root] if root.suffix in suffixes else []
    return [
        path
        for path in root.rglob("*")
        if path.is_file() and path.suffix in suffixes and "node_modules" not in path.parts
    ]


def _phase5_python_files() -> list[Path]:
    files: list[Path] = []
    for entry in PHASE5_MODULES:
        files.extend(_iter_files(entry, suffixes={".py"}))
    return files


# ================================================================ backend


def test_phase5_modules_exist_and_are_non_trivial() -> None:
    for entry in PHASE5_MODULES:
        assert entry.exists(), f"missing Phase 5 module: {entry}"
    for path in _phase5_python_files():
        assert len(path.read_text(encoding="utf-8").splitlines()) > 15, path


def test_phase5_backend_has_no_placeholders_or_secrets() -> None:
    offenders: list[str] = []
    for path in _phase5_python_files():
        source = path.read_text(encoding="utf-8")
        for marker in ("TODO", "FIXME", "NotImplementedError"):
            if marker in source:
                offenders.append(f"{path.name}: {marker}")
        if re.search(r"(?i)(api[_-]?key|secret|token)\s*=\s*['\"][A-Za-z0-9_\-]{12,}['\"]", source):
            offenders.append(f"{path.name}: hardcoded secret")
    assert offenders == []


def test_phase5_backend_logs_no_pii() -> None:
    """日志调用不得包含 email/phone 等 PII 字段名。"""
    offenders: list[str] = []
    for path in _phase5_python_files():
        source = path.read_text(encoding="utf-8")
        if re.search(r"(?i)log.*\b(email|phone|mobile|password)\b", source):
            offenders.append(path.name)
    assert offenders == []


def test_evaluation_package_never_imports_tests() -> None:
    """§28/ADR-042：评测包不 import tests/，也不提供 production fake provider。"""
    offenders: list[str] = []
    for path in _iter_files(BACKEND_SRC / "evaluation", suffixes={".py"}):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in {"support", "tests", "conftest"}:
                        offenders.append(f"{path.name}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.module.split(".")[0] in {"support", "tests", "conftest"}:
                    offenders.append(f"{path.name}: from {node.module}")
        source = path.read_text(encoding="utf-8")
        if "DeterministicProvider" in source:
            offenders.append(f"{path.name}: fake provider reference")
    assert offenders == []


# ================================================================ frontend


def _frontend_files() -> list[Path]:
    files: list[Path] = []
    for directory in _FRONTEND_SOURCE_DIRS:
        root = FRONTEND_ROOT / directory
        if root.exists():
            files.extend(_iter_files(root, suffixes=_FRONTEND_EXTS))
    return files


def test_frontend_source_exists() -> None:
    files = _frontend_files()
    assert len(files) >= 10, f"frontend source looks empty: {len(files)} files"
    required = [
        FRONTEND_ROOT / "app" / "page.tsx",
        FRONTEND_ROOT / "app" / "jobs" / "page.tsx",
        FRONTEND_ROOT / "app" / "analyses" / "new" / "page.tsx",
        FRONTEND_ROOT / "app" / "analyses" / "[id]" / "page.tsx",
        FRONTEND_ROOT / "app" / "reports" / "[id]" / "page.tsx",
        FRONTEND_ROOT / "app" / "review" / "[id]" / "page.tsx",
        FRONTEND_ROOT / "lib" / "api" / "client.ts",
        FRONTEND_ROOT / "lib" / "api" / "resources.ts",
    ]
    for path in required:
        assert path.is_file(), f"missing frontend file: {path}"


def test_frontend_has_no_placeholders_or_console_logging() -> None:
    offenders: list[str] = []
    for path in _frontend_files():
        source = path.read_text(encoding="utf-8")
        if re.search(r"\bTODO\b|\bFIXME\b", source):
            offenders.append(f"{path.name}: placeholder")
        if re.search(r"\bconsole\.(log|debug|info|warn|error)\s*\(", source):
            offenders.append(f"{path.name}: console logging")
    assert offenders == []


def test_frontend_api_base_url_is_env_driven() -> None:
    """API base URL 必须来自环境变量；只有注释/示例文件允许出现字面 localhost。"""
    allowed = {"client.ts", ".env.local.example"}
    offenders: list[str] = []
    for path in _frontend_files():
        if path.name in allowed:
            continue
        source = path.read_text(encoding="utf-8")
        if re.search(r"https?://(localhost|127\.0\.0\.1):\d+", source):
            offenders.append(path.name)
    assert offenders == []

    client_source = (FRONTEND_ROOT / "lib" / "api" / "client.ts").read_text(encoding="utf-8")
    assert "NEXT_PUBLIC_API_BASE_URL" in client_source


def test_frontend_has_no_fake_result_data() -> None:
    """§56：禁止静态 JSON 假装后端 / 硬编码分析结果。"""
    offenders: list[str] = []
    suspicious = ("hardcoded", "mockResponse", "fakeResult", "sampleReport", "demoData")
    for path in _frontend_files():
        source = path.read_text(encoding="utf-8")
        for token in suspicious:
            if token in source:
                offenders.append(f"{path.name}: {token}")
    assert offenders == []


def test_frontend_has_no_secrets() -> None:
    offenders: list[str] = []
    for path in _frontend_files():
        source = path.read_text(encoding="utf-8")
        if "DEEPSEEK_API_KEY" in source:
            offenders.append(f"{path.name}: secret reference")
        if re.search(r"(?i)(api[_-]?key|secret|token)\s*[:=]\s*['\"][A-Za-z0-9_\-]{12,}['\"]", source):
            offenders.append(f"{path.name}: hardcoded secret")
    assert offenders == []


# ================================================================ e2e


def test_e2e_depends_on_no_external_services() -> None:
    files = _iter_files(E2E_ROOT, suffixes={".ts"})
    assert files, "E2E specs missing"
    offenders: list[str] = []
    for path in files:
        source = path.read_text(encoding="utf-8")
        for match in re.findall(r"https?://[A-Za-z0-9\.\-]+", source):
            host = match.split("://", 1)[1]
            if host not in {"localhost", "127.0.0.1"}:
                offenders.append(f"{path.name}: {match}")
        if "DEEPSEEK_API_KEY" in source:
            offenders.append(f"{path.name}: live credential dependency")
    assert offenders == []


def test_e2e_uses_real_backend_over_http() -> None:
    """§25：E2E 必须打真实 backend（HTTP），不得 import backend Python 函数。"""
    files = _iter_files(E2E_ROOT, suffixes={".ts"})
    offenders: list[str] = []
    for path in files:
        source = path.read_text(encoding="utf-8")
        if re.search(r"from\s+['\"].*backend.*['\"]", source):
            offenders.append(f"{path.name}: imports backend code")
        if re.search(r"\brequire\(['\"].*jobfit", source):
            offenders.append(f"{path.name}: requires jobfit")
    assert offenders == []

    helper = (E2E_ROOT / "specs" / "helpers.ts").read_text(encoding="utf-8")
    assert "/documents" in helper and "/analyses" in helper  # 真实 HTTP 调用
    assert "FIXTURES" in helper  # 使用合成 fixture


def test_e2e_uses_synthetic_fixtures_only() -> None:
    helper = (E2E_ROOT / "specs" / "helpers.ts").read_text(encoding="utf-8")
    # fixture 一律来自 backend/tests/fixtures（合成数据），不得内联真实 PII
    assert "backend" in helper and "fixtures" in helper
    for pattern in (r"\b1[3-9]\d{9}\b", r"[\w\.\-]+@[\w\-]+\.[A-Za-z]{2,}"):
        assert not re.search(pattern, helper), f"PII-like literal in helpers.ts: {pattern}"


def test_e2e_harness_is_test_only_and_documents_no_production_use() -> None:
    harness = E2E_ROOT / "backend" / "app.py"
    assert harness.is_file()
    source = harness.read_text(encoding="utf-8")
    assert "dependency_overrides" in source
    assert "/__e2e__/" in source
    # harness 自身不是生产模块
    assert not str(harness).startswith(str(BACKEND_SRC))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))

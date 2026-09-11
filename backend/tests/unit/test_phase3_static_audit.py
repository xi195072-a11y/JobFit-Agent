"""unit: 静态架构审计（Phase 3 §43/§46）——基于 AST/tokenize 的**语义**检查。

刻意不使用简单 grep：注释与 docstring 中的 `now()`、`pass`、`TODO` 不应产生误报。
"""

from __future__ import annotations

import ast
import tokenize
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "jobfit"
PHASE3_PACKAGES = ("matching", "evidence", "workflow", "db")


def _modules(*relative: str) -> list[Path]:
    out: list[Path] = []
    for item in relative:
        target = SRC / item
        if target.is_dir():
            out.extend(sorted(target.rglob("*.py")))
        else:
            out.append(target)
    return [path for path in out if path.is_file()]


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


# ---------------------------------------------------------------- 1. 无占位实现


def test_no_todo_markers_in_comments() -> None:
    offenders: list[str] = []
    for path in _modules(*PHASE3_PACKAGES):
        with path.open(encoding="utf-8") as handle:
            for token in tokenize.generate_tokens(handle.readline):
                if token.type != tokenize.COMMENT:
                    continue
                text = token.string.upper()
                if any(marker in text for marker in ("TODO", "FIXME", "XXX", "HACK")):
                    offenders.append(f"{path.name}:{token.start[0]}: {token.string.strip()}")
    assert offenders == []


def test_no_not_implemented_errors() -> None:
    offenders: list[str] = []
    for path in _modules("matching", "evidence", "workflow", "db", "api"):
        for node in ast.walk(_parse(path)):
            if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
                func = node.exc.func
                name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
                if name == "NotImplementedError":
                    offenders.append(f"{path.name}:{node.lineno}")
    assert offenders == []


def _is_protocol(class_node: ast.ClassDef) -> bool:
    for base in class_node.bases:
        name = base.id if isinstance(base, ast.Name) else getattr(base, "attr", "")
        if name.endswith("Protocol"):
            return True
    return False


def _exempt_function_lines(tree: ast.Module) -> set[int]:
    """Protocol 接口方法 / abstractmethod / overload 允许空实现（它们是接口声明，不是占位实现）。"""
    exempt: set[int] = set()
    for class_node in ast.walk(tree):
        if isinstance(class_node, ast.ClassDef) and _is_protocol(class_node):
            for child in class_node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    exempt.add(child.lineno)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            name = decorator.id if isinstance(decorator, ast.Name) else getattr(decorator, "attr", "")
            if name in {"abstractmethod", "overload"}:
                exempt.add(node.lineno)
    return exempt


def _has_empty_body(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    body = [
        stmt
        for stmt in node.body
        if not (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant))
    ]
    return len(body) == 0


def test_no_pass_only_placeholder_functions() -> None:
    """除接口声明外，函数体不得为空（`pass` / `...`）—— 禁止占位实现。"""
    offenders: list[str] = []
    for path in _modules(*PHASE3_PACKAGES):
        tree = _parse(path)
        exempt = _exempt_function_lines(tree)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.lineno in exempt:
                continue
            if _has_empty_body(node):
                offenders.append(f"{path.name}:{node.lineno}:{node.name}")
    assert offenders == []


# ---------------------------------------------------------------- 2. 时间语义


def _forbidden_clock_calls(tree: ast.Module) -> list[str]:
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in {"now", "utcnow", "today", "time"}:
                found.append(f"line {node.lineno}: {node.func.attr}")
    return found


def test_no_wall_clock_calls_in_deterministic_stages() -> None:
    """lease 判定必须走 clock_timestamp()；确定性阶段不得读进程时钟。"""
    offenders: dict[str, list[str]] = {}
    for path in _modules(*PHASE3_PACKAGES):
        hits = _forbidden_clock_calls(_parse(path))
        if hits:
            offenders[path.name] = hits
    assert offenders == {}


def test_lease_sql_uses_clock_timestamp_only() -> None:
    analyses_repo = (SRC / "db" / "repositories" / "analyses.py").read_text(encoding="utf-8")
    results_repo = (SRC / "db" / "repositories" / "results.py").read_text(encoding="utf-8")
    combined = analyses_repo + results_repo
    assert "clock_timestamp()" in combined
    assert "lease_expires_at" in combined
    # 禁止数据库本地时间函数参与 lease 判定
    assert "CURRENT_TIMESTAMP" not in combined.upper()
    assert "now()" not in combined.replace("clock_timestamp()", "")
    # 四条件 fencing 必须完整出现在 SQL 中
    for condition in ("claim_token", "status = 'running'", "lease_expires_at > clock_timestamp()"):
        assert condition in combined


# ---------------------------------------------------------------- 3. 无 LLM 参与判定


def test_hard_constraint_and_matching_never_import_llm() -> None:
    """§9/§43-10：硬条件、匹配、计分逻辑里不得出现任何 LLM 依赖。"""
    offenders: list[str] = []
    for path in _modules("matching", "evidence"):
        for node in ast.walk(_parse(path)):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("jobfit.llm") or alias.name in {"openai", "anthropic"}:
                        offenders.append(f"{path.name}:{alias.name}")
            elif isinstance(node, ast.ImportFrom) and (node.module or "").startswith("jobfit.llm"):
                offenders.append(f"{path.name}:{node.module}")
    assert offenders == []


def test_no_test_provider_reference_in_production_code() -> None:
    """§46：生产路径不得引用 test-only fake provider。"""
    offenders: list[str] = []
    for path in _modules("matching", "evidence", "workflow", "api", "llm", "db", "parsing", "extraction"):
        source = path.read_text(encoding="utf-8")
        if "DeterministicProvider" in source:
            offenders.append(path.name)
    assert offenders == []


def test_retrieval_uses_real_pgvector_distance() -> None:
    """§43-11：检索必须走真实 pgvector 距离算子，而不是伪造排序。"""
    tree = _parse(SRC / "evidence" / "retrieval.py")
    attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert "cosine_distance" in attributes
    assert "order_by" in attributes


def test_embedding_provider_has_no_hardcoded_constant_vector() -> None:
    """§46：embedding 实现必须是真实函数（同一文本同向量，不同文本不同向量）。"""
    from jobfit.evidence.embeddings import HashingEmbeddingProvider

    provider = HashingEmbeddingProvider()
    first, second = provider.embed(["Python, FastAPI"]), provider.embed(["Java, Spring Boot"])
    assert first[0] != second[0]
    assert first[0] == provider.embed(["Python, FastAPI"])[0]


@pytest.mark.parametrize(
    "relative",
    [
        "matching/constraints.py",
        "matching/skills.py",
        "matching/score.py",
        "evidence/retrieval.py",
        "workflow/nodes.py",
    ],
)
def test_phase3_modules_exist_and_are_non_trivial(relative: str) -> None:
    path = SRC / relative
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert len(text.splitlines()) > 30
    assert "def " in text


def test_tests_directory_never_imported_by_production() -> None:
    offenders: list[str] = []
    for path in _modules(*PHASE3_PACKAGES, "api", "llm"):
        tree = _parse(path)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(("tests", "support")):
                offenders.append(path.name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith(("tests", "support")):
                        offenders.append(path.name)
    assert offenders == []

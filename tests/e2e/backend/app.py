"""E2E-only backend harness（Phase 5 §24/§25）。

以**真实 HTTP** 暴露真实 FastAPI app，并注入 test-only `DeterministicProvider`
（ADR-029）——E2E 因此不依赖 live DeepSeek，也不 mock 后端。

本文件属于测试设施，**绝不被生产代码 import**：

- 生产 `create_app()` 不含任何 fake provider；这里通过 FastAPI `dependency_overrides`
  注入 `DeterministicProvider`（与 Phase 1–4 集成测试同一策略）；
- 额外注册 `/__e2e__/*` 控制端点（仅存在于本 harness），用于在小规模内切换场景与重置库。

启动：
    python -m uvicorn app:app --app-dir tests/e2e/backend --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

import os
import pathlib
import sys
import tempfile
import uuid

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]  # tests/e2e/backend/app.py
_BACKEND_ROOT = _REPO_ROOT / "backend"
sys.path.insert(0, str(_BACKEND_ROOT / "src"))
sys.path.insert(0, str(_BACKEND_ROOT / "tests"))

# ---- 环境：必须在 import jobfit.* 之前设定（Settings 会被缓存）----
os.environ.setdefault(
    "DATABASE_URL", "postgresql+psycopg://jobfit:jobfit@localhost:5432/jobfit_test"
)
os.environ.setdefault("TEST_DATABASE_URL", os.environ["DATABASE_URL"])
os.environ.setdefault("STORAGE_DIR", str(pathlib.Path(tempfile.gettempdir()) / "jobfit-e2e-storage"))
os.environ.setdefault("CONFIG_DIR", str(_BACKEND_ROOT / "config"))
os.environ.setdefault("PIPELINE_VERSION", "e2e-pipeline-1")
os.environ.setdefault("LLM_PROVIDER", "deepseek")
os.environ.setdefault("DEEPSEEK_MODEL", "e2e-deterministic")
os.environ.setdefault("MAX_LLM_ATTEMPTS", "10")
os.environ.setdefault("LEASE_TTL_SECONDS", "120")
os.environ.pop("DEEPSEEK_API_KEY", None)

from fastapi import Body  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from jobfit.config.settings import get_settings  # noqa: E402
from jobfit.db.base import Base  # noqa: E402

get_settings.cache_clear()
settings = get_settings()

import jobfit.db.models  # noqa: F401,E402  注册全部表
from jobfit.api.deps import get_optional_llm_provider, get_session, get_session_factory  # noqa: E402
from jobfit.api.deps import get_settings_dep  # noqa: E402
from jobfit.api.v1.extractions import get_provider  # noqa: E402  (== deps.get_llm_provider)
from jobfit.main import create_app  # noqa: E402
from support import (  # noqa: E402
    JD_MATCH_PAYLOAD,
    JD_MISMATCH_PAYLOAD,
    JD_UNKNOWN_PAYLOAD,
    RESUME_MATCH_PAYLOAD,
    RESUME_MISMATCH_PAYLOAD,
    DeterministicProvider,
    build_valid_critique_payload,
    extract_prompt_data,
)

_engine = create_engine(settings.database_url, pool_pre_ping=True)
# begin() 才会提交：connect() 块退出即回滚，CREATE EXTENSION 会变成静默空操作。
with _engine.begin() as _conn:
    _conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
Base.metadata.create_all(_engine)
session_factory = sessionmaker(bind=_engine, expire_on_commit=False)

_EXTRACTION_SCENARIOS: dict[str, tuple[dict, dict]] = {
    "match": (RESUME_MATCH_PAYLOAD, JD_MATCH_PAYLOAD),
    "unknown": (RESUME_MISMATCH_PAYLOAD, JD_UNKNOWN_PAYLOAD),
    "mismatch": (RESUME_MISMATCH_PAYLOAD, JD_MISMATCH_PAYLOAD),
}

provider = DeterministicProvider(
    resume_payload=dict(RESUME_MATCH_PAYLOAD),
    jd_payload=dict(JD_MATCH_PAYLOAD),
    critique_payload=build_valid_critique_payload,
)


def _foreign_chunk_id(prompt: str) -> str:
    """取一个**不属于当前 analysis 证据池**的真实 chunk id（跨 analysis 引用样本）。

    若库中还没有其它 chunk（测试尚未建第二个 analysis），退化为随机 uuid
    （此时断言仍是"必须 rejected"，只是失败原因可能是 fabricated）。
    """
    data = extract_prompt_data(prompt)
    current = {
        str(entry.get("source_chunk_id"))
        for entry in data.get("evidence_pool", {}).values()
        if entry.get("source_chunk_id")
    }
    with session_factory() as session:
        row = session.execute(
            text(
                "SELECT id FROM document_chunks WHERE NOT (id::text = ANY(:current)) LIMIT 1"
            ),
            {"current": list(current) or [""]},
        ).first()
    return str(row[0]) if row is not None else str(uuid.uuid4())


def _critique_payload_for(mode: str):
    if mode == "valid":
        return build_valid_critique_payload
    if mode == "fabricated":
        return lambda prompt: build_valid_critique_payload(
            prompt, fabricated_chunk_id=str(uuid.uuid4())
        )
    if mode == "cross_analysis":
        return lambda prompt: build_valid_critique_payload(
            prompt, fabricated_chunk_id=_foreign_chunk_id(prompt)
        )
    if mode == "unknown_override":
        return lambda prompt: build_valid_critique_payload(prompt, unknown_claim=True)
    raise ValueError(f"unknown critique mode: {mode}")


app = create_app()


def _session():
    session = session_factory()
    try:
        yield session
    finally:
        session.close()


app.dependency_overrides[get_session] = _session
app.dependency_overrides[get_session_factory] = lambda: session_factory
app.dependency_overrides[get_provider] = lambda: provider
app.dependency_overrides[get_optional_llm_provider] = lambda: provider
app.dependency_overrides[get_settings_dep] = lambda: settings


@app.post("/__e2e__/scenario")
def set_scenario(
    extraction: str = Body(default="match", embed=True),
    critique: str = Body(default="valid", embed=True),
) -> dict[str, str]:
    """切换 E2E 场景（test-only）：抽取 payload + critique 行为。"""
    if extraction not in _EXTRACTION_SCENARIOS:
        raise ValueError(f"unknown extraction scenario: {extraction}")
    resume_payload, jd_payload = _EXTRACTION_SCENARIOS[extraction]
    provider.resume_payload = dict(resume_payload)
    provider.jd_payload = dict(jd_payload)
    provider.critique_payload = _critique_payload_for(critique)
    return {"extraction": extraction, "critique": critique}


@app.post("/__e2e__/reset")
def reset_database() -> dict[str, str]:
    """清空全部表（test-only），保证 E2E 用例之间相互隔离（§26）。"""
    tables = ", ".join(table.name for table in Base.metadata.sorted_tables)
    with _engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    return {"status": "reset"}

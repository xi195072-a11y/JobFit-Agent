"""pytest fixtures。

- unit 测试不需要数据库；
- 集成测试（@pytest.mark.db）需要真实 PostgreSQL(pgvector)，不可用时自动 skip，
  不会伪装通过。
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

BACKEND_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = BACKEND_ROOT / "config"


@pytest.fixture
def backend_root() -> Path:
    return BACKEND_ROOT


@pytest.fixture
def config_dir() -> Path:
    return CONFIG_DIR


def _from_env_file(key: str) -> str | None:
    """允许 backend/.env 提供 DB URL，使 pytest 在未 export 环境变量时同样连真实库。"""
    env_file = BACKEND_ROOT / ".env"
    if not env_file.is_file():
        return None
    pattern = re.compile(rf"^\s*{key}\s*=\s*(.+?)\s*$")
    for line in env_file.read_text(encoding="utf-8").splitlines():
        m = pattern.match(line)
        if m:
            return m.group(1)
    return None


def _pg_url() -> str | None:
    for key in ("TEST_DATABASE_URL", "DATABASE_URL"):
        value = os.getenv(key) or _from_env_file(key)
        if value:
            return value
    try:
        from jobfit.config.settings import get_settings

        return get_settings().database_url
    except Exception:  # noqa: BLE001
        return None


@pytest.fixture(autouse=True)
def _isolate_db(request):
    """每个 db 测试前清空全部表，保证 claim_next 只可能领到自己创建的行。

    集成测试共享同一 test database；若不清空，先前测试遗留的 queued analysis
    会被 claim_next（ORDER BY created_at）抢先领取，造成测试间串号。
    """
    if request.node.get_closest_marker("db") is None:
        yield
        return
    from jobfit.db.base import Base

    engine = request.getfixturevalue("db_engine")
    names = ", ".join(t.name for t in Base.metadata.sorted_tables)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
    yield


@pytest.fixture(scope="session")
def db_engine():
    """集成测试用真实 PostgreSQL(pgvector)。不可达/不可建表 => skip。"""
    url = _pg_url()
    if not url:
        pytest.skip("integration tests need TEST_DATABASE_URL / DATABASE_URL")
    from jobfit.db.base import Base

    engine = create_engine(url, pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        Base.metadata.create_all(engine)  # 测试态建表；生产走 Alembic migration
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"postgres(pgvector) unavailable: {exc}")

    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def unit_settings(tmp_path: Path):
    """不依赖 DB 的 settings（用于 prompts/parser/抽取构建等单测）。"""
    from jobfit.config.settings import Settings

    return Settings(
        app_env="test",
        database_url="postgresql+psycopg://jobfit:jobfit@localhost:5432/jobfit_test",
        pipeline_version="test-pipeline-1",
        llm_provider="deepseek",
        deepseek_api_key=None,
        deepseek_model="test-deterministic",
        storage_dir=tmp_path / "storage",
        config_dir=CONFIG_DIR,
        max_llm_attempts=10,
    )


@pytest.fixture
def db_settings(db_engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """集成测试专用 settings：真实 test database + 临时 storage。

    通过环境变量驱动（而非仅构造对象），确保 API 内部 `get_settings()` 与测试
    拿到完全一致的 storage_dir / database_url —— 否则 upload 与 parse 会指向不同目录。
    """
    url = _pg_url()
    assert url is not None
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("TEST_DATABASE_URL", url)
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path / "storage"))
    monkeypatch.setenv("CONFIG_DIR", str(CONFIG_DIR))
    monkeypatch.setenv("PIPELINE_VERSION", "test-pipeline-1")
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_MODEL", "test-deterministic")
    monkeypatch.setenv("MAX_LLM_ATTEMPTS", "10")
    monkeypatch.setenv("LEASE_TTL_SECONDS", "120")
    # 本机 .env 可能配置了真实 key（live 验证用）：显式置空。
    # 否则 `get_settings()` 会读到它，让"无凭证 => 503 / unavailable"这类集成测试
    # 变成真实外呼。环境变量优先于 .env，置空等价于 CI 的无密钥环境。
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")

    from jobfit.config.settings import Settings, get_settings

    get_settings.cache_clear()
    settings = Settings()
    assert settings.deepseek_api_key is not None
    assert settings.deepseek_api_key.get_secret_value() == ""
    assert settings.storage_dir == tmp_path / "storage"
    return settings


@pytest.fixture
def rule_config():
    """版本化规则视图（Phase 3 单测用；与执行期一样来自同一份 YAML 快照）。"""
    from jobfit.config.loader import ConfigLoader
    from jobfit.matching.rules import RuleConfig

    loader = ConfigLoader(CONFIG_DIR)
    return RuleConfig.from_snapshot(
        loader.config_snapshot(),
        ruleset_version=loader.ruleset_version(),
        scoring_version=loader.scoring_version(),
    )


@pytest.fixture
def session_factory(db_engine):
    from sqlalchemy.orm import sessionmaker

    return sessionmaker(bind=db_engine, expire_on_commit=False)


@pytest.fixture
def session(session_factory):
    s = session_factory()
    yield s
    s.close()


@pytest.fixture
def api_client(session_factory, db_settings):
    """FastAPI TestClient，注入 test-only deterministic provider 与 test DB。"""
    from fastapi.testclient import TestClient

    from jobfit.api.deps import get_session, get_session_factory
    from jobfit.api.v1.extractions import get_provider, get_settings_dep
    from jobfit.main import create_app
    from support import DeterministicProvider

    provider = DeterministicProvider()
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
    app.dependency_overrides[get_settings_dep] = lambda: db_settings
    client = TestClient(app)
    client.provider = provider  # type: ignore[attr-defined]
    return client


@pytest.fixture
def phase2_client(api_client):
    return api_client


@pytest.fixture
def phase3_client(api_client):
    return api_client

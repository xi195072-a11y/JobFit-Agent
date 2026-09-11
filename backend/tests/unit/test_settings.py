"""settings 类型校验 / secret 不泄漏 / 必需模板存在。"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from jobfit.config.settings import Settings

REQUIRED_ENV_KEYS = [
    "APP_ENV",
    "DATABASE_URL",
    "PIPELINE_VERSION",
    "LLM_PROVIDER",
    "DEEPSEEK_API_KEY",
    "DEEPSEEK_MODEL",
    "MAX_UPLOAD_BYTES",
    "MAX_LLM_ATTEMPTS",
    "MAX_PROVIDER_RETRIES",
    "MAX_REPAIR_RETRIES",
    "MAX_NODE_RETRIES",
    "LOG_LEVEL",
]


def test_defaults_present() -> None:
    s = Settings()
    assert s.max_upload_bytes == 10 * 1024 * 1024
    assert s.max_llm_attempts == 15
    assert s.max_node_retries == 2
    assert s.database_url.startswith("postgresql")


def test_typed_validation() -> None:
    with pytest.raises(ValidationError):
        Settings(max_llm_attempts=0)
    with pytest.raises(ValidationError):
        Settings(app_env="prod-environment")
    with pytest.raises(ValidationError):
        Settings(llm_provider="openai")


def test_secret_never_leaks_in_str() -> None:
    s = Settings(deepseek_api_key=SecretStr("sk-super-secret-value"))
    assert s.deepseek_api_key is not None
    assert s.deepseek_api_key.get_secret_value() == "sk-super-secret-value"
    assert "sk-super-secret-value" not in str(s)
    assert "sk-super-secret-value" not in repr(s)


def test_env_file_template_exists(backend_root: Path) -> None:
    example = backend_root.parent / ".env.example"
    assert example.is_file(), ".env.example 必须存在"
    content = example.read_text(encoding="utf-8")
    for key in REQUIRED_ENV_KEYS:
        assert key in content, f".env.example 缺少 {key}"


def test_allowlist_enforced_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "unknown-vendor")
    with pytest.raises(ValidationError):
        Settings()

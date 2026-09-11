"""应用配置（pydantic-settings）。密钥只经环境/.env 注入，绝不写入源码。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/src/jobfit/config/settings.py -> parents: config, jobfit, src, backend, repo根
_PKG_FILE = Path(__file__).resolve()
_BACKEND_ROOT = _PKG_FILE.parents[3]
_REPO_ROOT = _PKG_FILE.parents[4]

_ENV_FILES = [
    str(_REPO_ROOT / ".env"),
    str(_BACKEND_ROOT / ".env"),
]


class Settings(BaseSettings):
    """全局配置。所有密钥为 SecretStr，禁止打印。

    命名对齐 architecture.md §4.4/§4.5 与 ADR-018/019。
    """

    model_config = SettingsConfigDict(
        env_file=_ENV_FILES,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "local"
    database_url: str = "postgresql+psycopg://jobfit:jobfit@localhost:5432/jobfit"
    pipeline_version: str = "dev"
    llm_provider: str = "deepseek"
    deepseek_api_key: SecretStr | None = None
    deepseek_model: str = "deepseek-chat"

    # 检索用 embedding 模型（ADR-030：MVP 为确定性本地 hash embedding；
    # bge-m3 属 V1，同一 Protocol 下替换）。写入 analyses.embedding_model 以固化身份。
    embedding_model: str = "hash-ngram-v1"

    max_upload_bytes: int = Field(default=10 * 1024 * 1024, ge=1)
    max_llm_attempts: int = Field(default=15, ge=1)
    max_provider_retries: int = Field(default=3, ge=0)
    max_repair_retries: int = Field(default=1, ge=0)
    max_node_retries: int = Field(default=2, ge=0)

    log_level: str = "INFO"
    lease_ttl_seconds: int = Field(default=120, ge=1)

    # CORS（Phase 5 §39）：显式 origin 白名单（逗号分隔）；禁止 "*"。
    # 默认仅允许本地开发前端（Next.js dev server）。
    cors_allow_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    storage_dir: Path = Path("./data/uploads")
    config_dir: Path = Path("./config")

    pii_enc_key: SecretStr | None = None

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_allow_origins.split(",") if origin.strip()]

    @field_validator("app_env")
    @classmethod
    def _app_env_allowed(cls, v: str) -> str:
        if v not in {"local", "test", "prod"}:
            raise ValueError(f"app_env must be one of local/test/prod, got {v!r}")
        return v

    @field_validator("llm_provider")
    @classmethod
    def _llm_provider_allowed(cls, v: str) -> str:
        if v not in {"deepseek"}:
            raise ValueError(f"llm_provider must be 'deepseek' for now, got {v!r}")
        return v

    @field_validator("log_level")
    @classmethod
    def _log_level_upper(cls, v: str) -> str:
        return v.upper()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

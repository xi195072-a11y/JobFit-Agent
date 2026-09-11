"""FastAPI 依赖：每请求短事务 DB session。

禁止在此事务内执行 LLM/parser/external HTTP（architecture §4.3-13）。
"""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Request
from sqlalchemy.orm import Session, sessionmaker

from jobfit.config.settings import Settings, get_settings
from jobfit.db.session import make_session_factory
from jobfit.evidence.embeddings import EmbeddingProvider, build_embedding_provider
from jobfit.llm.factory import build_provider
from jobfit.llm.provider import LLMProvider

_FACTORY = None  # 延迟初始化


def _factory():
    global _FACTORY
    if _FACTORY is None:
        _FACTORY = make_session_factory(get_settings())
    return _FACTORY


def get_session(request: Request) -> Iterator[Session]:
    """每请求一个短事务 session。调用方自行 commit；异常须回滚。"""
    factory = _factory()
    session = factory()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session_factory() -> sessionmaker[Session]:
    """给需要在节点/子步骤中自行开短事务的调用方（如 workflow runner）。"""
    return _factory()


def get_embedding_provider() -> EmbeddingProvider:
    """检索用 embedding provider（ADR-030；模型由 settings.embedding_model 决定）。"""
    return build_embedding_provider(get_settings().embedding_model)


def get_llm_provider() -> LLMProvider:
    """LLM provider 依赖：默认 DeepSeek；测试通过 dependency_overrides 注入 test-only provider。"""
    return build_provider(get_settings())


def get_optional_llm_provider() -> LLMProvider | None:
    """可缺失的 LLM provider 依赖（Phase 4 critique 用）。

    未配置凭证时返回 `None` —— 调用方走 EXTERNAL CREDENTIAL BLOCKED 路径
    （critique 落 `unavailable`），**绝不**伪造 live 结果（ADR-021/ADR-036）。

    作为 FastAPI 依赖暴露，使 test-only harness 可以用 `dependency_overrides`
    注入测试用 provider（生产代码路径仍然不包含任何 fake provider，ADR-029）。
    """
    from jobfit.core.errors import ConfigurationError

    try:
        return build_provider(get_settings())
    except ConfigurationError:
        return None


def get_settings_dep() -> Settings:
    return get_settings()

"""provider 工厂：只从 settings/env 读取凭证；缺失即显式失败（不伪造 provider）。"""

from __future__ import annotations

from jobfit.config.settings import Settings
from jobfit.core.errors import ConfigurationError
from jobfit.llm.deepseek import DeepSeekProvider
from jobfit.llm.provider import LLMProvider


def build_provider(settings: Settings) -> LLMProvider:
    if settings.llm_provider != "deepseek":
        raise ConfigurationError(f"unsupported llm_provider: {settings.llm_provider}")
    key = settings.deepseek_api_key
    if key is None or not key.get_secret_value().strip():
        raise ConfigurationError(
            "DEEPSEEK_API_KEY is not configured; live LLM extraction is unavailable"
        )
    return DeepSeekProvider(api_key=key.get_secret_value(), model=settings.deepseek_model)

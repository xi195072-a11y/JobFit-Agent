"""DeepSeek LLM adapter（Provider Protocol 的第一个实现）。

只负责外部 API 交互；不接触业务数据库、不实现 retry budget
（reservation/retry 编排属于上层，ADR-018）。
structured output 经 llm.structured.parse_structured_json 校验。
"""

from __future__ import annotations

from typing import TypeVar

import httpx
from pydantic import BaseModel

from jobfit.core.errors import StructuredOutputError
from jobfit.llm.provider import LLMProvider
from jobfit.llm.structured import parse_structured_json

T = TypeVar("T", bound=BaseModel)

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
_CHAT_PATH = "/chat/completions"


class DeepSeekProvider(LLMProvider):
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_seconds: float = 60.0,
        base_url: str = DEEPSEEK_BASE_URL,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        # 生产 key 必须来自 env（settings.deepseek_api_key）；此处不校验格式。
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(
            base_url=self._base_url,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            timeout=httpx.Timeout(timeout_seconds),
        )

    @property
    def model_name(self) -> str:
        return self._model

    async def complete_text(self, prompt: str, *, max_tokens: int | None = None) -> str:
        resp = await self._client.post(
            _CHAT_PATH,
            json={
                "model": self._model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "stream": False,
            },
        )
        return self._extract_content(resp)

    async def complete_structured(self, prompt: str, *, schema: type[T]) -> T:
        resp = await self._client.post(
            _CHAT_PATH,
            json={
                "model": self._model,
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"},
                "stream": False,
            },
        )
        content = self._extract_content(resp)
        return parse_structured_json(content, schema)

    @staticmethod
    def _extract_content(resp: httpx.Response) -> str:
        if resp.status_code >= 400:
            raise StructuredOutputError(f"deepseek http {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise StructuredOutputError(f"unexpected deepseek payload: {exc}") from exc

    async def aclose(self) -> None:
        await self._client.aclose()

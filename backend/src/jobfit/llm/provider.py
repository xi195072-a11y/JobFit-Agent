"""LLM Provider Protocol。

Provider 层只负责外部 API interaction，不接触业务数据库。
LLM 输出必须经 Pydantic validation 后才可进入 domain layer（ADR-002）。
"""

from __future__ import annotations

from typing import Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


@runtime_checkable
class LLMProvider(Protocol):
    @property
    def model_name(self) -> str: ...

    async def complete_text(self, prompt: str, *, max_tokens: int | None = None) -> str: ...

    async def complete_structured(self, prompt: str, *, schema: type[T]) -> T: ...

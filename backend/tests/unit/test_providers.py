"""Provider Protocol 契约（ADR-003/ADR-006）与 DeepSeek adapter（走 MockTransport，非生产 fake）。"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from jobfit.core.enums import RequirementType
from jobfit.core.schemas import JDProfile, JDRequirement
from jobfit.llm.deepseek import DeepSeekProvider
from jobfit.llm.provider import LLMProvider

JD_JSON = json.dumps(
    {
        "document_id": "d-1",
        "parsed_document_id": "p-1",
        "company": "Tencent",
        "role_title": "AI Engineer",
        "location": "Shenzhen",
        "requirements": [
            {
                "req_type": RequirementType.SKILL.value,
                "operator": "has",
                "value": {"skill": "python"},
                "weight": 1.0,
                "is_hard": True,
            }
        ],
        "preferred_qualifications": [],
        "responsibilities": [],
    }
)


def _transport(content: str) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        payload = {"choices": [{"message": {"content": content}}]}
        return httpx.Response(200, json=payload)

    return httpx.MockTransport(handler)


def test_llm_provider_is_runtime_protocol() -> None:
    assert isinstance(LLMProvider, type)
    client = httpx.AsyncClient(transport=_transport("ok"), base_url="http://x")
    provider = DeepSeekProvider(api_key="test-key", model="m", client=client)
    assert isinstance(provider, LLMProvider)
    assert provider.model_name == "m"


def test_deepseek_complete_structured_parses_and_validates() -> None:
    async def run() -> JDProfile:
        client = httpx.AsyncClient(transport=_transport(JD_JSON), base_url="http://x")
        provider = DeepSeekProvider(api_key="test-key", model="m", client=client)
        try:
            return await provider.complete_structured("", schema=JDProfile)
        finally:
            await client.aclose()

    profile = asyncio.run(run())
    assert profile.company == "Tencent"
    req = profile.requirements[0]
    assert isinstance(req, JDRequirement)
    assert req.is_hard is True


def test_deepseek_http_error_raises_structured_error() -> None:
    async def run() -> None:
        transport = httpx.MockTransport(lambda req: httpx.Response(500, text="boom"))
        client = httpx.AsyncClient(transport=transport, base_url="http://x")
        provider = DeepSeekProvider(api_key="k", model="m", client=client)
        try:
            await provider.complete_text("hi")
        finally:
            await client.aclose()

    with pytest.raises(Exception):
        asyncio.run(run())


def test_embedding_protocol_signature() -> None:
    """契约：embed(texts) -> list[list[float]]。本 Phase 无实现 adapter（bge 属 V1）。"""
    from jobfit.evidence.embeddings import EmbeddingProvider

    class DummyEmbedder:
        @property
        def model_name(self) -> str:
            return "test-embed"

        def embed(self, texts: list[str]) -> list[list[float]]:
            return [[0.0] * 8 for _ in texts]

    assert isinstance(DummyEmbedder(), EmbeddingProvider)

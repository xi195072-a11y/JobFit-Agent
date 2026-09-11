"""unit: 检索的确定性构件（tokenizer / lexical 打分 / anchor gate / embedding）。"""

from __future__ import annotations

import math

import pytest

from jobfit.core.errors import ConfigurationError
from jobfit.evidence.embeddings import (
    EMBEDDING_DIM,
    HASH_EMBEDDING_MODEL,
    HashingEmbeddingProvider,
    build_embedding_provider,
)
from jobfit.evidence.keyword import lexical_score, tokenize
from jobfit.evidence.retrieval import passes_anchor_gate


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return 0.0 if na == 0 or nb == 0 else dot / (na * nb)


def test_tokenize_is_deterministic_and_covers_cjk() -> None:
    assert tokenize("Python, FastAPI") == ["python", "fastapi"]
    tokens = tokenize("北京大学")
    assert "北" in tokens and "北京" in tokens  # 单字 + 二元组
    assert tokenize("数学 数学") == tokenize("数学 数学")


def test_lexical_score_zero_without_overlap() -> None:
    assert lexical_score("kubernetes", "Java, Spring Boot") == 0.0
    assert lexical_score("", "anything") == 0.0
    assert lexical_score("python", "Python, FastAPI") > 0.0


def test_lexical_score_is_deterministic() -> None:
    a = lexical_score("熟悉 Python", "技能: Python, FastAPI")
    b = lexical_score("熟悉 Python", "技能: Python, FastAPI")
    assert a == b


def test_anchor_gate_requires_literal_term() -> None:
    assert passes_anchor_gate("技能: Python, FastAPI", ["Python"]) is True
    assert passes_anchor_gate("技能: Python, FastAPI", ["python"]) is True  # 大小写无关
    assert passes_anchor_gate("技能: Java", ["Python"]) is False
    # 空 gate => 不做限制（调用方自担精度）
    assert passes_anchor_gate("任意文本", []) is True
    # 关键：不会把"未提及"当成命中
    assert passes_anchor_gate("使用 Redis 做缓存", ["Kubernetes"]) is False


def test_embedding_provider_is_deterministic_and_normalized() -> None:
    provider = HashingEmbeddingProvider()
    first = provider.embed(["Python, FastAPI"])[0]
    second = provider.embed(["Python, FastAPI"])[0]
    assert first == second
    assert len(first) == EMBEDDING_DIM
    norm = math.sqrt(sum(value * value for value in first))
    assert norm == pytest.approx(1.0, abs=1e-6)


def test_embedding_is_order_insensitive_within_text() -> None:
    provider = HashingEmbeddingProvider()
    assert provider.embed(["python docker"])[0] == provider.embed(["docker python"])[0]


def test_embedding_distinguishes_different_text() -> None:
    provider = HashingEmbeddingProvider()
    a, b = provider.embed(["Python, FastAPI, PostgreSQL"])[0], provider.embed(["Java, Spring Boot"])[0]
    assert a != b
    assert _cosine(a, b) < _cosine(a, a)


def test_embedding_factory_rejects_unknown_model() -> None:
    with pytest.raises(ConfigurationError):
        build_embedding_provider("bge-m3")
    assert build_embedding_provider(HASH_EMBEDDING_MODEL).model_name == HASH_EMBEDDING_MODEL

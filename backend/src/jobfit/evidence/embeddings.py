"""EmbeddingProvider Protocol（ADR-006）与 MVP 的确定性本地实现（ADR-030）。

层次：
- `EmbeddingProvider`：跨模型边界；禁止跨模型混用（architecture §7）。
- `HashingEmbeddingProvider`：MVP 的**真实**本地实现——feature hashing over
  词/CJK 二元组（与 lexical 共用同一 tokenizer），L2 归一化，纯函数、无网络、无新增依赖。
  它是**词面**表示，不是语义模型；不得对外声称具备语义理解能力。
- ADR-006 规划的本地 `bge-m3` 属 V1，将在同一 Protocol 下替换（只改配置，不改调用方）。
- 没有 embedding 时检索必须仍可用（lexical fallback），见 evidence/retrieval.py。
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from jobfit.core.errors import ConfigurationError
from jobfit.evidence.keyword import tokenize

# 与 document_chunks.embedding (Vector(1024)) 对齐；改动需同步 migration。
EMBEDDING_DIM = 1024
HASH_EMBEDDING_MODEL = "hash-ngram-v1"

_DIGEST_SIZE = 16


@runtime_checkable
class EmbeddingProvider(Protocol):
    @property
    def model_name(self) -> str: ...

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """把一批文本编码为向量。禁止跨模型混用（architecture §7 embedding 约束）。"""
        ...


class HashingEmbeddingProvider:
    """确定性 feature-hashing embedding（同一文本 => 同一向量，逐位可复现）。"""

    def __init__(self, *, model_name: str = HASH_EMBEDDING_MODEL, dim: int = EMBEDDING_DIM) -> None:
        if dim <= 0:
            raise ConfigurationError(f"embedding dim must be positive, got {dim}")
        self._model_name = model_name
        self._dim = dim

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self._dim
        counts: dict[str, int] = {}
        for token in tokenize(text):
            counts[token] = counts.get(token, 0) + 1
        # 按 token 排序保证累加顺序与输入顺序无关 => 同一 token 多重集得到同一向量。
        for token in sorted(counts):
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=_DIGEST_SIZE).digest()
            index = int.from_bytes(digest[:8], "big") % self._dim
            sign = 1.0 if digest[8] % 2 == 0 else -1.0
            # sublinear tf：出现次数的影响为 1 + log(tf)
            vector[index] += sign * (1.0 + math.log(counts[token]))
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0.0:
            return vector
        return [round(value / norm, 12) for value in vector]


def build_embedding_provider(model_name: str) -> EmbeddingProvider:
    """按配置构造 embedding provider；未知模型显式失败（不静默降级）。"""
    if model_name == HASH_EMBEDDING_MODEL:
        return HashingEmbeddingProvider(model_name=model_name)
    raise ConfigurationError(
        f"unsupported embedding model: {model_name!r} "
        f"(available: {HASH_EMBEDDING_MODEL}; 本地 bge-m3 属 V1，见 ADR-006/ADR-030)"
    )

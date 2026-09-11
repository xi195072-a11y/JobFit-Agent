"""证据检索服务（Phase 3 §14–§17/§31/§35）。

- 输入：query / candidate document scope（parsed_document_id 列表）/ top_k / 可选 metadata filter
  / 可选 anchor gate 词表。
- 输出：source_chunk_id、document_id、parsed_document_id、chunk_index、page、span、score、retrieval_method。
- **document scope 强制隔离**：只检索传入的 parsed_document_id，绝不跨文档泄漏（§35）。
- **确定性排序**：score DESC → chunk_index ASC → chunk id ASC（同分必有稳定 tie-breaker，§16）。
- **精度来自确定性 anchor gate，而不是相似度阈值**（ADR-030 实测结论）：
  在中文文本上，纯词面/纯向量相似度无法区分"相关 / 不相关"（相关 0.20 vs 不相关 0.16），
  因此命中必须**字面包含**要求的关键词（技能名等 distinctive term）；向量只用于**排序**。
  被 gate 排除 => 无证据 => UNKNOWN，绝不当作 FALSE（§17/§21）。
- embedding 计算发生在事务之外，落库用短事务（§31）；向量检索只使用同一 embedding_model
  （禁止跨模型混用，architecture §7）。
- 没有 embedding 时退化为 lexical（ADR-006：embedding 是加分项，不是前提）。
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from jobfit.core.enums import RetrievalMethod
from jobfit.core.text import form_normalize
from jobfit.db import models
from jobfit.evidence.embeddings import EMBEDDING_DIM, EmbeddingProvider
from jobfit.evidence.keyword import lexical_score

_UPDATE_EMBEDDING_SQL = text(
    "UPDATE document_chunks SET embedding = CAST(:vec AS vector), embedding_model = :model WHERE id = :id"
)
# 向量路径需要先取回更大的候选集，再在 Python 侧施加 anchor gate（gate 依赖文本归一化）。
_VECTOR_CANDIDATE_FACTOR = 8
_VECTOR_CANDIDATE_MIN = 32


@dataclass(frozen=True)
class RetrievalHit:
    source_chunk_id: uuid.UUID
    document_id: uuid.UUID
    parsed_document_id: uuid.UUID
    chunk_index: int
    page: int | None
    char_start: int
    char_end: int
    score: float
    retrieval_method: str


@dataclass(frozen=True)
class RetrievalResult:
    hits: list[RetrievalHit]
    retrieval_method: str
    embedding_model: str | None
    scope_size: int
    considered_chunks: int

    @property
    def evidence_ids(self) -> list[str]:
        return [str(hit.source_chunk_id) for hit in self.hits]


@dataclass(frozen=True)
class IndexOutcome:
    embedded: int
    model: str


def _vector_literal(vector: Sequence[float]) -> str:
    return "[" + ",".join(repr(float(value)) for value in vector) + "]"


def passes_anchor_gate(content: str, anchor_terms: Sequence[str]) -> bool:
    """anchor gate：命中必须字面包含任一 anchor term（形式归一后子串匹配）。

    anchor_terms 为空 => 不做 gate（调用方自担精度）。
    """
    terms = [form_normalize(term) for term in anchor_terms]
    usable = [term for term in terms if term]
    if not usable:
        return True
    haystack = form_normalize(content)
    return any(term in haystack for term in usable)


def ensure_chunk_embeddings(
    session: Session,
    provider: EmbeddingProvider,
    parsed_document_ids: Sequence[uuid.UUID],
) -> IndexOutcome:
    """为 scope 内缺失/换模型的 chunk 生成 embedding（计算在事务外，落库为短事务）。"""
    ids = list(dict.fromkeys(parsed_document_ids))
    if not ids:
        return IndexOutcome(embedded=0, model=provider.model_name)

    model = provider.model_name
    pending = list(
        session.execute(
            select(models.DocumentChunk.id, models.DocumentChunk.content)
            .where(models.DocumentChunk.parsed_document_id.in_(ids))
            .where(
                (models.DocumentChunk.embedding.is_(None))
                | (models.DocumentChunk.embedding_model.is_distinct_from(model))
            )
            .order_by(models.DocumentChunk.parsed_document_id, models.DocumentChunk.chunk_index)
        ).all()
    )
    if not pending:
        return IndexOutcome(embedded=0, model=model)

    vectors = provider.embed([row.content for row in pending])
    for row, vector in zip(pending, vectors, strict=True):
        if len(vector) != EMBEDDING_DIM:
            raise ValueError(
                f"embedding dim mismatch: provider gave {len(vector)}, column expects {EMBEDDING_DIM}"
            )
        session.execute(
            _UPDATE_EMBEDDING_SQL,
            {"vec": _vector_literal(vector), "model": model, "id": row.id},
        )
    session.commit()
    return IndexOutcome(embedded=len(pending), model=model)


def _scope_is_fully_embedded(
    session: Session, parsed_document_ids: Sequence[uuid.UUID], model: str
) -> bool:
    ids = list(parsed_document_ids)
    total = session.execute(
        select(func.count())
        .select_from(models.DocumentChunk)
        .where(models.DocumentChunk.parsed_document_id.in_(ids))
    ).scalar_one()
    if total == 0:
        return False
    ready = session.execute(
        select(func.count())
        .select_from(models.DocumentChunk)
        .where(models.DocumentChunk.parsed_document_id.in_(ids))
        .where(models.DocumentChunk.embedding.is_not(None))
        .where(models.DocumentChunk.embedding_model == model)
    ).scalar_one()
    return int(ready) == int(total)


def _base_stmt(parsed_document_ids: Sequence[uuid.UUID], page: int | None):
    stmt = (
        select(
            models.DocumentChunk.id,
            models.ParsedDocument.document_id,
            models.DocumentChunk.parsed_document_id,
            models.DocumentChunk.chunk_index,
            models.DocumentChunk.page,
            models.DocumentChunk.char_start,
            models.DocumentChunk.char_end,
            models.DocumentChunk.content,
        )
        .select_from(models.DocumentChunk)
        .join(models.ParsedDocument, models.ParsedDocument.id == models.DocumentChunk.parsed_document_id)
        .where(models.DocumentChunk.parsed_document_id.in_(list(parsed_document_ids)))
    )
    if page is not None:
        stmt = stmt.where(models.DocumentChunk.page == page)
    return stmt


def _to_hit(row, score: float, method: str) -> RetrievalHit:
    return RetrievalHit(
        source_chunk_id=row.id,
        document_id=row.document_id,
        parsed_document_id=row.parsed_document_id,
        chunk_index=int(row.chunk_index),
        page=row.page,
        char_start=int(row.char_start),
        char_end=int(row.char_end),
        score=round(float(score), 12),
        retrieval_method=method,
    )


def _lexical_hits(
    session: Session,
    *,
    query: str,
    parsed_document_ids: Sequence[uuid.UUID],
    top_k: int,
    page: int | None,
    anchor_terms: Sequence[str],
    min_score: float,
) -> list[RetrievalHit]:
    rows = session.execute(_base_stmt(parsed_document_ids, page)).all()
    candidates: list[tuple[float, int, str, RetrievalHit]] = []
    for row in rows:
        if not passes_anchor_gate(row.content, anchor_terms):
            continue
        score = lexical_score(query, row.content)
        if score <= min_score:
            continue
        hit = _to_hit(row, score, RetrievalMethod.LEXICAL.value)
        candidates.append((hit.score, hit.chunk_index, str(hit.source_chunk_id), hit))
    candidates.sort(key=lambda item: (-item[0], item[1], item[2]))
    return [item[3] for item in candidates[:top_k]]


def _vector_hits(
    session: Session,
    *,
    query_vector: Sequence[float],
    parsed_document_ids: Sequence[uuid.UUID],
    model: str,
    top_k: int,
    page: int | None,
    anchor_terms: Sequence[str],
    min_similarity: float,
) -> list[RetrievalHit]:
    fetch_limit = max(top_k * _VECTOR_CANDIDATE_FACTOR, _VECTOR_CANDIDATE_MIN)
    distance = models.DocumentChunk.embedding.cosine_distance(list(query_vector))
    stmt = (
        _base_stmt(parsed_document_ids, page)
        .add_columns((1.0 - distance).label("score"))
        .where(models.DocumentChunk.embedding.is_not(None))
        .where(models.DocumentChunk.embedding_model == model)
        .order_by(
            distance.asc(),
            models.DocumentChunk.chunk_index.asc(),
            models.DocumentChunk.id.asc(),
        )
        .limit(fetch_limit)
    )
    hits: list[RetrievalHit] = []
    for row in session.execute(stmt).all():
        if not passes_anchor_gate(row.content, anchor_terms):
            continue
        score = round(float(row.score), 12)
        if score < min_similarity:
            continue
        hits.append(_to_hit(row, score, RetrievalMethod.VECTOR.value))
        if len(hits) >= top_k:
            break
    return hits


def count_scope_chunks(session: Session, parsed_document_ids: Sequence[uuid.UUID]) -> int:
    ids = list(parsed_document_ids)
    if not ids:
        return 0
    return int(
        session.execute(
            select(func.count())
            .select_from(models.DocumentChunk)
            .where(models.DocumentChunk.parsed_document_id.in_(ids))
        ).scalar_one()
    )


def retrieve_evidence(
    session: Session,
    *,
    query: str,
    parsed_document_ids: Sequence[uuid.UUID],
    top_k: int = 5,
    provider: EmbeddingProvider | None = None,
    method: str = "auto",
    page: int | None = None,
    anchor_terms: Sequence[str] = (),
    min_similarity: float = 0.0,
    min_lexical_score: float = 0.0,
) -> RetrievalResult:
    """在 candidate document scope 内检索证据。

    method:
      - "auto"：scope 内 chunk 全部具备同模型 embedding => vector；否则 lexical。
      - "vector" / "lexical"：显式指定（vector 需要 provider）。
    """
    if top_k <= 0:
        raise ValueError("top_k must be positive")
    if method not in {"auto", "vector", "lexical"}:
        raise ValueError(f"unsupported retrieval method: {method!r}")
    ids = list(dict.fromkeys(parsed_document_ids))
    if not ids:
        return RetrievalResult(
            hits=[],
            retrieval_method=RetrievalMethod.LEXICAL.value,
            embedding_model=provider.model_name if provider is not None else None,
            scope_size=0,
            considered_chunks=0,
        )

    use_vector = method == "vector"
    if method == "auto":
        use_vector = provider is not None and _scope_is_fully_embedded(session, ids, provider.model_name)
    if use_vector and provider is None:
        raise ValueError("vector retrieval requires an embedding provider")

    considered = count_scope_chunks(session, ids)
    embedding_model = provider.model_name if provider is not None else None
    if use_vector:
        assert provider is not None
        query_vector = provider.embed([query])[0]
        hits = _vector_hits(
            session,
            query_vector=query_vector,
            parsed_document_ids=ids,
            model=provider.model_name,
            top_k=top_k,
            page=page,
            anchor_terms=anchor_terms,
            min_similarity=min_similarity,
        )
        used_method = RetrievalMethod.VECTOR.value
    else:
        hits = _lexical_hits(
            session,
            query=query,
            parsed_document_ids=ids,
            top_k=top_k,
            page=page,
            anchor_terms=anchor_terms,
            min_score=min_lexical_score,
        )
        used_method = RetrievalMethod.LEXICAL.value
    return RetrievalResult(
        hits=hits,
        retrieval_method=used_method,
        embedding_model=embedding_model,
        scope_size=len(ids),
        considered_chunks=considered,
    )

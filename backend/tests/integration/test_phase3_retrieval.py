# mypy: disable-error-code="arg-type,attr-defined,assignment,union-attr"
"""integration: 真实 pgvector 检索（Phase 3 §16/§31/§35）。

不 mock 向量检索：使用真实 PostgreSQL + pgvector 列与 `<=>` 距离算子。
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from jobfit.db import models
from jobfit.db.repositories import parse_artifacts as pa_repo
from jobfit.evidence.embeddings import EMBEDDING_DIM, HASH_EMBEDDING_MODEL, HashingEmbeddingProvider
from jobfit.evidence.retrieval import ensure_chunk_embeddings, retrieve_evidence
from jobfit.parsing.service import parse_document
from support import make_document

pytestmark = pytest.mark.db

RESUME_A = (
    "张三\n技能\nPython, FastAPI, PostgreSQL, Docker\n"
    "工作经历\n2020-07 - 2024-07  甲公司  后端开发工程师  使用 Python 开发服务\n"
)
RESUME_B = "李四\n技能\nKubernetes, Go\n工作经历\n2019-07 - 2024-07  乙公司  SRE\n"

_LONG_A = RESUME_A + ("Python 服务开发与 PostgreSQL 建模\n" * 60)
_LONG_B = RESUME_B + ("Kubernetes 集群运维与 Go 服务\n" * 60)


def _prepared(session: Session, settings, *, fixture: str, kind: str, text_body: str) -> uuid.UUID:
    document = make_document(
        session, settings, kind=kind, filename=fixture, content=text_body.encode("utf-8")
    )
    outcome = parse_document(session, settings, document)
    return outcome.parsed_document_id


def _embed(session: Session, parsed_ids: list[uuid.UUID]) -> None:
    provider = HashingEmbeddingProvider()
    ensure_chunk_embeddings(session, provider, parsed_ids)


# ---------------------------------------------------------------- 向量路径真实可用


def test_vector_path_is_real_and_metadata_recorded(session: Session, db_settings) -> None:
    parsed_id = _prepared(session, db_settings, fixture="long_a.txt", kind="resume", text_body=_LONG_A)
    _embed(session, [parsed_id])

    chunks = pa_repo.list_chunks(session, parsed_id)
    assert len(chunks) >= 2  # 长文本确实被切成多个 chunk
    stored = session.execute(
        text(
            "SELECT count(*) FROM document_chunks"
            " WHERE parsed_document_id = :p AND embedding IS NOT NULL AND embedding_model = :m"
        ),
        {"p": parsed_id, "m": HASH_EMBEDDING_MODEL},
    ).scalar_one()
    assert int(stored) == len(chunks)

    provider = HashingEmbeddingProvider()
    result = retrieve_evidence(
        session,
        query="Python",
        parsed_document_ids=[parsed_id],
        top_k=3,
        provider=provider,
        anchor_terms=["Python"],
    )
    assert result.retrieval_method == "vector"
    assert result.embedding_model == HASH_EMBEDDING_MODEL
    assert result.scope_size == 1
    assert result.considered_chunks == len(chunks)
    assert len(result.hits) >= 1
    assert all(hit.retrieval_method == "vector" for hit in result.hits)
    assert all(hit.parsed_document_id == parsed_id for hit in result.hits)


def test_lexical_fallback_without_embedding_provider(session: Session, db_settings) -> None:
    parsed_id = _prepared(session, db_settings, fixture="long_a.txt", kind="resume", text_body=_LONG_A)
    result = retrieve_evidence(
        session,
        query="Python",
        parsed_document_ids=[parsed_id],
        top_k=3,
        provider=None,
        anchor_terms=["Python"],
    )
    assert result.retrieval_method == "lexical"
    assert result.embedding_model is None
    assert result.hits  # 无 embedding 也必须可用（ADR-006）


# ---------------------------------------------------------------- 确定性与 top_k


def test_retrieval_is_deterministic_and_respects_top_k(session: Session, db_settings) -> None:
    parsed_id = _prepared(session, db_settings, fixture="long_a.txt", kind="resume", text_body=_LONG_A)
    _embed(session, [parsed_id])
    provider = HashingEmbeddingProvider()

    def _ids(top_k: int) -> list[str]:
        result = retrieve_evidence(
            session,
            query="Python 服务开发",
            parsed_document_ids=[parsed_id],
            top_k=top_k,
            provider=provider,
            anchor_terms=["Python"],
        )
        return [str(hit.source_chunk_id) for hit in result.hits]

    assert _ids(3) == _ids(3)  # 同输入 => 同排序
    assert _ids(2) == _ids(3)[:2]  # top_k 截断保持前缀一致
    assert len(_ids(1)) == 1


def test_tie_break_is_chunk_index_then_id(session: Session, db_settings) -> None:
    """同分时必须确定性：chunk_index 升序，再次为 chunk id 升序（§16）。"""
    document = make_document(
        session, db_settings, kind="resume", filename="ties.txt", content=b"Python Python Python"
    )
    parsed = parse_document(session, db_settings, document)
    # 直接插入内容完全相同的 3 个 chunk，制造"同分"场景（真实行，仅创建路径不同）
    for index in (10, 11, 12):
        session.add(
            models.DocumentChunk(
                parsed_document_id=parsed.parsed_document_id,
                chunk_index=index,
                content="Python Python Python",
                page=1,
                char_start=index,
                char_end=index + 23,
                span_sha256=f"tie-{index}",
            )
        )
    session.commit()
    _embed(session, [parsed.parsed_document_id])

    result = retrieve_evidence(
        session,
        query="Python",
        parsed_document_ids=[parsed.parsed_document_id],
        top_k=3,
        provider=HashingEmbeddingProvider(),
        anchor_terms=["Python"],
    )
    indexes = [hit.chunk_index for hit in result.hits]
    assert indexes == sorted(indexes)
    scores = [hit.score for hit in result.hits]
    assert len(set(scores)) == 1  # 确为同分场景，验证的是 tie-breaker 而非分数


# ---------------------------------------------------------------- scope 隔离


def test_document_scope_isolation_prevents_cross_document_leakage(
    session: Session, db_settings
) -> None:
    parsed_a = _prepared(session, db_settings, fixture="long_a.txt", kind="resume", text_body=_LONG_A)
    parsed_b = _prepared(session, db_settings, fixture="long_b.txt", kind="resume", text_body=_LONG_B)
    _embed(session, [parsed_a, parsed_b])
    provider = HashingEmbeddingProvider()

    scoped = retrieve_evidence(
        session,
        query="Kubernetes 集群运维",
        parsed_document_ids=[parsed_a],  # 只允许 A
        top_k=5,
        provider=provider,
        anchor_terms=["Kubernetes"],
    )
    assert scoped.hits == []  # A 中没有 Kubernetes => 无证据，且绝不返回 B 的 chunk
    assert scoped.scope_size == 1

    in_b = retrieve_evidence(
        session,
        query="Kubernetes 集群运维",
        parsed_document_ids=[parsed_b],
        top_k=5,
        provider=provider,
        anchor_terms=["Kubernetes"],
    )
    assert in_b.hits
    assert {hit.parsed_document_id for hit in in_b.hits} == {parsed_b}


def test_no_evidence_returns_empty_instead_of_weak_matches(session: Session, db_settings) -> None:
    """anchor gate 保证"没有证据"就是空结果，而不是相似度噪声（§17/§21）。"""
    parsed_id = _prepared(session, db_settings, fixture="long_a.txt", kind="resume", text_body=_LONG_A)
    _embed(session, [parsed_id])
    result = retrieve_evidence(
        session,
        query="熟悉 PyTorch",
        parsed_document_ids=[parsed_id],
        top_k=5,
        provider=HashingEmbeddingProvider(),
        anchor_terms=["PyTorch"],
    )
    assert result.hits == []
    assert result.considered_chunks >= 1  # 确实检索过，只是在 gate 处被排除


# ---------------------------------------------------------------- metadata filter


def test_page_filter_is_applied(session: Session, db_settings) -> None:
    parsed_id = _prepared(session, db_settings, fixture="long_a.txt", kind="resume", text_body=_LONG_A)
    _embed(session, [parsed_id])
    provider = HashingEmbeddingProvider()

    page_one = retrieve_evidence(
        session,
        query="Python",
        parsed_document_ids=[parsed_id],
        top_k=5,
        provider=provider,
        page=1,
        anchor_terms=["Python"],
    )
    assert page_one.hits
    assert all(hit.page == 1 for hit in page_one.hits)

    page_nine = retrieve_evidence(
        session,
        query="Python",
        parsed_document_ids=[parsed_id],
        top_k=5,
        provider=provider,
        page=9,
        anchor_terms=["Python"],
    )
    assert page_nine.hits == []


def test_empty_scope_and_invalid_arguments(session: Session, db_settings) -> None:
    empty = retrieve_evidence(session, query="Python", parsed_document_ids=[], top_k=5)
    assert empty.hits == [] and empty.scope_size == 0
    with pytest.raises(ValueError):
        retrieve_evidence(session, query="x", parsed_document_ids=[uuid.uuid4()], top_k=0)
    with pytest.raises(ValueError):
        retrieve_evidence(session, query="x", parsed_document_ids=[uuid.uuid4()], method="magic")
    with pytest.raises(ValueError):
        retrieve_evidence(session, query="x", parsed_document_ids=[uuid.uuid4()], method="vector")


# ---------------------------------------------------------------- embedding 维度与模型约束


def test_embedding_dimension_matches_column(session: Session, db_settings) -> None:
    provider = HashingEmbeddingProvider()
    vector = provider.embed(["Python"])[0]
    assert len(vector) == EMBEDDING_DIM

    parsed_id = _prepared(session, db_settings, fixture="long_a.txt", kind="resume", text_body=_LONG_A)
    _embed(session, [parsed_id])
    raw = session.execute(
        text("SELECT vector_dims(embedding) FROM document_chunks WHERE parsed_document_id = :p LIMIT 1"),
        {"p": parsed_id},
    ).scalar_one()
    assert int(raw) == EMBEDDING_DIM


def test_scope_not_fully_embedded_falls_back_to_lexical(session: Session, db_settings) -> None:
    """部分 chunk 未嵌入时不得走向量路径（否则会静默漏检）=> auto 退化为 lexical。"""
    parsed_id = _prepared(session, db_settings, fixture="long_a.txt", kind="resume", text_body=_LONG_A)
    chunks = pa_repo.list_chunks(session, parsed_id)
    assert len(chunks) >= 2
    _embed(session, [parsed_id])
    # 人为让其中一个 chunk 属于"旧模型" => scope 不再"全部就绪"
    session.execute(
        text("UPDATE document_chunks SET embedding_model = 'stale-model' WHERE id = :id"),
        {"id": chunks[-1].id},
    )
    session.commit()
    result = retrieve_evidence(
        session,
        query="Python",
        parsed_document_ids=[parsed_id],
        top_k=5,
        provider=HashingEmbeddingProvider(),
        anchor_terms=["Python"],
    )
    assert result.retrieval_method == "lexical"
    assert result.hits  # 退化路径仍然可用


def test_embedding_model_change_reindexes(session: Session, db_settings) -> None:
    """换模型必须重新索引（禁止跨模型混用，architecture §7）。"""
    parsed_id = _prepared(session, db_settings, fixture="long_a.txt", kind="resume", text_body=_LONG_A)
    _embed(session, [parsed_id])
    session.execute(
        text("UPDATE document_chunks SET embedding_model = 'stale-model' WHERE parsed_document_id = :p"),
        {"p": parsed_id},
    )
    session.commit()

    outcome = ensure_chunk_embeddings(session, HashingEmbeddingProvider(), [parsed_id])
    assert outcome.embedded >= 1
    models_used = set(
        session.execute(
            select(models.DocumentChunk.embedding_model).where(
                models.DocumentChunk.parsed_document_id == parsed_id
            )
        )
        .scalars()
        .all()
    )
    assert models_used == {HASH_EMBEDDING_MODEL}

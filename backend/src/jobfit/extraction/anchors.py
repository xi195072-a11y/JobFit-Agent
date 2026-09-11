"""确定性 evidence grounding（ADR-007 / 需求 12）。

LLM 只被允许给出"逐字引用"；由本模块在**真实 chunk 文本**中定位该引用，
派生 chunk_id 与字符区间。定位失败 => 该条目被丢弃 + extraction_warning，
绝不使用模型给的偏移量，也绝不编造 anchor。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from jobfit.core.enums import DocumentKind
from jobfit.core.schemas import SourceAnchor


class ChunkLike(Protocol):
    """chunk 的最小结构化契约（ORM DocumentChunk 与测试替身都满足）。"""

    id: object
    content: str
    char_start: int
    char_end: int
    page: int | None


@dataclass
class Grounding:
    evidence_ids: list[str] = field(default_factory=list)
    anchors: list[SourceAnchor] = field(default_factory=list)
    matched_quotes: list[str] = field(default_factory=list)
    unmatched_quotes: list[str] = field(default_factory=list)

    @property
    def grounded(self) -> bool:
        return bool(self.anchors)


def _norm_with_map(text: str) -> tuple[str, list[int]]:
    """把连续空白折叠为单个空格，并记录每个规范化字符对应的原始下标。"""
    out: list[str] = []
    pos: list[int] = []
    prev_space = False
    for index, char in enumerate(text):
        if char.isspace():
            if prev_space:
                continue
            out.append(" ")
            pos.append(index)
            prev_space = True
        else:
            out.append(char)
            pos.append(index)
            prev_space = False
    return "".join(out), pos


def ground_quotes(
    *,
    quotes: list[str],
    chunks: Sequence[ChunkLike],
    parsed_document_id: str,
    doc_kind: DocumentKind,
) -> Grounding:
    grounding = Grounding()
    seen_ids: set[str] = set()
    for quote in quotes:
        quote_norm, _ = _norm_with_map(quote)
        if not quote_norm.strip():
            grounding.unmatched_quotes.append(quote)
            continue
        found = False
        for chunk in chunks:
            chunk_norm, mapping = _norm_with_map(chunk.content)
            at = chunk_norm.find(quote_norm)
            if at < 0:
                continue
            local_start = mapping[at]
            local_end = mapping[at + len(quote_norm) - 1] + 1
            grounding.anchors.append(
                SourceAnchor(
                    doc_kind=doc_kind,
                    parsed_document_id=parsed_document_id,
                    page=chunk.page,
                    char_start=int(chunk.char_start) + local_start,
                    char_end=int(chunk.char_start) + local_end,
                )
            )
            chunk_id = str(chunk.id)
            if chunk_id not in seen_ids:
                seen_ids.add(chunk_id)
                grounding.evidence_ids.append(chunk_id)
            grounding.matched_quotes.append(quote)
            found = True
            break
        if not found:
            grounding.unmatched_quotes.append(quote)
    return grounding


def find_literal(
    *,
    literal: str,
    chunks: Sequence[ChunkLike],
    parsed_document_id: str,
    doc_kind: DocumentKind,
) -> tuple[SourceAnchor | None, str | None]:
    """标量字段（email/phone/name/location）的存在性校验：找不到即视为无证据。"""
    result = ground_quotes(
        quotes=[literal],
        chunks=chunks,
        parsed_document_id=parsed_document_id,
        doc_kind=doc_kind,
    )
    if not result.anchors:
        return None, None
    return result.anchors[0], result.evidence_ids[0]

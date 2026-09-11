"""确定性 chunker（无随机、无时间依赖）。

同一 parse artifact + 同一 chunker 版本/参数 => 完全相同的 chunk 结构。
chunk 参数属于 parse artifact 身份的一部分（见 parsing/service.py 的 PARSE_VERSION）。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from jobfit.parsing.base import PageSpan

CHUNKER_VERSION = "c1.0.0"
CHUNK_SIZE = 1200
CHUNK_OVERLAP = 200


@dataclass(frozen=True)
class ChunkSpec:
    chunk_index: int
    content: str
    char_start: int
    char_end: int
    page: int | None

    @property
    def span_sha256(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()


def _page_for(char_start: int, pages: list[PageSpan]) -> int | None:
    for span in pages:
        if span.char_start <= char_start < span.char_end:
            return span.page
    return pages[-1].page if pages else None


def chunk_text(
    text: str,
    pages: list[PageSpan],
    *,
    size: int = CHUNK_SIZE,
    overlap: int = CHUNK_OVERLAP,
) -> list[ChunkSpec]:
    """滑动窗口切分。窗口完全落在空白上时跳过，但索引连续（deterministic）。"""
    if size <= 0:
        raise ValueError("size must be positive")
    if overlap < 0 or overlap >= size:
        raise ValueError("overlap must satisfy 0 <= overlap < size")

    specs: list[ChunkSpec] = []
    step = size - overlap
    length = len(text)
    start = 0
    index = 0
    while start < length:
        end = min(start + size, length)
        content = text[start:end]
        if content.strip():
            specs.append(
                ChunkSpec(
                    chunk_index=index,
                    content=content,
                    char_start=start,
                    char_end=end,
                    page=_page_for(start, pages),
                )
            )
            index += 1
        if end >= length:
            break
        start += step
    return specs

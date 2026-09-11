"""chunker 单测：确定性、偏移正确、空白窗口跳过、参数校验。"""

from __future__ import annotations

import pytest

from jobfit.parsing.base import PageSpan
from jobfit.parsing.chunker import ChunkSpec, chunk_text

PAGES = [PageSpan(page=1, char_start=0, char_end=100000)]


def test_chunker_deterministic() -> None:
    text = "".join(f"line {i}\n" for i in range(200))
    first = chunk_text(text, PAGES)
    second = chunk_text(text, PAGES)
    assert [(c.chunk_index, c.content, c.char_start, c.char_end) for c in first] == [
        (c.chunk_index, c.content, c.char_start, c.char_end) for c in second
    ]
    assert [c.span_sha256 for c in first] == [c.span_sha256 for c in second]


def test_chunker_offsets_match_source_text() -> None:
    text = "".join(f"abcdefghij{i}\n" for i in range(400))
    specs = chunk_text(text, PAGES, size=300, overlap=50)
    assert specs
    for spec in specs:
        assert text[spec.char_start : spec.char_end] == spec.content
        assert spec.chunk_index >= 0


def test_chunker_full_coverage_without_overlap() -> None:
    text = "x" * 2500
    specs = chunk_text(text, PAGES, size=1000, overlap=0)
    assert len(specs) >= 3
    assert "".join(spec.content for spec in specs) == text
    assert [s.char_start for s in specs] == [0, 1000, 2000]


def test_chunker_skips_whitespace_only_windows() -> None:
    text = "ab\n" + " " * 3000
    specs = chunk_text(text, PAGES, size=1000, overlap=0)
    assert len(specs) == 1
    assert specs[0].chunk_index == 0
    assert specs[0].content.startswith("ab")


def test_chunker_page_mapping() -> None:
    pages = [PageSpan(page=1, char_start=0, char_end=500), PageSpan(page=2, char_start=500, char_end=1200)]
    text = "a" * 1200
    specs = chunk_text(text, pages, size=600, overlap=0)
    assert [spec.page for spec in specs] == [1, 2]


def test_chunker_rejects_invalid_params() -> None:
    with pytest.raises(ValueError):
        chunk_text("abc", PAGES, size=100, overlap=100)
    with pytest.raises(ValueError):
        chunk_text("abc", PAGES, size=0, overlap=0)


def test_chunk_spec_span_hash_is_content_hash() -> None:
    import hashlib

    spec = ChunkSpec(chunk_index=0, content="hello", char_start=0, char_end=5, page=1)
    assert spec.span_sha256 == hashlib.sha256(b"hello").hexdigest()

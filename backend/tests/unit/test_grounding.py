# mypy: disable-error-code="arg-type,attr-defined,assignment,union-attr,index"
"""grounding 单测：引用必须能在真实 chunk 文本中定位（ADR-007）。"""

from __future__ import annotations

from jobfit.core.enums import DocumentKind
from jobfit.extraction.anchors import find_literal, ground_quotes
from support import chunks_from_windows


def test_exact_quote_is_located_with_correct_offsets() -> None:
    text = "教育经历\n2018-09 - 2022-06  南京大学  计算机科学与技术\n技能\nPython"
    chunks = chunks_from_windows(text, 40)
    grounding = ground_quotes(
        quotes=["南京大学"],
        chunks=chunks,
        parsed_document_id="p-1",
        doc_kind=DocumentKind.RESUME,
    )
    assert grounding.grounded
    anchor = grounding.anchors[0]
    assert text[anchor.char_start : anchor.char_end] == "南京大学"
    assert grounding.evidence_ids == [str(chunks[0].id)]


def test_whitespace_insensitive_match_maps_back_to_source() -> None:
    text = "2018-09 - 2022-06   南京大学  计算机科学与技术"
    chunks = chunks_from_windows(text, 100)
    grounding = ground_quotes(
        quotes=["2018-09 - 2022-06 南京大学"],
        chunks=chunks,
        parsed_document_id="p-1",
        doc_kind=DocumentKind.RESUME,
    )
    assert grounding.grounded
    span = text[grounding.anchors[0].char_start : grounding.anchors[0].char_end]
    assert span.startswith("2018-09 - 2022-06")
    assert span.endswith("南京大学")


def test_quote_across_multiple_chunks_locates_owner_chunk() -> None:
    text = "A" * 50 + "Python 熟练" + "B" * 50
    chunks = chunks_from_windows(text, 40)
    grounding = ground_quotes(
        quotes=["Python 熟练"],
        chunks=chunks,
        parsed_document_id="p-1",
        doc_kind=DocumentKind.RESUME,
    )
    assert grounding.grounded
    owner = next(c for c in chunks if str(c.id) == grounding.evidence_ids[0])
    span = owner.content[
        grounding.anchors[0].char_start - owner.char_start : grounding.anchors[0].char_end - owner.char_start
    ]
    assert span == "Python 熟练"


def test_missing_quote_is_reported_not_fabricated() -> None:
    text = "只有中文文本"
    grounding = ground_quotes(
        quotes=["nonexistent token"],
        chunks=chunks_from_windows(text, 100),
        parsed_document_id="p-1",
        doc_kind=DocumentKind.JD,
    )
    assert not grounding.grounded
    assert grounding.anchors == []
    assert grounding.evidence_ids == []
    assert grounding.unmatched_quotes == ["nonexistent token"]


def test_blank_quote_is_unmatched() -> None:
    grounding = ground_quotes(
        quotes=["   "],
        chunks=chunks_from_windows("abc", 100),
        parsed_document_id="p-1",
        doc_kind=DocumentKind.RESUME,
    )
    assert not grounding.grounded


def test_find_literal_returns_none_when_absent() -> None:
    chunks = chunks_from_windows("张三\nPython", 100)
    anchor, evidence_id = find_literal(
        literal="李四",
        chunks=chunks,
        parsed_document_id="p-1",
        doc_kind=DocumentKind.RESUME,
    )
    assert anchor is None
    assert evidence_id is None


def test_find_literal_returns_anchor_when_present() -> None:
    chunks = chunks_from_windows("张三\nPython", 100)
    anchor, evidence_id = find_literal(
        literal="张三",
        chunks=chunks,
        parsed_document_id="p-1",
        doc_kind=DocumentKind.RESUME,
    )
    assert anchor is not None
    assert evidence_id == str(chunks[0].id)

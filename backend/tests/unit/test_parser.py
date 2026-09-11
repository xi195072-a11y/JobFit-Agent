"""parser 单测：真实解析 PDF/DOCX/TXT，显式失败（不吞异常、不产生假结果）。"""

from __future__ import annotations

import io

import docx
import pytest

from jobfit.core.errors import ParseFailure
from jobfit.parsing import docx as docx_parser
from jobfit.parsing import pdf as pdf_parser
from jobfit.parsing import txt as txt_parser


def build_pdf_bytes(*, text: str | None) -> bytes:
    """手写一个最小合法 PDF（含精确 xref 偏移），用于真实文本层解析。"""
    if text is None:
        content = b""
    else:
        content = f"BT /F1 12 Tf 40 700 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
        ),
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_pos = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_pos}\n%%EOF\n"
    ).encode()
    return bytes(out)


def build_docx_bytes(paragraphs: list[str]) -> bytes:
    document = docx.Document()
    for line in paragraphs:
        document.add_paragraph(line)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


# ---------------------------------------------------------------- txt

def test_parse_txt_normalizes_and_maps_page() -> None:
    parsed = txt_parser.parse_txt("张三\r\n\r\n\r\n\r\nPython 熟练\n".encode())
    assert parsed.text == "张三\n\n\nPython 熟练"
    assert parsed.actual_kind == "txt"
    assert parsed.pages[0].char_start == 0
    assert parsed.pages[0].char_end == len(parsed.text)


def test_parse_txt_is_deterministic() -> None:
    data = "abc\n\n\n\n def \n".encode()
    first = txt_parser.parse_txt(data)
    second = txt_parser.parse_txt(data)
    assert first.text == second.text
    assert first.parser_meta == second.parser_meta


def test_parse_txt_invalid_encoding_fails_explicitly() -> None:
    with pytest.raises(ParseFailure):
        txt_parser.parse_txt(b"\xff\xfe\x00\x01")


def test_parse_txt_empty_fails_explicitly() -> None:
    with pytest.raises(ParseFailure):
        txt_parser.parse_txt(b"   \n\n  ")


# ---------------------------------------------------------------- pdf

def test_parse_pdf_text_layer() -> None:
    parsed = pdf_parser.parse_pdf(build_pdf_bytes(text="Hello JobFit"))
    assert "Hello JobFit" in parsed.text
    assert parsed.actual_kind == "pdf"
    assert parsed.parser_meta["page_count"] == 1
    assert parsed.pages[0].page == 1


def test_parse_pdf_without_text_layer_fails_explicitly() -> None:
    with pytest.raises(ParseFailure) as exc:
        pdf_parser.parse_pdf(build_pdf_bytes(text=None))
    assert "no text layer" in str(exc.value)


def test_parse_pdf_corrupt_bytes_fail_explicitly() -> None:
    with pytest.raises(ParseFailure):
        pdf_parser.parse_pdf(b"%PDF-1.4 broken")


# ---------------------------------------------------------------- docx

def test_parse_docx_paragraphs() -> None:
    parsed = docx_parser.parse_docx(build_docx_bytes(["张三", "", "Python 熟练"]))
    assert parsed.text == "张三\nPython 熟练"
    assert parsed.actual_kind == "docx"
    assert parsed.parser_meta["paragraph_count"] == 2


def test_parse_docx_invalid_package_fails_explicitly() -> None:
    with pytest.raises(ParseFailure):
        docx_parser.parse_docx(b"definitely not a docx")

"""DOCX parser（python-docx，只读段落文本；不执行宏、不解压外部实体）。"""

from __future__ import annotations

import io
import zipfile

import docx
from docx.opc.exceptions import PackageNotFoundError

from jobfit.core.errors import ParseFailure
from jobfit.parsing.base import PageSpan, ParsedText, normalize_text

ACTUAL_KIND = "docx"


def _docx_version() -> str:
    try:
        from importlib.metadata import version

        return version("python-docx")
    except Exception:  # noqa: BLE001
        return "unknown"


DOCX_LIB_VERSION = _docx_version()


def parse_docx(data: bytes) -> ParsedText:
    try:
        document = docx.Document(io.BytesIO(data))
    except (PackageNotFoundError, KeyError, ValueError, OSError, zipfile.BadZipFile) as exc:
        raise ParseFailure(f"docx open failed: {exc}") from exc

    paragraphs = [normalize_text(p.text) for p in document.paragraphs]
    kept = [p for p in paragraphs if p]
    text = "\n".join(kept)
    if not text.strip():
        raise ParseFailure("docx document has no extractable paragraph text")
    return ParsedText(
        text=text,
        pages=[PageSpan(page=1, char_start=0, char_end=len(text))],
        parser_meta={
            "kind": ACTUAL_KIND,
            "python_docx_version": DOCX_LIB_VERSION,
            "paragraph_count": len(kept),
        },
        actual_kind=ACTUAL_KIND,
    )

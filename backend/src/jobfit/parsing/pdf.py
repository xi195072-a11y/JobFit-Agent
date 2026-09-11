"""PDF parser（pypdf，文本层）。扫描件/无文本层 => 显式 ParseFailure（ADR-011）。"""

from __future__ import annotations

import io

import pypdf
import pypdf.errors

from jobfit.core.errors import ParseFailure
from jobfit.parsing.base import PageSpan, ParsedText, normalize_text

ACTUAL_KIND = "pdf"
PDF_LIB_VERSION = getattr(pypdf, "__version__", "unknown")


def parse_pdf(data: bytes) -> ParsedText:
    try:
        reader = pypdf.PdfReader(io.BytesIO(data), strict=False)
    except (pypdf.errors.PdfReadError, OSError, ValueError) as exc:
        raise ParseFailure(f"pdf open failed: {exc}") from exc

    chunks: list[str] = []
    pages: list[PageSpan] = []
    cursor = 0
    for index, page in enumerate(reader.pages, start=1):
        try:
            raw = page.extract_text() or ""
        except Exception as exc:  # noqa: BLE001 - pypdf 对损坏页会抛多种异常，统一显式失败
            raise ParseFailure(f"pdf page {index} extract failed: {exc}") from exc
        normalized = normalize_text(raw)
        if not normalized:
            # 空页保留占位，维持页号映射
            pages.append(PageSpan(page=index, char_start=cursor, char_end=cursor))
            continue
        if chunks:
            chunks.append("")  # 页间分隔行
            cursor += 1
        start = cursor
        chunks.append(normalized)
        cursor += len(normalized)
        pages.append(PageSpan(page=index, char_start=start, char_end=cursor))

    text = "\n".join(chunks)
    if not text.strip():
        raise ParseFailure(
            "pdf has no text layer (scanned/image-only PDF is out of MVP scope, ADR-011)"
        )
    return ParsedText(
        text=text,
        pages=pages,
        parser_meta={
            "kind": ACTUAL_KIND,
            "pypdf_version": PDF_LIB_VERSION,
            "page_count": len(reader.pages),
        },
        actual_kind=ACTUAL_KIND,
    )

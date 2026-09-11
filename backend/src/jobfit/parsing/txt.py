"""纯文本 parser：严格 UTF-8，解码失败显式报错（不猜测编码）。"""

from __future__ import annotations

from jobfit.core.errors import ParseFailure
from jobfit.parsing.base import PageSpan, ParsedText, normalize_text

ACTUAL_KIND = "txt"


def parse_txt(data: bytes) -> ParsedText:
    try:
        raw = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ParseFailure(f"txt decode failed (not valid UTF-8): {exc}") from exc
    text = normalize_text(raw)
    if not text.strip():
        raise ParseFailure("txt document has no extractable text")
    return ParsedText(
        text=text,
        pages=[PageSpan(page=1, char_start=0, char_end=len(text))],
        parser_meta={"kind": ACTUAL_KIND, "encoding": "utf-8"},
        actual_kind=ACTUAL_KIND,
    )

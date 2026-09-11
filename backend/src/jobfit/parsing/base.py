"""parser 抽象与 parse artifact 版本（ADR-011 / ADR-023）。

parse artifact version 必须同时覆盖：parser 代码版本、第三方库版本、解析参数、
以及 chunking 配置（chunks 是 parse artifact 的不可变子行）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

PARSER_CODE_VERSION = "p2.0.0"


@dataclass(frozen=True)
class PageSpan:
    page: int
    char_start: int
    char_end: int

    def as_dict(self) -> dict:
        return {"page": self.page, "char_start": self.char_start, "char_end": self.char_end}


@dataclass(frozen=True)
class ParsedText:
    """解析结果（规范化文本 + 页映射 + parser 元数据）。"""

    text: str
    pages: list[PageSpan]
    parser_meta: dict = field(default_factory=dict)
    actual_kind: str = "txt"


class DocumentParser(Protocol):
    actual_kind: str

    def parse(self, data: bytes) -> ParsedText: ...


def normalize_text(raw: str) -> str:
    """确定性规范化：统一换行、去除行尾空白、折叠 3+ 空行为 2 行、去首尾空行。

    不改变字符语义（不做大小写/全半角/翻译），保证同一输入 => 同一输出。
    """
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    out: list[str] = []
    blank_run = 0
    for line in lines:
        if line == "":
            blank_run += 1
            if blank_run > 2:
                continue
        else:
            blank_run = 0
        out.append(line)
    return "\n".join(out).strip("\n")

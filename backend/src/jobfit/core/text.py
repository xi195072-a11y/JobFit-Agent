"""通用文本归一化（确定性；供 matching 与 evidence 共用，避免层次倒置）。"""

from __future__ import annotations

import re
import unicodedata

_EDGE_PUNCT = "。，,、;；:：/\\|·•【】[]（）()<>《》\"'“”‘’!！?？*#"
_WS = re.compile(r"\s+")


def form_normalize(text: str) -> str:
    """形式归一（确定性、无语言模型参与）：NFKC → casefold → 空白折叠 → 去首尾标点。"""
    normalized = unicodedata.normalize("NFKC", text).casefold()
    normalized = _WS.sub(" ", normalized).strip()
    return normalized.strip(_EDGE_PUNCT).strip()

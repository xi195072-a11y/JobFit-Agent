"""确定性 lexical 检索打分（Phase 3 §15/§16）。

- 分词为纯函数：ASCII/数字词（小写）+ CJK 单字与二元组；无第三方分词器依赖。
- 打分公式（无魔法常量，只有对数）：
      score = Σ_{t ∈ query terms} log(1 + tf_t) / (1 + log(1 + doc_token_count))
  tf_t 为 t 在 chunk 中的出现次数；分母惩罚过长 chunk。
- 结果与 tie-break 见 evidence/retrieval.py（同一 chunk 的分数在相同输入下逐位一致）。
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter

_WORD = re.compile(r"[a-z0-9][a-z0-9_.+#-]*")
_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


def tokenize(text: str) -> list[str]:
    """确定性分词（同一文本 => 同一 token 序列）。"""
    normalized = unicodedata.normalize("NFKC", text).casefold()
    tokens: list[str] = []
    for chunk in _split_cjk_runs(normalized):
        if chunk.is_cjk:
            tokens.extend(_cjk_tokens(chunk.text))
        else:
            tokens.extend(_WORD.findall(chunk.text))
    return tokens


class _Run:
    __slots__ = ("text", "is_cjk")

    def __init__(self, text: str, is_cjk: bool) -> None:
        self.text = text
        self.is_cjk = is_cjk


def _split_cjk_runs(text: str) -> list[_Run]:
    runs: list[_Run] = []
    current: list[str] = []
    current_is_cjk = False
    for char in text:
        is_cjk = bool(_CJK.match(char))
        if current and is_cjk != current_is_cjk:
            runs.append(_Run("".join(current), current_is_cjk))
            current = []
        current.append(char)
        current_is_cjk = is_cjk
    if current:
        runs.append(_Run("".join(current), current_is_cjk))
    return runs


def _cjk_tokens(run: str) -> list[str]:
    """CJK：单字 + 相邻二元组（覆盖"北京"这类双字词，无需词典）。"""
    chars = [c for c in run if _CJK.match(c)]
    if not chars:
        return []
    if len(chars) == 1:
        return list(chars)
    return chars + [chars[i] + chars[i + 1] for i in range(len(chars) - 1)]


def lexical_score(query: str, text: str) -> float:
    """query 与 text 的确定性 lexical 相关度（无匹配 => 0.0）。"""
    query_terms = sorted(set(tokenize(query)))
    if not query_terms:
        return 0.0
    counts = Counter(tokenize(text))
    if not counts:
        return 0.0
    numerator = sum(math.log(1 + counts[term]) for term in query_terms if counts[term] > 0)
    if numerator == 0.0:
        return 0.0
    denominator = 1.0 + math.log(1 + sum(counts.values()))
    return round(numerator / denominator, 12)

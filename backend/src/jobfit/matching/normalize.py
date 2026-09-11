"""确定性归一化（Phase 3 §19）。

原则：
1. 先做**形式归一**（NFKC / 大小写 / 空白 / 首尾标点），它是所有比较的基础；
2. 再做**显式词表**查表（aliases / degree_levels / levels）；
3. 查不到就返回 None（未归一），绝不做模糊匹配或语义近邻猜测——
   "Python → PyTorch" 这类未声明的映射在结构上不可能发生（§19/§36）；
4. 任一侧无法归一 => 调用方必须产出 UNKNOWN，而不是 FALSE（§10）。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from jobfit.core.text import form_normalize
from jobfit.matching.rules import RuleConfig

__all__ = [
    "degree_level",
    "form_normalize",
    "language_level",
    "normalize_location",
    "normalize_skill",
    "parse_language_entry",
    "skill_category",
    "skill_compare_key",
]


def _reverse_alias(aliases: Mapping[str, Any]) -> dict[str, str]:
    """{形式归一后的别名: canonical}；同名冲突时按 canonical 排序取最小（确定性）。"""
    reverse: dict[str, str] = {}
    for canonical in sorted(aliases):
        for alias in aliases[canonical] or []:
            key = form_normalize(str(alias))
            if key and key not in reverse:
                reverse[key] = str(canonical)
    return reverse


def normalize_skill(raw: str, cfg: RuleConfig) -> str | None:
    """返回 canonical 技能名；未在词表中声明 => None。"""
    if not raw or not raw.strip():
        return None
    return _reverse_alias(cfg.skill_aliases).get(form_normalize(raw))


def skill_compare_key(raw: str, cfg: RuleConfig) -> str:
    """技能比较键：已声明取 canonical，未声明退化为形式归一字符串（仍确定性、可解释）。"""
    return normalize_skill(raw, cfg) or form_normalize(raw)


def skill_category(raw: str, cfg: RuleConfig) -> str | None:
    canonical = normalize_skill(raw, cfg)
    if canonical is None:
        return None
    value = cfg.skill_categories.get(canonical)
    return str(value) if value is not None else None


def degree_level(raw: str | None, cfg: RuleConfig) -> int | None:
    """学历 → 有序等级（education_rules.yaml 的 degree_levels 是唯一真源）。

    精确匹配优先；否则取"被包含的最长已声明键"（如 "工学硕士" → 硕士）；
    仍未命中 => None（UNKNOWN，不猜测）。
    """
    if not raw or not raw.strip():
        return None
    text = form_normalize(raw)
    pairs = [
        (form_normalize(str(key)), value)
        for key, value in cfg.degree_levels.items()
        if form_normalize(str(key))
    ]
    for key, value in pairs:
        if key == text:
            return None if value is None else int(value)
    contained = sorted(
        ((key, value) for key, value in pairs if key in text),
        key=lambda item: (-len(item[0]), item[0]),
    )
    if not contained:
        return None
    value = contained[0][1]
    return None if value is None else int(value)


def normalize_location(raw: str | None, cfg: RuleConfig) -> str | None:
    """地点 → canonical；未声明 => None（无法比较 => UNKNOWN）。"""
    if not raw or not raw.strip():
        return None
    return _reverse_alias(cfg.location_aliases).get(form_normalize(raw))


def parse_language_entry(text: str, cfg: RuleConfig) -> tuple[str, str | None] | None:
    """把 "英语 CET-6" 拆成 (语言, 等级标签)；语言必须已在 language_rules.yaml 声明。"""
    if not text or not text.strip():
        return None
    normalized = form_normalize(text)
    for name in cfg.language_names:
        key = form_normalize(name)
        if normalized == key:
            return name, None
        if normalized.startswith(key):
            remainder = normalized[len(key) :].strip(" -_/")
            return name, remainder.upper() if remainder else None
    return None


def language_level(language: str, label: str | None, cfg: RuleConfig) -> int | None:
    """(语言, 等级标签) → 有序分值；未声明 => None（不猜测 CEFR/TOEFL 换算）。"""
    if not label:
        return None
    table = cfg.language_levels.get(language)
    if not isinstance(table, Mapping):
        return None
    for declared, value in table.items():
        if str(declared).upper() == label.upper():
            return None if value is None else int(value)
    return None

"""unit: 确定性归一化（Phase 3 §19）。"""

from __future__ import annotations

import pytest

from jobfit.core.text import form_normalize
from jobfit.matching.normalize import (
    degree_level,
    language_level,
    normalize_location,
    normalize_skill,
    parse_language_entry,
    skill_compare_key,
)


def test_form_normalize_is_stable_and_unicode_aware() -> None:
    assert form_normalize("  Python  ") == "python"
    assert form_normalize("Ｐｙｔｈｏｎ") == "python"  # NFKC 全角
    assert form_normalize("Python，") == "python"
    assert form_normalize("Fast\tAPI") == "fast api"


def test_skill_alias_normalization(rule_config) -> None:
    assert normalize_skill("Python", rule_config) == "python"
    assert normalize_skill("python3", rule_config) == "python"
    assert normalize_skill("Python 3", rule_config) == "python"
    assert normalize_skill("Python Programming", rule_config) == "python"
    assert normalize_skill("Postgres", rule_config) == "postgresql"
    assert normalize_skill("K8s", rule_config) == "kubernetes"


def test_skill_not_in_dictionary_is_not_guessed(rule_config) -> None:
    """未声明的近义关系一律不成立（§19）。"""
    assert normalize_skill("Golang", rule_config) is None
    # Python 绝不映射到 PyTorch（§36 的反例）
    assert normalize_skill("Python", rule_config) != normalize_skill("PyTorch", rule_config)
    assert skill_compare_key("PyTorch", rule_config) == "pytorch"


def test_degree_level_mapping_and_unknown(rule_config) -> None:
    assert degree_level("本科", rule_config) == 2
    assert degree_level("硕士", rule_config) == 3
    assert degree_level("工学硕士", rule_config) == 3  # 最长已声明键被包含
    assert degree_level("大专", rule_config) == 1
    assert degree_level(None, rule_config) is None
    assert degree_level("技校", rule_config) is None  # 未声明 => UNKNOWN，不猜


def test_location_normalization_requires_declared_city(rule_config) -> None:
    assert normalize_location("深圳", rule_config) == "深圳"
    assert normalize_location("深圳市", rule_config) == "深圳"
    assert normalize_location("北京", rule_config) == "北京"
    assert normalize_location("火星", rule_config) is None
    assert normalize_location(None, rule_config) is None


def test_language_parsing_and_levels(rule_config) -> None:
    assert parse_language_entry("英语 CET-6", rule_config) == ("英语", "CET-6")
    assert parse_language_entry("日语 N1", rule_config) == ("日语", "N1")
    assert parse_language_entry("Klingon", rule_config) is None
    assert language_level("英语", "CET-6", rule_config) == 2
    assert language_level("英语", "CET-4", rule_config) == 1
    assert language_level("英语", "GRE", rule_config) is None
    assert language_level("英语", None, rule_config) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("python", "python"), ("postgresql", "postgresql"), ("docker", "docker")],
)
def test_compare_key_is_stable(rule_config, raw: str, expected: str) -> None:
    assert skill_compare_key(raw, rule_config) == expected

"""版本化规则访问层（ADR-019 / reproducibility.md §4）。

**执行期只读 `analyses.config_snapshot`**，绝不读取"当前磁盘上的配置"——
否则历史 analysis 会随配置漂移而改变结论，破坏可复现性。
快照缺失时显式失败，不做隐式回退。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from jobfit.core.errors import ValidationFailed

UNKNOWN_POLICY_EXCLUDE_AND_RENORMALIZE = "exclude_and_renormalize"


@dataclass(frozen=True)
class RuleConfig:
    """一次 analysis 固化的规则视图（不可变）。"""

    ruleset_version: str
    scoring_version: str
    scoring: Mapping[str, Any]
    education: Mapping[str, Any]
    location: Mapping[str, Any]
    language: Mapping[str, Any]
    skills: Mapping[str, Any]
    constraints: Mapping[str, Any]
    retrieval: Mapping[str, Any]

    # ------------------------------------------------------------ 构造
    @classmethod
    def from_snapshot(
        cls,
        snapshot: Mapping[str, Any] | None,
        *,
        ruleset_version: str | None,
        scoring_version: str | None,
    ) -> RuleConfig:
        if not snapshot:
            raise ValidationFailed(
                "analysis.config_snapshot is missing: 执行必须使用入队时固化的规则快照"
                "（reproducibility.md §4），禁止回退到磁盘上的当前配置"
            )
        try:
            scoring = snapshot["scoring"]
            rules = snapshot["rules"]
        except (KeyError, TypeError) as exc:  # pragma: no cover - 快照结构由 loader 保证
            raise ValidationFailed(f"malformed config_snapshot: {exc}") from exc
        return cls(
            ruleset_version=ruleset_version or str(snapshot.get("schema", {}).get("ruleset_version", "")),
            scoring_version=scoring_version or str(snapshot.get("schema", {}).get("scoring_version", "")),
            scoring=scoring,
            education=rules["education_rules.yaml"],
            location=rules["location_rules.yaml"],
            language=rules["language_rules.yaml"],
            skills=rules["skills.yaml"],
            constraints=rules["constraint_rules.yaml"],
            retrieval=rules["retrieval.yaml"],
        )

    # ------------------------------------------------------------ scoring
    def section_weight(self, section: str) -> float:
        entry = self.scoring.get("sections", {}).get(section)
        if not isinstance(entry, Mapping) or "weight" not in entry:
            raise ValidationFailed(f"scoring.yaml 未声明 section weight: {section}")
        return float(entry["weight"])

    def score_sections(self) -> list[str]:
        """参与加权的 section（按声明顺序，排除 veto 门禁）。"""
        out: list[str] = []
        for name, entry in self.scoring.get("sections", {}).items():
            if isinstance(entry, Mapping) and entry.get("kind") == "veto":
                continue
            out.append(str(name))
        return out

    def credit(self, key: str) -> float:
        credits = self.scoring.get("credits", {})
        if key not in credits:
            raise ValidationFailed(f"scoring.yaml 未声明 credits.{key}")
        return float(credits[key])

    @property
    def claimed_only_penalty(self) -> float:
        return float(self.scoring.get("flags", {}).get("claimed_only_penalty", 0.0))

    @property
    def scale(self) -> float:
        return float(self.scoring.get("scale", 100.0))

    def unknown_policy(self) -> Mapping[str, Any]:
        policy = self.scoring.get("unknown_policy")
        if not isinstance(policy, Mapping) or "mode" not in policy:
            raise ValidationFailed("scoring.yaml 必须显式声明 unknown_policy.mode（§27）")
        return policy

    def gate_policy(self) -> Mapping[str, Any]:
        gate = self.scoring.get("gate")
        if not isinstance(gate, Mapping):
            raise ValidationFailed("scoring.yaml 必须显式声明 gate 策略")
        return gate

    # ------------------------------------------------------------ constraint operators
    def operator_rule(self, req_type: str) -> Mapping[str, Any] | None:
        operators = self.constraints.get("operators", {})
        entry = operators.get(req_type)
        return entry if isinstance(entry, Mapping) else None

    def unsupported_types(self) -> set[str]:
        return {str(item) for item in self.constraints.get("unsupported", [])}

    # ------------------------------------------------------------ retrieval
    @property
    def min_similarity(self) -> float:
        return float(self.retrieval.get("vector", {}).get("min_similarity", 0.0))

    @property
    def min_lexical_score(self) -> float:
        return float(self.retrieval.get("lexical", {}).get("min_score", 0.0))

    @property
    def retrieval_top_k(self) -> int:
        return int(self.retrieval.get("default_top_k", 5))

    @property
    def retrieval_max_top_k(self) -> int:
        return int(self.retrieval.get("max_top_k", 50))

    # ------------------------------------------------------------ 词表
    @property
    def degree_levels(self) -> Mapping[str, Any]:
        return self.education.get("degree_levels", {})

    @property
    def skill_aliases(self) -> Mapping[str, Any]:
        return self.skills.get("aliases", {})

    @property
    def skill_categories(self) -> Mapping[str, Any]:
        return self.skills.get("categories", {})

    @property
    def location_aliases(self) -> Mapping[str, Any]:
        return self.location.get("aliases", {})

    @property
    def language_levels(self) -> Mapping[str, Any]:
        return self.language.get("levels", {})

    @property
    def language_names(self) -> list[str]:
        """已声明的语言名（用于从 "英语 CET-6" 这类文本中切分语言与等级）。"""
        return sorted((str(name) for name in self.language_levels), key=len, reverse=True)

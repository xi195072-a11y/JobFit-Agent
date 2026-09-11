"""版本化 YAML 配置加载器（ADR-019）。

职责：
- 读取 config/scoring|skills|education_rules|location_rules|language_rules.yaml
- 校验每份文件声明 version
- 计算 ruleset_version / scoring_version / prompt_version
- 生成 config_snapshot（供 analyses.config_snapshot 固化）
- 生成 PipelineVersions（入队时快照，执行时只使用快照）
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

from jobfit.config.settings import Settings
from jobfit.core.errors import ConfigurationError
from jobfit.core.schemas import PipelineVersions

RULES_FILES = (
    "education_rules.yaml",
    "location_rules.yaml",
    "language_rules.yaml",
    "skills.yaml",
    "constraint_rules.yaml",
    "retrieval.yaml",
)


def _canonical_dump(obj: object) -> str:
    """语义内容 canonical 序列化（忽略 key 顺序差异，注释/空白不参与）。"""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def load_yaml(path: Path) -> dict:
    if not path.is_file():
        raise ConfigurationError(f"missing config file: {path}")
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ConfigurationError(f"config must be a mapping: {path}")
    if "version" not in data:
        raise ConfigurationError(f"config must declare a version: {path}")
    return data


class ConfigLoader:
    def __init__(self, config_dir: Path) -> None:
        self.config_dir = Path(config_dir)

    def scoring(self) -> dict:
        return load_yaml(self.config_dir / "scoring.yaml")

    def ruleset(self) -> dict[str, dict]:
        return {name: load_yaml(self.config_dir / name) for name in RULES_FILES}

    def prompt_files(self) -> list[Path]:
        prompts = self.config_dir / "prompts"
        if not prompts.is_dir():
            return []
        return sorted(prompts.glob("*.j2"))

    # ------------------------------------------------------------- 版本

    def scoring_version(self) -> str:
        return f"s:{self.scoring()['version']}"

    def ruleset_version(self) -> str:
        parts = "".join(f"{name}={self.ruleset()[name]['version']}|" for name in RULES_FILES)
        digest = hashlib.sha256(parts.encode("utf-8")).hexdigest()[:8]
        return f"r:{digest}"

    def prompt_version(self) -> str | None:
        files = self.prompt_files()
        if not files:
            return None
        digest = hashlib.sha256()
        for path in files:
            digest.update(path.name.encode("utf-8"))
            digest.update(path.read_bytes())
        return f"h:{digest.hexdigest()[:16]}"

    # ------------------------------------------------------------- snapshot

    def config_snapshot(self) -> dict:
        rules = self.ruleset()
        return {
            "scoring": self.scoring(),
            "rules": rules,
            "schema": {"ruleset_version": self.ruleset_version(), "scoring_version": self.scoring_version()},
            "canonical": _canonical_dump({"scoring": self.scoring(), "rules": rules}),
        }

    def resolve_versions(
        self,
        *,
        pipeline_version: str,
        llm_model: str,
        extraction_schema_version: str,
        embedding_model: str | None = None,
    ) -> PipelineVersions:
        """入队时固化一次版本快照；执行/重放只使用快照。"""
        return PipelineVersions(
            pipeline_version=pipeline_version,
            extraction_schema_version=extraction_schema_version,
            prompt_version=self.prompt_version(),
            ruleset_version=self.ruleset_version(),
            scoring_version=self.scoring_version(),
            llm_model=llm_model,
            embedding_model=embedding_model,
            config_snapshot=self.config_snapshot(),
        )


def get_loader(settings: Settings) -> ConfigLoader:
    return ConfigLoader(settings.config_dir)

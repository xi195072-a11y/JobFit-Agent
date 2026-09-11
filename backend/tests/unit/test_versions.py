"""PipelineVersions / config loader（ADR-019）。

config dir 来自 conftest：backend/config（版本化 YAML）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jobfit.config.loader import ConfigLoader
from jobfit.core.errors import ConfigurationError


@pytest.fixture
def loader(config_dir: Path) -> ConfigLoader:
    return ConfigLoader(config_dir)


def test_scoring_and_ruleset_load(loader: ConfigLoader) -> None:
    scoring = loader.scoring()
    assert scoring["version"] == "0.2.0"
    assert set(loader.ruleset().keys()) == {
        "education_rules.yaml",
        "location_rules.yaml",
        "language_rules.yaml",
        "skills.yaml",
        "constraint_rules.yaml",
        "retrieval.yaml",
    }


def test_versions_deterministic(loader: ConfigLoader) -> None:
    sv1, sv2 = loader.scoring_version(), loader.scoring_version()
    rv1, rv2 = loader.ruleset_version(), loader.ruleset_version()
    assert sv1 == sv2 == "s:0.2.0"
    assert rv1 == rv2
    assert rv1.startswith("r:") and len(rv1.split(":")[1]) == 8


def test_prompt_version_hashing(loader: ConfigLoader) -> None:
    pv = loader.prompt_version()
    assert pv is not None and pv.startswith("h:")


def test_config_snapshot_shape(loader: ConfigLoader) -> None:
    snap = loader.config_snapshot()
    assert snap["scoring"]["version"] == "0.2.0"
    assert "canonical" in snap
    assert snap["schema"]["scoring_version"] == "s:0.2.0"
    # Phase 3：UNKNOWN 策略与门禁策略必须是显式配置（§26/§27），不得由代码隐式决定。
    assert snap["scoring"]["unknown_policy"]["mode"] == "exclude_and_renormalize"
    assert "gate" in snap["scoring"]


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError):
        ConfigLoader(tmp_path).scoring()


def test_config_without_version_rejected(tmp_path: Path) -> None:
    bad = tmp_path / "scoring.yaml"
    bad.write_text("sections: {}\n", encoding="utf-8")
    with pytest.raises(ConfigurationError):
        ConfigLoader(tmp_path).scoring()


def test_resolve_versions(loader: ConfigLoader) -> None:
    v = loader.resolve_versions(
        pipeline_version="2026.09.09-1",
        llm_model="deepseek-chat",
        extraction_schema_version="resume.v1|jd.v1",
    )
    assert v.pipeline_version == "2026.09.09-1"
    assert v.llm_model == "deepseek-chat"
    assert v.config_snapshot["scoring"]["version"] == "0.2.0"

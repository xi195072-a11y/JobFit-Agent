"""prompt 资产版本化单测（ADR-019：prompt_version = 模板内容哈希）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from jobfit.config.prompts import JD_PROMPT, RESUME_PROMPT, PromptRegistry
from jobfit.core.errors import ConfigurationError


def test_versions_are_deterministic(config_dir: Path) -> None:
    first = PromptRegistry(config_dir / "prompts")
    second = PromptRegistry(config_dir / "prompts")
    assert first.versions() == second.versions()
    assert first.combined_version() == second.combined_version()
    assert first.combined_version().startswith("h:")


def test_prompt_contains_required_guardrails(config_dir: Path) -> None:
    registry = PromptRegistry(config_dir / "prompts")
    resume_text = registry.get(RESUME_PROMPT).text.lower()
    jd_text = registry.get(JD_PROMPT).text.lower()
    for needle in ("only", "never infer", "{{chunks}}"):
        assert needle in resume_text
    for needle in ("never invent", "{{chunks}}", "unknown"):
        assert needle in jd_text


def test_version_changes_when_template_content_changes(config_dir: Path, tmp_path: Path) -> None:
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    for name in (RESUME_PROMPT, JD_PROMPT):
        (prompts / name).write_text(
            (config_dir / "prompts" / name).read_text(encoding="utf-8"), encoding="utf-8"
        )
    baseline = PromptRegistry(prompts).combined_version()

    target = prompts / RESUME_PROMPT
    target.write_text(target.read_text(encoding="utf-8") + "\n# extra rule\n", encoding="utf-8")
    assert PromptRegistry(prompts).combined_version() != baseline


def test_missing_placeholder_rejected(config_dir: Path, tmp_path: Path) -> None:
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / RESUME_PROMPT).write_text("no placeholder here", encoding="utf-8")
    (prompts / JD_PROMPT).write_text("{{chunks}}", encoding="utf-8")
    with pytest.raises(ConfigurationError):
        PromptRegistry(prompts).get(RESUME_PROMPT)


def test_missing_template_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError):
        PromptRegistry(tmp_path).get(RESUME_PROMPT)


def test_render_replaces_placeholder(config_dir: Path) -> None:
    registry = PromptRegistry(config_dir / "prompts")
    rendered = registry.get(RESUME_PROMPT).render(chunks="[chunk:0] hello")
    assert "[chunk:0] hello" in rendered

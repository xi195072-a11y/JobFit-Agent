# mypy: disable-error-code="arg-type,attr-defined,assignment,union-attr"
"""unit: Phase 4 确定性 fingerprint（§21/§25/§37）——不依赖 DB。

同一输入 => 同一 fingerprint；任一关键配置/结果变化 => 新 fingerprint。
"""

from __future__ import annotations

import uuid

from jobfit.critique.fingerprint import critique_fingerprint
from jobfit.db import models
from jobfit.reports.fingerprint import REPORT_BUILDER_VERSION, report_fingerprint

ANALYSIS = uuid.uuid4()
CONFIG = {"ruleset_version": "r:1", "scoring_version": "s:0.2.0"}


def test_critique_fingerprint_deterministic() -> None:
    a = critique_fingerprint(
        analysis_id=str(ANALYSIS),
        prompt_version="h:abc",
        provider="deepseek",
        model="deepseek-chat",
        config_snapshot=CONFIG,
    )
    b = critique_fingerprint(
        analysis_id=str(ANALYSIS),
        prompt_version="h:abc",
        provider="deepseek",
        model="deepseek-chat",
        config_snapshot=CONFIG,
    )
    assert a == b
    assert a.startswith("cr:")


def test_critique_fingerprint_changes_on_any_input() -> None:
    base = dict(
        analysis_id=str(ANALYSIS),
        prompt_version="h:abc",
        provider="deepseek",
        model="deepseek-chat",
        config_snapshot=CONFIG,
    )
    assert critique_fingerprint(**base) != critique_fingerprint(**{**base, "analysis_id": str(uuid.uuid4())})
    assert critique_fingerprint(**base) != critique_fingerprint(**{**base, "prompt_version": "h:zzz"})
    assert critique_fingerprint(**base) != critique_fingerprint(**{**base, "provider": "openai"})
    assert critique_fingerprint(**base) != critique_fingerprint(**{**base, "model": "gpt-4"})
    assert critique_fingerprint(**base) != critique_fingerprint(
        **{**base, "config_snapshot": {"ruleset_version": "r:2", "scoring_version": "s:0.2.0"}}
    )


def _constraints() -> list[models.HardConstraintResult]:
    return [
        models.HardConstraintResult(
            requirement_id=uuid.uuid4(),
            result="MET",
            basis="deterministic",
            ruleset_version="r:1",
            reason_code="DEGREE_AT_LEAST_MET",
        ),
        models.HardConstraintResult(
            requirement_id=uuid.uuid4(),
            result="UNKNOWN",
            basis="deterministic",
            ruleset_version="r:1",
            reason_code="DEGREE_UNKNOWN",
        ),
    ]


def _skills() -> list[models.SkillMatchResult]:
    return [
        models.SkillMatchResult(
            jd_requirement_id=uuid.uuid4(),
            status="matched",
            ruleset_version="r:1",
            score_contribution=40.0,
        )
    ]


def _score() -> models.ScoreSnapshot:
    return models.ScoreSnapshot(
        analysis_id=ANALYSIS,
        kind="base",
        total=82.5,
        per_section=[],
        flags=["GATE:PASS"],
        scoring_version="s:0.2.0",
    )


def test_report_fingerprint_deterministic_and_sensitive() -> None:
    constraints = _constraints()
    skills = _skills()
    score = _score()
    fp1 = report_fingerprint(
        analysis_id=ANALYSIS,
        constraints=constraints,
        skill_matches=skills,
        score=score,
        critique_fingerprint="cr:abc",
    )
    fp2 = report_fingerprint(
        analysis_id=ANALYSIS,
        constraints=constraints,
        skill_matches=skills,
        score=score,
        critique_fingerprint="cr:abc",
    )
    assert fp1 == fp2
    assert fp1.startswith("rp:")

    # 任一确定性结果变化 => 新 fingerprint（保持 requirement_id 不变，证明 result 敏感）
    different_constraint = [
        models.HardConstraintResult(
            requirement_id=constraints[0].requirement_id,
            result="NOT_MET",
            basis=constraints[0].basis,
            ruleset_version=constraints[0].ruleset_version,
            reason_code="DEGREE_BELOW_REQUIRED",
        ),
        constraints[1],
    ]
    assert report_fingerprint(
        analysis_id=ANALYSIS,
        constraints=different_constraint,
        skill_matches=skills,
        score=score,
        critique_fingerprint="cr:abc",
    ) != fp1

    different_score = _score()
    different_score.total = 60.0
    assert report_fingerprint(
        analysis_id=ANALYSIS,
        constraints=constraints,
        skill_matches=skills,
        score=different_score,
        critique_fingerprint="cr:abc",
    ) != fp1

    assert report_fingerprint(
        analysis_id=ANALYSIS,
        constraints=constraints,
        skill_matches=skills,
        score=score,
        critique_fingerprint="cr:xyz",
    ) != fp1


def test_report_builder_version_is_stable_singleton() -> None:
    from jobfit.reports.builder import REPORT_BUILDER_VERSION as BUILDER_VERSION

    assert BUILDER_VERSION == REPORT_BUILDER_VERSION
    assert REPORT_BUILDER_VERSION == "report.builder.v1"

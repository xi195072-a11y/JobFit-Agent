"""Report artifact 确定性 fingerprint（Phase 4 §25/§37）。

固化：analysis identity + report builder version + 确定性结果快照（constraints /
skill matches / score）+ critique fingerprint。任一关键输入变化 => 新 fingerprint。

canonical serialization：stable ordering + stable encoding，SHA-256。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from jobfit.db import models

# report builder 版本常量：定义于此以打破 builder <-> fingerprint 循环导入，
# builder.py 从这里导入（同一常量，单一事实来源）。
REPORT_BUILDER_VERSION = "report.builder.v1"


def _hash_hex(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return f"rp:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:32]}"


def report_fingerprint(
    *,
    analysis_id: uuid.UUID,
    constraints: list[models.HardConstraintResult],
    skill_matches: list[models.SkillMatchResult],
    score: models.ScoreSnapshot,
    critique_fingerprint: str | None,
) -> str:
    """确定性报告指纹：只依赖 DB 持久化的结果与配置，不含任何自由文本。"""
    constraint_rows = [
        {
            "requirement_id": str(row.requirement_id),
            "result": row.result,
            "basis": row.basis,
            "ruleset_version": row.ruleset_version,
            "reason_code": row.reason_code,
        }
        for row in sorted(constraints, key=lambda r: str(r.requirement_id))
    ]
    skill_rows = [
        {
            "jd_requirement_id": str(row.jd_requirement_id),
            "status": row.status,
            "ruleset_version": row.ruleset_version,
            "score_contribution": row.score_contribution,
        }
        for row in sorted(skill_matches, key=lambda r: str(r.jd_requirement_id))
    ]
    payload: dict[str, Any] = {
        "analysis_id": str(analysis_id),
        "builder_version": REPORT_BUILDER_VERSION,
        "constraints": constraint_rows,
        "skill_matches": skill_rows,
        "score": {
            "total": score.total,
            "kind": score.kind,
            "scoring_version": score.scoring_version,
            "flags": list(score.flags or []),
            "per_section": score.per_section,
        },
        "critique_fingerprint": critique_fingerprint,
    }
    return _hash_hex(payload)


__all__ = ["report_fingerprint"]

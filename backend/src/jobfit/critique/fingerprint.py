"""Critique artifact 确定性 fingerprint（Phase 4 §21）。

同一输入（analysis identity + critique prompt version + schema version + provider +
model + 相关 config）=> 同一 fingerprint；任一配置变化 => 新 fingerprint。
canonical serialization：stable ordering + stable encoding，SHA-256。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from jobfit.critique.schema import SCHEMA_VERSION


def critique_fingerprint(
    *,
    analysis_id: str,
    prompt_version: str,
    schema_version: str = SCHEMA_VERSION,
    provider: str,
    model: str,
    config_snapshot: dict[str, Any] | None = None,
) -> str:
    """critiques.fingerprint：幂等 identity 的输入部分（与 DB UNIQUE 配合）。

    config_snapshot 只参与 hash（它已是 stable-serialized JSON），不复制进 artifact。
    """
    payload: dict[str, Any] = {
        "analysis_id": str(analysis_id),
        "prompt_version": prompt_version,
        "schema_version": schema_version,
        "provider": provider,
        "model": model,
    }
    if config_snapshot is not None:
        payload["config"] = config_snapshot
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return f"cr:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:32]}"

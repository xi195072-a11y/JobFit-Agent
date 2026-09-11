"""确定性 id / 摘要工具（canonical JSON + SHA-256）。

被用于两类不可变身份：
- extraction artifact fingerprint（ADR-026，额外含 parsed_document_id 作审计超集）；
- analysis 幂等 identity（Phase 3 §29）。

同一实现保证两处 canonical 序列化口径一致：key 排序、紧凑分隔符、UTF-8 保真、非 JSON 类型用 str。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(payload: Any) -> str:
    """确定性 JSON：key 排序、紧凑分隔符、UTF-8 保真、日期等用 str 兜底。"""
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def fingerprint(payload: dict[str, Any]) -> str:
    return sha256_hex(canonical_json(payload))


def analysis_identity_key(
    *,
    resume_profile_id: str | None,
    jd_profile_id: str | None,
    resume_document_id: str,
    jd_document_id: str,
    pipeline_version: str,
    extraction_schema_version: str,
    prompt_version: str | None,
    ruleset_version: str | None,
    scoring_version: str | None,
    embedding_model: str | None,
) -> str:
    """analysis 幂等 identity（§29）。

    已绑定 profile 时以 profile 为准（结果只取决于这两个不可变 artifact + 版本集）；
    未绑定时退化为 document + 版本集（profile 尚未产出，此时身份由入队版本决定）。
    """
    return "an:" + fingerprint(
        {
            "kind": "analysis",
            "resume": str(resume_profile_id or resume_document_id),
            "jd": str(jd_profile_id or jd_document_id),
            "pipeline_version": pipeline_version,
            "extraction_schema_version": extraction_schema_version,
            "prompt_version": prompt_version,
            "ruleset_version": ruleset_version,
            "scoring_version": scoring_version,
            "embedding_model": embedding_model,
        }
    )

"""Deterministic artifact fingerprint（ADR-019 / 需求 15）。

同一组输入 => 同一 fingerprint；任何影响输出的配置变化 => 新 fingerprint。
fingerprint 绝不会是随机 UUID（UUID 只作 primary key）。
"""

from __future__ import annotations

from jobfit.core.ids import canonical_json, fingerprint, sha256_hex

__all__ = [
    "canonical_json",
    "jd_profile_fingerprint",
    "resume_profile_fingerprint",
    "sha256_hex",
]


def _fingerprint(payload: dict) -> str:
    return fingerprint(payload)


def resume_profile_fingerprint(
    *,
    document_id: str,
    parsed_document_id: str,
    pipeline_version: str,
    extraction_schema_version: str,
    prompt_version: str,
    llm_model: str,
) -> str:
    return _fingerprint(
        {
            "artifact": "resume_profile",
            "document_id": document_id,
            "parsed_document_id": parsed_document_id,
            "pipeline_version": pipeline_version,
            "extraction_schema_version": extraction_schema_version,
            "prompt_version": prompt_version,
            "llm_model": llm_model,
        }
    )


def jd_profile_fingerprint(
    *,
    document_id: str,
    parsed_document_id: str,
    pipeline_version: str,
    extraction_schema_version: str,
    prompt_version: str,
    llm_model: str,
) -> str:
    return _fingerprint(
        {
            "artifact": "jd_profile",
            "document_id": document_id,
            "parsed_document_id": parsed_document_id,
            "pipeline_version": pipeline_version,
            "extraction_schema_version": extraction_schema_version,
            "prompt_version": prompt_version,
            "llm_model": llm_model,
        }
    )

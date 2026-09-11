"""fingerprint 单测：canonical JSON 稳定性 + 版本敏感（需求 15）。"""

from __future__ import annotations

import json

from jobfit.extraction.fingerprints import (
    canonical_json,
    jd_profile_fingerprint,
    resume_profile_fingerprint,
    sha256_hex,
)


def test_canonical_json_is_key_order_independent() -> None:
    assert canonical_json({"b": 1, "a": [2, 3]}) == canonical_json({"a": [2, 3], "b": 1})
    assert canonical_json({"a": "中文"}) == json.dumps(
        {"a": "中文"}, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def test_resume_fingerprint_deterministic() -> None:
    kwargs = dict(
        document_id="doc",
        parsed_document_id="parsed",
        pipeline_version="p1",
        extraction_schema_version="resume.v1",
        prompt_version="h:abc",
        llm_model="deepseek-chat",
    )
    first = resume_profile_fingerprint(**kwargs)
    second = resume_profile_fingerprint(**kwargs)
    assert first == second
    assert len(first) == 64


def test_fingerprint_changes_when_any_version_changes() -> None:
    base = dict(
        document_id="doc",
        parsed_document_id="parsed",
        pipeline_version="p1",
        extraction_schema_version="resume.v1",
        prompt_version="h:abc",
        llm_model="deepseek-chat",
    )
    baseline = resume_profile_fingerprint(**base)
    assert resume_profile_fingerprint(**{**base, "prompt_version": "h:zzz"}) != baseline
    assert resume_profile_fingerprint(**{**base, "llm_model": "deepseek-reasoner"}) != baseline
    assert resume_profile_fingerprint(**{**base, "pipeline_version": "p2"}) != baseline
    assert resume_profile_fingerprint(**{**base, "parsed_document_id": "other"}) != baseline


def test_resume_and_jd_fingerprints_differ_for_same_inputs() -> None:
    kwargs = dict(
        document_id="doc",
        parsed_document_id="parsed",
        pipeline_version="p1",
        extraction_schema_version="v1",
        prompt_version="h:abc",
        llm_model="m",
    )
    assert resume_profile_fingerprint(**kwargs) != jd_profile_fingerprint(**kwargs)


def test_sha256_hex_matches_stdlib() -> None:
    import hashlib

    assert sha256_hex("abc") == hashlib.sha256(b"abc").hexdigest()

# mypy: disable-error-code="arg-type,attr-defined,assignment,union-attr"
"""integration: Phase 3 API（§28）——真实 PG + 真实检索，只注入 test-only LLM provider。"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from support import (
    JD_MATCH_PAYLOAD,
    RESUME_MATCH_PAYLOAD,
    read_fixture_bytes,
)

pytestmark = pytest.mark.db


def _upload(client: TestClient, kind: str, filename: str) -> dict:
    response = client.post(
        "/documents",
        data={"kind": kind},
        files={"file": (filename, read_fixture_bytes(filename), "text/plain")},
    )
    assert response.status_code in {200, 201}, response.text
    return response.json()


def _prepare(client: TestClient) -> tuple[dict, dict]:
    client.provider.resume_payload = dict(RESUME_MATCH_PAYLOAD)  # type: ignore[attr-defined]
    client.provider.jd_payload = dict(JD_MATCH_PAYLOAD)  # type: ignore[attr-defined]
    resume = _upload(client, "resume", "resume_match.txt")
    jd = _upload(client, "jd", "jd_match.txt")
    return resume, jd


def test_full_phase3_api_flow(phase3_client: TestClient) -> None:
    client = phase3_client
    resume, jd = _prepare(client)

    created = client.post(
        "/analyses", json={"resume_document_id": resume["id"], "jd_document_id": jd["id"]}
    )
    assert created.status_code == 201
    analysis = created.json()
    assert analysis["status"] == "queued"
    assert analysis["resume_profile_id"] is None  # 未提供绑定 => 由执行期绑定，不猜
    assert analysis["embedding_model"] == "hash-ngram-v1"
    assert analysis["ruleset_version"].startswith("r:")
    assert analysis["scoring_version"] == "s:0.2.0"
    assert analysis["reused"] is False
    analysis_id = analysis["id"]

    # 幂等入队：相同 identity 不制造新行
    again = client.post(
        "/analyses", json={"resume_document_id": resume["id"], "jd_document_id": jd["id"]}
    )
    assert again.status_code == 200
    assert again.json()["id"] == analysis_id
    assert again.json()["reused"] is True

    # 未执行前没有评分
    assert client.get(f"/analyses/{analysis_id}/score").status_code == 404

    run = client.post(f"/analyses/{analysis_id}/run", json={})
    assert run.status_code == 200, run.text
    body = run.json()
    assert body["status"] == "completed"
    assert body["gate"] == "pass"
    assert body["score_total"] == 100.0
    assert body["constraint_count"] == 6
    assert body["skill_match_count"] == 2
    assert body["trace_count"] == 13

    # 终态与绑定
    status = client.get(f"/analyses/{analysis_id}").json()
    assert status["status"] == "succeeded"
    assert status["resume_profile_id"] and status["jd_profile_id"]

    constraints = client.get(f"/analyses/{analysis_id}/constraints").json()
    assert len(constraints) == 6
    assert {item["result"] for item in constraints} == {"MET"}
    assert all(item["basis"] == "deterministic" for item in constraints)
    assert all(item["ruleset_version"] for item in constraints)
    assert all(item["reason_code"] for item in constraints)
    assert all(item["trace_id"] for item in constraints)

    matches = client.get(f"/analyses/{analysis_id}/skill-matches").json()
    assert {item["status"] for item in matches} == {"matched"}
    assert all(item["norm_used"] in {"python", "postgresql"} for item in matches)

    traces = client.get(f"/analyses/{analysis_id}/trace").json()
    assert len(traces) == 13
    assert {item["decision_type"] for item in traces} == {
        "constraint",
        "skill_match",
        "score_component",
    }

    score = client.get(f"/analyses/{analysis_id}/score").json()
    assert score["total"] == 100.0
    assert score["gate"] == "pass"
    assert score["scoring_version"] == "s:0.2.0"
    assert {item["section"] for item in score["per_section"]} == {
        "skills",
        "experience",
        "education",
        "location",
        "language",
    }

    # 重复执行：非 queued => 409，且无副作用
    conflict = client.post(f"/analyses/{analysis_id}/run", json={})
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["status"] == "not_claimable"

    # 显式 requeue 重跑：结果幂等
    rerun = client.post(f"/analyses/{analysis_id}/run", json={"requeue": True})
    assert rerun.status_code == 200
    assert rerun.json()["trace_count"] == 13
    assert len(client.get(f"/analyses/{analysis_id}/constraints").json()) == 6


def test_explicit_bindings_are_validated(phase3_client: TestClient) -> None:
    client = phase3_client
    resume, jd = _prepare(client)
    analysis = client.post(
        "/analyses", json={"resume_document_id": resume["id"], "jd_document_id": jd["id"]}
    ).json()
    assert client.post(f"/analyses/{analysis['id']}/run", json={}).status_code == 200
    completed = client.get(f"/analyses/{analysis['id']}").json()

    bound = client.post(
        "/analyses",
        json={
            "resume_document_id": resume["id"],
            "jd_document_id": jd["id"],
            "resume_profile_id": completed["resume_profile_id"],
            "jd_profile_id": completed["jd_profile_id"],
        },
    )
    assert bound.status_code == 201, bound.text
    payload = bound.json()
    assert payload["resume_profile_id"] == completed["resume_profile_id"]
    assert payload["jd_profile_id"] == completed["jd_profile_id"]

    # 归属错误的 profile（另一个 document）必须被拒绝
    other_resume = _upload(client, "resume", "resume_mismatch.txt")
    bad = client.post(
        "/analyses",
        json={
            "resume_document_id": other_resume["id"],
            "jd_document_id": jd["id"],
            "resume_profile_id": completed["resume_profile_id"],  # 属于另一个 document
        },
    )
    assert bad.status_code == 422


def test_retrieval_endpoint_with_anchor_gate(phase3_client: TestClient) -> None:
    client = phase3_client
    resume, _jd = _prepare(client)
    assert client.post(f"/documents/{resume['id']}/parse").status_code == 200
    artifacts = client.get(f"/documents/{resume['id']}/artifacts").json()["artifacts"]
    parsed_id = next(item["artifact_id"] for item in artifacts if item["kind"] == "parsed_document")

    hit = client.post(
        "/retrieval/search",
        json={
            "query": "熟悉 Python",
            "parsed_document_ids": [parsed_id],
            "top_k": 3,
            "anchor_terms": ["Python"],
        },
    )
    assert hit.status_code == 200, hit.text
    payload = hit.json()
    assert payload["hits"]
    assert payload["retrieval_method"] in {"vector", "lexical"}
    assert payload["embedding_model"] == "hash-ngram-v1"
    first = payload["hits"][0]
    assert first["parsed_document_id"] == parsed_id
    assert set(first) == {
        "source_chunk_id",
        "document_id",
        "parsed_document_id",
        "chunk_index",
        "page",
        "char_start",
        "char_end",
        "score",
        "retrieval_method",
    }
    assert "content" not in first  # 不返回文本

    miss = client.post(
        "/retrieval/search",
        json={
            "query": "熟悉 Kubernetes",
            "parsed_document_ids": [parsed_id],
            "top_k": 3,
            "anchor_terms": ["Kubernetes"],
        },
    )
    assert miss.status_code == 200
    assert miss.json()["hits"] == []

    invalid = client.post(
        "/retrieval/search", json={"query": "x", "parsed_document_ids": [], "top_k": 3}
    )
    assert invalid.status_code == 422


def test_not_found_paths(phase3_client: TestClient) -> None:
    client = phase3_client
    missing = uuid.uuid4()
    assert client.post(f"/analyses/{missing}/run", json={}).status_code == 404
    assert client.get(f"/analyses/{missing}/constraints").status_code == 404
    assert client.get(f"/analyses/{missing}/skill-matches").status_code == 404
    assert client.get(f"/analyses/{missing}/trace").status_code == 404
    assert client.get(f"/analyses/{missing}/score").status_code == 404

# mypy: disable-error-code="arg-type,attr-defined,assignment,union-attr"
"""integration: Phase 5 API 打磨（§45/§46/§47/§39）——真实 PG + test-only provider。

覆盖：
- 目录只读 API：`GET /analyses`（分页 + 确定性顺序 + PII-safe）、`GET /profiles`；
- 证据 API：`GET /analyses/{id}/evidence`（chunk 定位 + 引用关系，**不含原文**）；
- 统一错误契约：`error.{code,message,details,request_id,retryable}` + 保留 `detail`；
- request id 透传/回写；CORS 显式白名单（非 "*"）。
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from support import JD_MATCH_PAYLOAD, RESUME_MATCH_PAYLOAD, read_fixture_bytes

pytestmark = pytest.mark.db

_PII_KEYS = {"name", "phone", "email", "full_dump", "content", "text", "bullets"}


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
    return _upload(client, "resume", "resume_match.txt"), _upload(client, "jd", "jd_match.txt")


def _create_and_run(client: TestClient, *, idempotency_key: str | None = None) -> str:
    resume, jd = _prepare(client)
    payload: dict = {"resume_document_id": resume["id"], "jd_document_id": jd["id"]}
    if idempotency_key:
        payload["idempotency_key"] = idempotency_key
    created = client.post("/analyses", json=payload)
    assert created.status_code in {200, 201}, created.text
    analysis_id = created.json()["id"]
    run = client.post(f"/analyses/{analysis_id}/run", json={})
    assert run.status_code == 200, run.text
    return analysis_id


# ---------------------------------------------------------------- catalog


def test_analyses_list_is_paginated_ordered_and_pii_free(phase3_client: TestClient) -> None:
    client = phase3_client
    first = _create_and_run(client, idempotency_key="phase5-list-a")
    second = _create_and_run(client, idempotency_key="phase5-list-b")
    assert first != second

    listed = client.get("/analyses", params={"limit": 1, "offset": 0})
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["total"] >= 2
    assert body["limit"] == 1
    assert body["offset"] == 0
    assert len(body["items"]) == 1
    # 确定性顺序：created_at DESC => 后创建的在前
    assert body["items"][0]["id"] == second

    page2 = client.get("/analyses", params={"limit": 1, "offset": 1}).json()
    assert page2["items"][0]["id"] == first

    # 分页不串行：两页不相交
    assert {item["id"] for item in body["items"]}.isdisjoint({item["id"] for item in page2["items"]})

    # 列表响应 PII-free
    flat_keys = {key for item in body["items"] for key in item}
    assert flat_keys.isdisjoint(_PII_KEYS)


def test_profiles_listing_is_pii_safe(phase3_client: TestClient) -> None:
    client = phase3_client
    _create_and_run(client)

    resp = client.get("/profiles", params={"kind": "resume", "limit": 10})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["kind"] == "resume"
    assert body["total"] >= 1
    item = body["items"][0]
    assert item["reference"].startswith("resume-")
    assert item["pipeline_version"]
    assert set(item).isdisjoint(_PII_KEYS)

    # kind 必须受约束
    assert client.get("/profiles", params={"kind": "other"}).status_code == 422
    jd = client.get("/profiles", params={"kind": "jd"}).json()
    assert jd["kind"] == "jd" and jd["total"] >= 1


# ---------------------------------------------------------------- evidence


def test_evidence_endpoint_returns_locators_without_raw_text(phase3_client: TestClient) -> None:
    client = phase3_client
    analysis_id = _create_and_run(client)

    resp = client.get(f"/analyses/{analysis_id}/evidence")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["analysis_id"] == analysis_id
    assert body["total"] == len(body["items"])
    assert body["total"] >= 1

    item = body["items"][0]
    assert item["source_chunk_id"]
    assert item["span_sha256"]
    assert item["resolvable"] is True
    assert item["referenced_by"], "证据必须说明被谁引用"
    kinds = {ref["kind"] for ref in item["referenced_by"]}
    assert kinds <= {"constraint", "skill", "trace"}
    # PII-safe：不含原文与任何 PII 字段
    assert set(item).isdisjoint(_PII_KEYS)

    # 证据可回溯到真实 chunk（确定性：两次读取一致）
    again = client.get(f"/analyses/{analysis_id}/evidence").json()
    assert [i["source_chunk_id"] for i in again["items"]] == [
        i["source_chunk_id"] for i in body["items"]
    ]


def test_evidence_endpoint_404_for_unknown_analysis(phase3_client: TestClient) -> None:
    resp = phase3_client.get(f"/analyses/{uuid.uuid4()}/evidence")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


# ---------------------------------------------------------------- error contract


def test_error_contract_has_code_message_details_request_id(phase3_client: TestClient) -> None:
    client = phase3_client
    missing = uuid.uuid4()

    resp = client.get(f"/analyses/{missing}")
    assert resp.status_code == 404
    body = resp.json()
    assert set(body) >= {"detail", "error"}
    err = body["error"]
    assert err["code"] == "NOT_FOUND"
    assert err["message"]
    assert err["retryable"] is False
    assert err["request_id"]
    # 向后兼容：Phase 1–4 的 detail 语义保留
    assert isinstance(body["detail"], str)

    # 请求体校验失败 => 422 REQUEST_VALIDATION_FAILED + 结构化 details
    bad = client.post("/analyses", json={"resume_document_id": "not-a-uuid"})
    assert bad.status_code == 422
    bad_body = bad.json()
    assert bad_body["error"]["code"] == "REQUEST_VALIDATION_FAILED"
    assert isinstance(bad_body["error"]["details"], list)

    # 冲突（analysis 不可领取）=> 409 CONFLICT，detail 仍为 dict（Phase 2–4 兼容）
    resume, jd = _prepare(client)
    created = client.post(
        "/analyses", json={"resume_document_id": resume["id"], "jd_document_id": jd["id"]}
    ).json()
    conflict = client.post(f"/analyses/{created['id']}/run", json={})
    assert conflict.status_code in {200, 409}
    if conflict.status_code == 409:
        assert conflict.json()["error"]["code"] == "CONFLICT"


def test_request_id_is_generated_and_propagated(phase3_client: TestClient) -> None:
    client = phase3_client
    generated = client.get("/health")
    assert generated.headers.get("X-Request-ID")

    inbound = "test-correlation-id-123"
    echoed = client.get("/health", headers={"X-Request-ID": inbound})
    assert echoed.headers["X-Request-ID"] == inbound

    # 错误响应体携带同一 request id
    miss = client.get(
        f"/analyses/{uuid.uuid4()}", headers={"X-Request-ID": "err-correlation-1"}
    )
    assert miss.json()["error"]["request_id"] == "err-correlation-1"
    assert miss.headers["X-Request-ID"] == "err-correlation-1"


def test_cors_uses_explicit_allowlist_not_wildcard(phase3_client: TestClient) -> None:
    client = phase3_client
    allowed = client.options(
        "/analyses",
        headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET"},
    )
    assert allowed.headers.get("access-control-allow-origin") == "http://localhost:3000"
    assert allowed.headers.get("access-control-allow-origin") != "*"

    denied = client.options(
        "/analyses",
        headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "GET"},
    )
    assert "access-control-allow-origin" not in denied.headers


def test_openapi_documents_phase5_endpoints(phase3_client: TestClient) -> None:
    schema = phase3_client.get("/openapi.json").json()
    paths = schema["paths"]
    assert "/analyses" in paths and "get" in paths["/analyses"]
    assert "/profiles" in paths and "get" in paths["/profiles"]
    assert "/analyses/{analysis_id}/evidence" in paths
    evidence_op = paths["/analyses/{analysis_id}/evidence"]["get"]
    assert evidence_op["responses"].get("200")
    # 明确列出错误响应契约（§22）
    assert "404" in evidence_op["responses"]

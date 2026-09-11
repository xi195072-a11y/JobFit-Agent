"""integration: Phase 2 API（parse / artifacts / extract）端到端。"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from jobfit.api.deps import get_session, get_session_factory
from jobfit.api.v1.extractions import get_settings_dep
from jobfit.main import create_app
from support import read_fixture_bytes

pytestmark = pytest.mark.db


def _upload(client: TestClient, kind: str, filename: str, content: bytes) -> str:
    resp = client.post(
        "/documents",
        data={"kind": kind},
        files={"file": (filename, content, "text/plain")},
    )
    assert resp.status_code in {200, 201}, resp.text
    return str(resp.json()["id"])


def test_parse_endpoint_is_idempotent(phase2_client: TestClient) -> None:
    document_id = _upload(phase2_client, "resume", "resume.txt", read_fixture_bytes("resume.txt"))
    first = phase2_client.post(f"/documents/{document_id}/parse")
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["chunk_count"] >= 1
    assert body["chunks_created"] == body["chunk_count"]
    assert body["reused_artifact"] is False
    assert body["parser_version"].startswith("p2.0.0")

    second = phase2_client.post(f"/documents/{document_id}/parse")
    assert second.status_code == 200
    assert second.json()["reused_artifact"] is True
    assert second.json()["chunks_created"] == 0
    assert second.json()["parsed_document_id"] == body["parsed_document_id"]


def test_artifacts_endpoint_lists_versions_without_pii(phase2_client: TestClient) -> None:
    document_id = _upload(phase2_client, "resume", "resume.txt", read_fixture_bytes("resume.txt"))
    phase2_client.post(f"/documents/{document_id}/parse")
    resp = phase2_client.get(f"/documents/{document_id}/artifacts")
    assert resp.status_code == 200
    payload = resp.json()
    kinds = {item["kind"] for item in payload["artifacts"]}
    assert "parsed_document" in kinds
    raw = json.dumps(payload, ensure_ascii=False)
    assert "13800138000" not in raw
    assert "zhangsan@example.com" not in raw
    assert "张三" not in raw


def test_extract_endpoint_returns_artifact_identity(phase2_client: TestClient) -> None:
    resume_id = _upload(phase2_client, "resume", "resume.txt", read_fixture_bytes("resume.txt"))
    jd_id = _upload(phase2_client, "jd", "jd.txt", read_fixture_bytes("jd.txt"))
    created = phase2_client.post(
        "/analyses", json={"resume_document_id": resume_id, "jd_document_id": jd_id}
    )
    assert created.status_code == 201
    analysis_id = created.json()["id"]

    resp = phase2_client.post(f"/analyses/{analysis_id}/extract")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "completed"
    assert body["resume_profile_id"] and body["jd_profile_id"]
    assert len(body["resume_fingerprint"]) == 64
    assert len(body["jd_fingerprint"]) == 64
    assert body["resume_fingerprint"] != body["jd_fingerprint"]

    # 同一 analysis 已处于 running（未进入 review）=> 不可重复领取
    again = phase2_client.post(f"/analyses/{analysis_id}/extract")
    assert again.status_code == 409
    assert again.json()["detail"]["status"] == "not_claimable"

    artifacts = phase2_client.get(f"/documents/{resume_id}/artifacts").json()["artifacts"]
    assert {item["kind"] for item in artifacts} == {"parsed_document", "resume_profile"}


def test_extract_endpoint_without_credentials_returns_503(
    session_factory: sessionmaker, db_settings
) -> None:
    app = create_app()  # 不 override provider：settings 无 DEEPSEEK_API_KEY

    def _session():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_session_factory] = lambda: session_factory
    app.dependency_overrides[get_settings_dep] = lambda: db_settings
    no_key_client = TestClient(app)

    resume_id = _upload(no_key_client, "resume", "resume.txt", read_fixture_bytes("resume.txt"))
    jd_id = _upload(no_key_client, "jd", "jd.txt", read_fixture_bytes("jd.txt"))
    analysis_id = no_key_client.post(
        "/analyses", json={"resume_document_id": resume_id, "jd_document_id": jd_id}
    ).json()["id"]

    resp = no_key_client.post(f"/analyses/{analysis_id}/extract")
    assert resp.status_code == 503
    assert "DEEPSEEK_API_KEY" in resp.json()["detail"]

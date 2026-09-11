"""integration: FastAPI 端点（需真实 PostgreSQL+pgvector）。无 DB 时整组 skip。"""

from __future__ import annotations

import tempfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from jobfit.api.deps import get_session

pytestmark = pytest.mark.db


@pytest.fixture
def client(db_engine, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("STORAGE_DIR", tempfile.mkdtemp(prefix="jobfit-api-"))
    from jobfit.config.settings import get_settings

    get_settings.cache_clear()

    from jobfit.main import create_app

    app = create_app()
    factory = sessionmaker(bind=db_engine, expire_on_commit=False)

    def _override():
        s = factory()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_session] = _override
    return TestClient(app)


def _upload(client: TestClient, kind: str, name: str, content: bytes) -> dict:
    resp = client.post("/documents", data={"kind": kind}, files={"file": (name, content, "text/plain")})
    assert resp.status_code in {200, 201}, resp.text
    return resp.json()


def test_health_ready(client: TestClient) -> None:
    resp = client.get("/health/ready")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ready"


def test_upload_dedupe_and_kind_contract(client: TestClient) -> None:
    content = b"plain txt resume (upload only, parse not run)"
    r1 = _upload(client, "resume", "cv.txt", content)
    r2 = _upload(client, "resume", "cv-copy.txt", content)
    assert r1["id"] == r2["id"]  # 同 sha256 幂等
    assert r1["kind"] == "resume"

    # 错误 kind / 无 magic 匹配的二进制 => 422
    bad = client.post(
        "/documents",
        data={"kind": "resume"},
        files={"file": ("x.bin", b"\x00\x01\x02", "application/octet-stream")},
    )
    assert bad.status_code == 422


def test_analysis_queued_no_fake_result(client: TestClient) -> None:
    resume = _upload(client, "resume", "r.txt", b"resume content")
    jd = _upload(client, "jd", "j.txt", b"jd content")
    resp = client.post(
        "/analyses",
        json={"resume_document_id": resume["id"], "jd_document_id": jd["id"]},
    )
    assert resp.status_code == 201
    analysis = resp.json()
    assert analysis["status"] == "queued"
    assert analysis["current_phase"] == "queued"
    assert analysis["resume_profile_id"] is None
    assert analysis["jd_profile_id"] is None
    assert analysis["extraction_schema_version"] == "resume.v1|jd.v1"

    got = client.get(f"/analyses/{analysis['id']}")
    assert got.status_code == 200
    assert got.json()["id"] == analysis["id"]


def test_kind_mismatch_rejected(client: TestClient) -> None:
    resume = _upload(client, "resume", "r.txt", b"resume bytes again")
    resp = client.post(
        "/analyses",
        json={"resume_document_id": resume["id"], "jd_document_id": resume["id"]},
    )
    assert resp.status_code == 422


def test_not_found(client: TestClient) -> None:
    assert client.get("/analyses/00000000-0000-0000-0000-000000000000").status_code == 404
    assert client.get("/documents/00000000-0000-0000-0000-000000000000").status_code == 404

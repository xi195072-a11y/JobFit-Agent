"""/health（离线可跑，不依赖 DB）。/health/ready 属 integration(test_api)。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from jobfit.config.settings import get_settings
from jobfit.main import create_app


def test_health_ok() -> None:
    app = create_app()
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["pipeline_version"] == get_settings().pipeline_version

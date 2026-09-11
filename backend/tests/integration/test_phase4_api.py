# mypy: disable-error-code="arg-type,attr-defined,assignment,union-attr"
"""integration: Phase 4 API（§39/§40）——真实 PG + test-only LLM provider。

api_client 的 settings 无 DEEPSEEK_API_KEY => `_provider_or_none` 返回 None，
POST /critique 走 EXTERNAL CREDENTIAL BLOCKED 路径：critique 落 unavailable，
绝不伪造 live 验证（§18/§48）。已校验 critique 的路径由服务层集成测试覆盖。
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from support import JD_MATCH_PAYLOAD, RESUME_MATCH_PAYLOAD, read_fixture_bytes

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


def _run_phase3(client: TestClient) -> str:
    resume, jd = _prepare(client)
    created = client.post(
        "/analyses", json={"resume_document_id": resume["id"], "jd_document_id": jd["id"]}
    )
    assert created.status_code == 201
    analysis_id = created.json()["id"]
    run = client.post(f"/analyses/{analysis_id}/run", json={})
    assert run.status_code == 200, run.text
    assert run.json()["status"] == "completed"
    assert client.get(f"/analyses/{analysis_id}").json()["status"] == "succeeded"
    return analysis_id


def test_full_phase4_api_flow_with_blocked_credentials(phase3_client: TestClient) -> None:
    client = phase3_client
    analysis_id = _run_phase3(client)

    # POST /critique：无凭证 => critique unavailable（不伪造），report 仍以确定性结果装配
    run = client.post(f"/analyses/{analysis_id}/critique")
    assert run.status_code == 200, run.text
    body = run.json()
    assert body["status"] == "completed"
    assert body["critique_status"] == "unavailable"
    assert body["critique_validation"] == "pending"
    assert body["report_version"] == 1
    assert body["report_stage"] == "validated"

    status = client.get(f"/analyses/{analysis_id}").json()
    assert status["status"] == "awaiting_review"

    critique = client.get(f"/analyses/{analysis_id}/critique").json()
    assert critique["status"] == "unavailable"
    assert critique["citations_validated"] is False
    assert critique["content"]["reason"] == "external_credential_blocked"

    report = client.get(f"/analyses/{analysis_id}/report").json()
    assert report["stage"] == "validated"
    assert report["version"] == 1
    sections = {s["type"] for s in report["content"]["sections"]}
    # critique 未 validated：不得出现 strengths/gaps/risks 正文分区
    assert "strengths" not in sections
    assert report["content"]["meta"]["critique"]["citations_validated"] is False

    # 重复触发：非 succeeded => 409，无副作用
    again = client.post(f"/analyses/{analysis_id}/critique")
    assert again.status_code == 409
    assert again.json()["detail"]["status"] == "not_claimable"
    assert client.get(f"/analyses/{analysis_id}/report").json()["version"] == 1

    # API 路径重新装配报告 => 新 version，仍 valid
    rebuilt = client.post(f"/analyses/{analysis_id}/report")
    assert rebuilt.status_code == 200, rebuilt.text
    assert rebuilt.json()["version"] == 2
    assert rebuilt.json()["valid"] is True

    # approve => finalized + report final（unavailable critique 不阻止 finalize，§32）
    approve = client.post(
        f"/analyses/{analysis_id}/review",
        json={"decision": "approve", "comments": "确认无误", "reviewed_by": "reviewer-a"},
    )
    assert approve.status_code == 200, approve.text
    assert approve.json()["to_state"] == "finalized"
    assert client.get(f"/analyses/{analysis_id}").json()["status"] == "finalized"
    final_report = client.get(f"/analyses/{analysis_id}/report").json()
    assert final_report["stage"] == "final"
    assert final_report["published_at"] is not None

    # 终态不可再 approve：状态机拒绝 => 422
    again_approve = client.post(
        f"/analyses/{analysis_id}/review", json={"decision": "approve"}
    )
    assert again_approve.status_code == 422

    # 审计：review 行已落库
    reviews = client.get(f"/analyses/{analysis_id}/reviews").json()
    assert len(reviews) == 1
    assert reviews[0]["decision"] == "approve"
    assert reviews[0]["from_state"] == "awaiting_review"


def test_quick_finalize_endpoint(phase3_client: TestClient) -> None:
    client = phase3_client
    analysis_id = _run_phase3(client)
    assert client.post(f"/analyses/{analysis_id}/critique").status_code == 200

    finalize = client.post(f"/analyses/{analysis_id}/finalize")
    assert finalize.status_code == 200, finalize.text
    assert finalize.json()["decision"] == "approve"
    assert finalize.json()["to_state"] == "finalized"
    assert client.get(f"/analyses/{analysis_id}/report").json()["stage"] == "final"


def test_reject_requires_reason_and_requeue_returns_to_queued(phase3_client: TestClient) -> None:
    client = phase3_client
    analysis_id = _run_phase3(client)
    assert client.post(f"/analyses/{analysis_id}/critique").status_code == 200

    # reject 必须给出理由（§33）：无 body 或空 comments => 422
    assert client.post(f"/analyses/{analysis_id}/reject").status_code == 422
    empty_reason = client.post(
        f"/analyses/{analysis_id}/reject",
        json={"decision": "approve", "comments": "   "},  # decision 被忽略，comments 为空
    )
    assert empty_reason.status_code == 422

    rejected = client.post(
        f"/analyses/{analysis_id}/reject",
        json={"decision": "approve", "comments": "证据不足，需要补交材料"},
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["to_state"] == "rejected"
    assert client.get(f"/analyses/{analysis_id}").json()["status"] == "rejected"

    # requeue（rejected -> queued，§33：新 execution，旧结果保留审计）
    requeue = client.post(
        f"/analyses/{analysis_id}/review",
        json={"decision": "request_changes", "comments": "requeue"},
    )
    assert requeue.status_code == 200, requeue.text
    assert requeue.json()["to_state"] == "queued"
    assert client.get(f"/analyses/{analysis_id}").json()["status"] == "queued"
    assert len(client.get(f"/analyses/{analysis_id}/reviews").json()) == 2


def test_illegal_review_transition_rejected(phase3_client: TestClient) -> None:
    client = phase3_client
    analysis_id = _run_phase3(client)
    # succeeded 状态不可直接 approve（必须先走 critique 管线到 awaiting_review）
    bad = client.post(f"/analyses/{analysis_id}/review", json={"decision": "approve"})
    assert bad.status_code == 422

    # finalize 前置：无 validated 报告 => 422
    before_report = client.post(f"/analyses/{analysis_id}/finalize")
    assert before_report.status_code == 422


def test_phase4_not_found_paths(phase3_client: TestClient) -> None:
    client = phase3_client
    missing = uuid.uuid4()
    assert client.post(f"/analyses/{missing}/critique").status_code == 404
    assert client.get(f"/analyses/{missing}/critique").status_code == 404
    assert client.get(f"/analyses/{missing}/critiques").status_code == 404
    assert client.get(f"/analyses/{missing}/report").status_code == 404
    assert client.get(f"/analyses/{missing}/reports").status_code == 404
    assert client.get(f"/analyses/{missing}/reviews").status_code == 404
    assert client.post(f"/analyses/{missing}/review", json={"decision": "approve"}).status_code == 404


def test_critique_endpoint_requires_prior_phase3(phase3_client: TestClient) -> None:
    client = phase3_client
    resume, jd = _prepare(client)
    created = client.post(
        "/analyses", json={"resume_document_id": resume["id"], "jd_document_id": jd["id"]}
    )
    analysis_id = created.json()["id"]
    # queued 未执行 => critique 不可触发（非 succeeded => 409，无副作用）
    queued = client.post(f"/analyses/{analysis_id}/critique")
    assert queued.status_code == 409
    assert queued.json()["detail"]["status"] == "not_claimable"
    # 未产生任何 critique/report
    assert client.get(f"/analyses/{analysis_id}/critique").status_code == 404
    assert client.get(f"/analyses/{analysis_id}/report").status_code == 404


def test_critique_endpoint_honours_injected_provider(phase3_client: TestClient) -> None:
    """Phase 5：critique 端点通过依赖注入接受 provider（未注入时返回 None => unavailable）。

    生产默认路径无凭证 => None（EXTERNAL CREDENTIAL BLOCKED）；这里验证
    test-only harness / 未来的托管调用方可以通过 `dependency_overrides` 注入 provider，
    且注入后 critique 走真实校验链路（ok + validated）。
    """
    client = phase3_client
    from jobfit.api.deps import get_optional_llm_provider
    from support import DeterministicProvider, build_valid_critique_payload

    analysis_id = _run_phase3(client)
    client.app.dependency_overrides[get_optional_llm_provider] = lambda: DeterministicProvider(
        critique_payload=build_valid_critique_payload
    )
    try:
        run = client.post(f"/analyses/{analysis_id}/critique")
        assert run.status_code == 200, run.text
        body = run.json()
        assert body["critique_status"] == "ok"
        assert body["critique_validation"] == "validated"
        assert body["report_stage"] == "validated"

        critique = client.get(f"/analyses/{analysis_id}/critique").json()
        assert critique["status"] == "ok"
        assert critique["citations_validated"] is True
        assert critique["content"]["strengths"]
    finally:
        client.app.dependency_overrides.pop(get_optional_llm_provider, None)

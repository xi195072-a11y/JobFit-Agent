"""integration: Phase 2 acceptance（需求 35 的 13 项验收）。"""

from __future__ import annotations

import asyncio
import logging
import shutil
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from jobfit.db import models
from jobfit.observability.logging import configure_logging
from jobfit.workflow.runner import run_extraction_pipeline
from support import (
    RESUME_PAYLOAD,
    DeterministicProvider,
    make_analysis,
    read_fixture_bytes,
)

pytestmark = pytest.mark.db

RESUME_BYTES = read_fixture_bytes("resume.txt")
JD_BYTES = read_fixture_bytes("jd.txt")


def _upload(client: TestClient, kind: str, filename: str, content: bytes) -> str:
    resp = client.post("/documents", data={"kind": kind}, files={"file": (filename, content, "text/plain")})
    assert resp.status_code in {200, 201}, resp.text
    return str(resp.json()["id"])


def test_phase2_acceptance_flow(
    phase2_client: TestClient,
    session: Session,
    session_factory: sessionmaker,
    db_settings,
    caplog: pytest.LogCaptureFixture,
    tmp_path,
) -> None:
    configure_logging("INFO")
    caplog.set_level(logging.INFO)

    # (1)(2) parser succeeds & chunks created
    resume_id = _upload(phase2_client, "resume", "resume.txt", RESUME_BYTES)
    parsed = phase2_client.post(f"/documents/{resume_id}/parse").json()
    assert parsed["chunk_count"] >= 1
    chunk_rows = session.execute(
        select(func.count()).select_from(models.DocumentChunk).where(
            models.DocumentChunk.parsed_document_id == parsed["parsed_document_id"]
        )
    ).scalar_one()
    assert chunk_rows == parsed["chunk_count"]

    # (3)(13) extraction produces profile & API returns artifact identity
    jd_id = _upload(phase2_client, "jd", "jd.txt", JD_BYTES)
    analysis_id = phase2_client.post(
        "/analyses", json={"resume_document_id": resume_id, "jd_document_id": jd_id}
    ).json()["id"]
    extract_resp = phase2_client.post(f"/analyses/{analysis_id}/extract")
    assert extract_resp.status_code == 200, extract_resp.text
    identity = extract_resp.json()
    assert identity["resume_profile_id"] and identity["jd_profile_id"]
    assert len(identity["resume_fingerprint"]) == 64

    session.expire_all()
    resume_profile = session.execute(select(models.ResumeProfile)).scalars().one()
    jd_profile = session.execute(select(models.JDProfile)).scalars().one()
    assert str(resume_profile.id) == identity["resume_profile_id"]

    # (4) child artifacts created
    children = session.execute(
        select(
            select(func.count()).select_from(models.ResumeEducation).scalar_subquery(),
            select(func.count()).select_from(models.ResumeExperience).scalar_subquery(),
            select(func.count()).select_from(models.ResumeSkill).scalar_subquery(),
            select(func.count()).select_from(models.JDRequirement).scalar_subquery(),
        )
    ).one()
    assert children[0] == 1 and children[1] == 1 and children[2] == 3 and children[3] == 5

    # (5) source linkage available
    education = session.execute(select(models.ResumeEducation)).scalars().one()
    assert education.anchors and education.bullet_evidence_ids
    parsed_row = session.get(models.ParsedDocument, resume_profile.parsed_document_id)
    assert parsed_row is not None
    anchor = education.anchors[0]
    assert str(anchor["parsed_document_id"]) == str(resume_profile.parsed_document_id)
    assert "南京大学" in parsed_row.text[int(anchor["char_start"]) : int(anchor["char_end"])]
    evidence_chunk = session.get(models.DocumentChunk, uuid.UUID(str(education.bullet_evidence_ids[0])))
    assert evidence_chunk is not None and evidence_chunk.parsed_document_id == parsed_row.id

    # (6) unknown fields remain unknown (not False, not guessed)
    assert resume_profile.full_dump["location"] is None
    experience_location = session.execute(select(models.ResumeExperience.location)).scalar_one()
    assert experience_location is None
    jd_requirement_types = {row for row in session.execute(select(models.JDRequirement.req_type)).scalars()}
    assert "security_clearance" not in jd_requirement_types  # 未提及即不存在该 requirement

    # (12) provider attempts recorded
    attempts = session.execute(select(func.count()).select_from(models.LlmAttemptLog)).scalar_one()
    assert attempts == 2

    # (11) PII-safe logs
    log_text = caplog.text
    assert "artifact_persisted" in log_text
    assert "13800138000" not in log_text
    assert "zhangsan@example.com" not in log_text
    assert "张三" not in log_text
    assert RESUME_PAYLOAD["experiences"][0]["bullets"][0] not in log_text

    # (7)(8) immutability + identical re-extraction reuses artifact
    original_dump = dict(resume_profile.full_dump)
    original_prompt_version = resume_profile.prompt_version
    second_analysis = make_analysis(
        session, resume_document_id=resume_profile.document_id, jd_document_id=jd_profile.document_id
    )
    provider = DeterministicProvider()
    rerun = asyncio.run(
        run_extraction_pipeline(
            session_factory=session_factory,
            settings=db_settings,
            provider=provider,
            analysis_id=second_analysis.id,
        )
    )
    assert rerun.status == "completed"
    assert provider.calls == []  # 复用，不重复调用 LLM
    profile_count = session.execute(select(func.count()).select_from(models.ResumeProfile)).scalar_one()
    assert profile_count == 1
    session.refresh(resume_profile)
    assert resume_profile.full_dump == original_dump

    # (9)(10) changed prompt version creates new artifact; old remains readable
    prompts = tmp_path / "prompts"
    shutil.copytree(db_settings.config_dir / "prompts", prompts)
    target = prompts / "extract_resume.j2"
    target.write_text(target.read_text(encoding="utf-8") + "\n# acceptance v2\n", encoding="utf-8")
    settings_v2 = db_settings.model_copy(update={"config_dir": tmp_path})
    third_analysis = make_analysis(
        session, resume_document_id=resume_profile.document_id, jd_document_id=jd_profile.document_id
    )
    changed = asyncio.run(
        run_extraction_pipeline(
            session_factory=session_factory,
            settings=settings_v2,
            provider=DeterministicProvider(),
            analysis_id=third_analysis.id,
        )
    )
    assert changed.status == "completed"
    profiles = session.execute(
        select(models.ResumeProfile).order_by(models.ResumeProfile.extracted_at)
    ).scalars().all()
    assert len(profiles) == 2
    assert len({p.prompt_version for p in profiles}) == 2
    assert original_prompt_version in {p.prompt_version for p in profiles}
    assert profiles[0].full_dump == original_dump

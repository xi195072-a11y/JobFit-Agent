# mypy: disable-error-code="arg-type,attr-defined,assignment,union-attr,index"
"""integration: Phase 2 artifact 语义（不可变 / 幂等 / 并发 / grounding / UNKNOWN 持久化）。"""

from __future__ import annotations

import asyncio
import hashlib
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import func, insert, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from jobfit.core.schemas import JDProfile, ResumeProfile
from jobfit.db import models
from jobfit.db.repositories import artifacts as artifacts_repo
from jobfit.db.repositories import parse_artifacts as pa_repo
from jobfit.extraction.fingerprints import resume_profile_fingerprint
from jobfit.extraction.service import extract_for_analysis
from jobfit.parsing.service import parse_document
from jobfit.workflow.runner import run_extraction_pipeline
from support import (
    JD_PAYLOAD,
    RESUME_PAYLOAD,
    DeterministicProvider,
    make_analysis,
    make_document,
    read_fixture_bytes,
)

pytestmark = pytest.mark.db


def _prepare_analysis(session: Session, settings) -> models.Analysis:
    resume = make_document(
        session, settings, kind="resume", filename="resume.txt", content=read_fixture_bytes("resume.txt")
    )
    jd = make_document(session, settings, kind="jd", filename="jd.txt", content=read_fixture_bytes("jd.txt"))
    return make_analysis(session, resume_document_id=resume.id, jd_document_id=jd.id)


# ---------------------------------------------------------------- parse / chunks

def test_parse_is_idempotent_and_persists_chunks(session: Session, db_settings) -> None:
    doc = make_document(
        session, db_settings, kind="resume", filename="resume.txt", content=read_fixture_bytes("resume.txt")
    )
    first = parse_document(session, db_settings, doc)
    assert first.chunk_count >= 1
    assert first.chunks_created == first.chunk_count
    assert first.reused_artifact is False

    second = parse_document(session, db_settings, doc)
    assert second.parsed_document_id == first.parsed_document_id
    assert second.reused_artifact is True
    assert second.chunks_created == 0
    assert second.chunk_count == first.chunk_count

    rows = pa_repo.list_chunks(session, first.parsed_document_id)
    assert len(rows) == first.chunk_count
    content_hash = session.execute(
        select(models.ParsedDocument.content_sha256).where(models.ParsedDocument.id == first.parsed_document_id)
    ).scalar_one()
    parsed_row = session.get(models.ParsedDocument, first.parsed_document_id)
    assert parsed_row is not None
    assert content_hash == hashlib.sha256(parsed_row.text.encode("utf-8")).hexdigest()


def test_chunk_unique_constraint_is_enforced_by_database(session: Session, db_settings) -> None:
    doc = make_document(session, db_settings, kind="resume", filename="r.txt", content=b"hello world")
    outcome = parse_document(session, db_settings, doc)
    with pytest.raises(IntegrityError):
        session.execute(
            insert(models.DocumentChunk).values(
                parsed_document_id=outcome.parsed_document_id,
                chunk_index=0,
                content="duplicate index",
                page=1,
                char_start=0,
                char_end=1,
                span_sha256="deadbeef",
            )
        )
        session.commit()
    session.rollback()


# ---------------------------------------------------------------- extraction artifact

def test_extraction_creates_artifacts_children_and_binding(session: Session, session_factory, db_settings) -> None:
    analysis = _prepare_analysis(session, db_settings)
    provider = DeterministicProvider()

    result = asyncio.run(
        run_extraction_pipeline(
            session_factory=session_factory,
            settings=db_settings,
            provider=provider,
            analysis_id=analysis.id,
        )
    )
    assert result.status == "completed", result.errors
    assert provider.calls == ["resume", "jd"]

    session.expire_all()
    row = session.get(models.Analysis, analysis.id)
    assert row is not None
    assert row.resume_profile_id is not None
    assert row.jd_profile_id is not None
    assert row.current_phase == "extraction_complete"

    resume_profile = session.get(models.ResumeProfile, row.resume_profile_id)
    jd_profile = session.get(models.JDProfile, row.jd_profile_id)
    assert resume_profile is not None and jd_profile is not None

    expected_fp = resume_profile_fingerprint(
        document_id=str(resume_profile.document_id),
        parsed_document_id=str(resume_profile.parsed_document_id),
        pipeline_version=resume_profile.pipeline_version,
        extraction_schema_version=resume_profile.extraction_schema_version,
        prompt_version=resume_profile.prompt_version,
        llm_model=resume_profile.llm_model,
    )
    assert resume_profile.full_dump["_meta"]["fingerprint"] == expected_fp

    education = session.execute(
        select(func.count())
        .select_from(models.ResumeEducation)
        .where(models.ResumeEducation.profile_id == resume_profile.id)
    ).scalar_one()
    experiences = session.execute(
        select(func.count())
        .select_from(models.ResumeExperience)
        .where(models.ResumeExperience.profile_id == resume_profile.id)
    ).scalar_one()
    skills = session.execute(
        select(func.count())
        .select_from(models.ResumeSkill)
        .where(models.ResumeSkill.profile_id == resume_profile.id)
    ).scalar_one()
    requirements = session.execute(
        select(func.count()).select_from(models.JDRequirement).where(models.JDRequirement.profile_id == jd_profile.id)
    ).scalar_one()
    assert (education, experiences, skills) == (1, 1, 3)
    assert requirements == 5  # 4 hard + 1 preferred


def test_second_run_reuses_artifacts_without_llm_calls(session: Session, session_factory, db_settings) -> None:
    first = _prepare_analysis(session, db_settings)
    asyncio.run(
        run_extraction_pipeline(
            session_factory=session_factory,
            settings=db_settings,
            provider=DeterministicProvider(),
            analysis_id=first.id,
        )
    )
    second = _prepare_analysis(session, db_settings)
    provider = DeterministicProvider()
    result = asyncio.run(
        run_extraction_pipeline(
            session_factory=session_factory,
            settings=db_settings,
            provider=provider,
            analysis_id=second.id,
        )
    )
    assert result.status == "completed"
    assert provider.calls == []  # 复用：不调用 LLM、不消耗 attempt

    counts = session.execute(
        text(
            "SELECT (SELECT count(*) FROM resume_profiles),"
            " (SELECT count(*) FROM jd_profiles),"
            " (SELECT count(*) FROM llm_attempt_log)"
        )
    ).first()
    assert counts is not None
    assert counts[0] == 1 and counts[1] == 1  # 同指纹只保留一行
    assert counts[2] == 2  # 第一次运行的两次真实调用


def test_changed_prompt_version_creates_new_artifact_keeping_old_readable(
    session: Session, session_factory, db_settings, tmp_path
) -> None:
    import shutil

    first = _prepare_analysis(session, db_settings)
    asyncio.run(
        run_extraction_pipeline(
            session_factory=session_factory,
            settings=db_settings,
            provider=DeterministicProvider(),
            analysis_id=first.id,
        )
    )
    old_profile = session.execute(select(models.ResumeProfile)).scalars().one()
    old_prompt_version = old_profile.prompt_version
    old_dump = dict(old_profile.full_dump)

    prompts = tmp_path / "prompts"
    shutil.copytree(db_settings.config_dir / "prompts", prompts)
    target = prompts / "extract_resume.j2"
    target.write_text(target.read_text(encoding="utf-8") + "\n# v2 rule\n", encoding="utf-8")
    settings_v2 = db_settings.model_copy(update={"config_dir": tmp_path})

    second = _prepare_analysis(session, db_settings)
    result = asyncio.run(
        run_extraction_pipeline(
            session_factory=session_factory,
            settings=settings_v2,
            provider=DeterministicProvider(),
            analysis_id=second.id,
        )
    )
    assert result.status == "completed"

    profiles = session.execute(select(models.ResumeProfile).order_by(models.ResumeProfile.extracted_at)).scalars().all()
    assert len(profiles) == 2  # 新 prompt_version => 新 artifact（禁止 overwrite）
    versions = {p.prompt_version for p in profiles}
    assert old_prompt_version in versions
    assert len(versions) == 2
    session.refresh(profiles[0])
    assert profiles[0].full_dump == old_dump  # 旧 artifact 内容不变


def test_unknown_fields_persisted_as_null_not_false(session: Session, session_factory, db_settings) -> None:
    payload = {
        **RESUME_PAYLOAD,
        "location": None,
        "education": [{**RESUME_PAYLOAD["education"][0], "gpa": None}],
        "skills": [{"skill_raw": "Python", "category": None, "proficiency": None, "evidence_quotes": ["Python 熟练"]}],
    }
    analysis = _prepare_analysis(session, db_settings)
    provider = DeterministicProvider(resume_payload=payload)
    result = asyncio.run(
        run_extraction_pipeline(
            session_factory=session_factory, settings=db_settings, provider=provider, analysis_id=analysis.id
        )
    )
    assert result.status == "completed", result.errors

    gpa = session.execute(select(models.ResumeEducation.gpa)).scalar_one()
    proficiency = session.execute(select(models.ResumeSkill.proficiency)).scalar_one()
    assert gpa is None
    assert proficiency is None

    profile = session.execute(select(models.ResumeProfile)).scalars().one()
    assert profile.full_dump["location"] is None
    assert profile.full_dump["education"][0]["gpa"] is None
    assert profile.full_dump["education"][0]["degree"] == "本科"


def test_grounding_rows_are_traceable_to_chunks(session: Session, session_factory, db_settings) -> None:
    analysis = _prepare_analysis(session, db_settings)
    asyncio.run(
        run_extraction_pipeline(
            session_factory=session_factory,
            settings=db_settings,
            provider=DeterministicProvider(),
            analysis_id=analysis.id,
        )
    )
    profile = session.execute(select(models.ResumeProfile)).scalars().one()
    education = session.execute(select(models.ResumeEducation)).scalars().one()
    assert education.anchors, "education 必须携带 anchor"

    anchor = education.anchors[0]
    parsed = session.get(models.ParsedDocument, profile.parsed_document_id)
    assert parsed is not None
    assert str(anchor["parsed_document_id"]) == str(profile.parsed_document_id)
    span_text = parsed.text[int(anchor["char_start"]) : int(anchor["char_end"])]
    assert "南京大学" in span_text

    assert education.bullet_evidence_ids, "education 必须记录 evidence chunk"
    evidence_chunk = session.get(models.DocumentChunk, uuid.UUID(str(education.bullet_evidence_ids[0])))
    assert evidence_chunk is not None
    assert evidence_chunk.parsed_document_id == parsed.id

    skill_rows = session.execute(select(models.ResumeSkill)).scalars().all()
    assert skill_rows
    for skill in skill_rows:
        assert skill.evidence_ids, "skill 必须可回溯到 chunk"
        for evidence_id in skill.evidence_ids:
            assert session.get(models.DocumentChunk, uuid.UUID(str(evidence_id))) is not None


def test_concurrent_create_same_fingerprint_yields_single_row(
    db_engine, session_factory: sessionmaker[Session]
) -> None:
    resume_doc_id = uuid.uuid4()
    parsed_id = uuid.uuid4()
    session = session_factory()
    doc = models.Document(
        id=resume_doc_id,
        kind="resume",
        original_filename="concurrent.txt",
        mime_type="text/plain",
        size_bytes=1,
        sha256=hashlib.sha256(b"concurrent").hexdigest(),
        storage_path="aa/concurrent.bin",
    )
    session.add(doc)
    session.add(
        models.ParsedDocument(
            id=parsed_id,
            document_id=resume_doc_id,
            parser_version="p:concurrent",
            text="t",
            pages={"pages": []},
            content_sha256=hashlib.sha256(b"t").hexdigest(),
        )
    )
    session.commit()
    session.close()

    barrier = threading.Barrier(2)
    results: list[bool] = []
    lock = threading.Lock()

    def worker() -> None:
        local = session_factory()
        try:
            barrier.wait(timeout=10)
            _row, created = artifacts_repo.get_or_create_resume_profile(
                local,
                document_id=resume_doc_id,
                parsed_document_id=parsed_id,
                pipeline_version="p-concurrent",
                extraction_schema_version="resume.v1",
                prompt_version="h:concurrent",
                llm_model="m",
                full_dump={"concurrent": True},
            )
            with lock:
                results.append(created)
        finally:
            local.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker) for _ in range(2)]
        for future in futures:
            future.result(timeout=30)

    check = session_factory()
    total = check.execute(
        select(func.count()).select_from(models.ResumeProfile).where(models.ResumeProfile.document_id == resume_doc_id)
    ).scalar_one()
    check.close()
    assert total == 1, "同一 fingerprint 并发创建必须只产生一行"
    assert sorted(results) == [False, True]


def test_extract_for_analysis_compose_marks_created(session: Session, db_settings) -> None:
    from jobfit.db.repositories import analyses as analyses_repo

    analysis = _prepare_analysis(session, db_settings)
    token = analyses_repo.claim_specific(
        session, analysis_id=analysis.id, worker_id="w-compose", ttl_seconds=120
    )
    assert token is not None
    outcome = asyncio.run(
        extract_for_analysis(
            session,
            db_settings,
            analysis=analysis,
            claim_token=token,
            provider=DeterministicProvider(),
            max_llm_attempts=10,
        )
    )
    assert outcome.resume.created is True and outcome.jd.created is True
    assert outcome.resume.fingerprint != outcome.jd.fingerprint
    stored = session.execute(select(models.ResumeProfile.full_dump)).scalar_one()
    resume_domain = ResumeProfile.model_validate({k: v for k, v in stored.items() if k != "_meta"})
    assert resume_domain.document_id == str(analysis.resume_document_id)
    jd_stored = session.execute(select(models.JDProfile.full_dump)).scalar_one()
    jd_domain = JDProfile.model_validate({k: v for k, v in jd_stored.items() if k != "_meta"})
    assert jd_domain.requirements
    assert JD_PAYLOAD["requirements"][0]["value"] == {"degree": "本科"}

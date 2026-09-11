"""integration: DB ownership / fencing / reservation 语义（需真实 PostgreSQL+pgvector）。

mypy 例外：SQLAlchemy legacy(未注解) Column 在 plugin 下被推断为 Column[Any]，
对这类"运行时 ORM 属性即标量"的用法统一豁免 arg-type/union-attr 误报；
src 仍受全量 mypy 检查。
"""
# mypy: disable-error-code="arg-type,union-attr"

from __future__ import annotations

import hashlib
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from jobfit.core.enums import AnalysisStatus
from jobfit.core.errors import ReservationFailed
from jobfit.db import models
from jobfit.db.repositories import analyses as analyses_repo
from jobfit.db.repositories import artifacts as art_repo
from jobfit.db.repositories import documents as docs_repo

pytestmark = pytest.mark.db


@pytest.fixture
def session(db_engine):
    s = Session(db_engine)
    yield s
    s.close()


def _doc(session: Session, kind: str, tag: str) -> models.Document:
    data = f"{kind}-{tag}".encode()

    doc, _ = docs_repo.create_document(
        session,
        kind=kind,
        original_filename=f"{tag}.txt",
        mime_type="text/plain",
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        storage_path=f"xx/{tag}.bin",
    )
    return doc


def _parsed(session: Session, document_id, parser_version: str = "p:0.1.0"):
    parsed, _ = art_repo.get_or_create_parsed_document(
        session,
        document_id=document_id,
        parser_version=parser_version,
        text="line1\nline2",
        pages={"count": 1},
    )
    return parsed


# ---------------------------------------------------------------- uniqueness

def test_parse_artifact_unique_and_reused(session: Session) -> None:
    doc = _doc(session, "jd", "parse-doc")
    a1, created1 = art_repo.get_or_create_parsed_document(
        session, document_id=doc.id, parser_version="p:1", text="hello", pages={}
    )
    a2, created2 = art_repo.get_or_create_parsed_document(
        session, document_id=doc.id, parser_version="p:1", text="hello", pages={}
    )
    assert created1 is True and created2 is False and a1.id == a2.id
    a3, created3 = art_repo.get_or_create_parsed_document(
        session, document_id=doc.id, parser_version="p:2", text="hello", pages={}
    )
    assert created3 is True and a3.id != a1.id


def test_resume_profile_fingerprint_unique_and_reused(session: Session) -> None:
    doc = _doc(session, "resume", "profile-doc")
    parsed = _parsed(session, doc.id)
    kwargs = dict(
        document_id=doc.id,
        parsed_document_id=parsed.id,
        pipeline_version="2026.09.09-1",
        extraction_schema_version="resume.v1",
        prompt_version="h:abc",
        llm_model="deepseek-chat",
        full_dump={"x": 1},
    )
    p1, created1 = art_repo.get_or_create_resume_profile(session, **kwargs)
    p2, created2 = art_repo.get_or_create_resume_profile(session, **kwargs)
    assert created1 is True and created2 is False and p1.id == p2.id
    # 同 document 不同 fingerprint（换模型）=> 新 artifact，不 overwrite 旧行
    p3, created3 = art_repo.get_or_create_resume_profile(session, **{**kwargs, "llm_model": "deepseek-reasoner"})
    assert created3 is True and p3.id != p1.id


def test_chunk_uniqueness_violation_raises(session: Session) -> None:
    doc = _doc(session, "jd", "chunk-doc")
    parsed = _parsed(session, doc.id)
    session.add(
        models.DocumentChunk(
            parsed_document_id=parsed.id,
            chunk_index=0,
            content="a",
            char_start=0,
            char_end=1,
            span_sha256="s1",
        )
    )
    session.commit()
    session.add(
        models.DocumentChunk(
            parsed_document_id=parsed.id,
            chunk_index=0,
            content="b",
            char_start=0,
            char_end=1,
            span_sha256="s2",
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


# ---------------------------------------------------------------- analysis binding / fencing

def _queued_analysis(session: Session) -> models.Analysis:
    return analyses_repo.create_analysis(
        session,
        resume_document_id=_doc(session, "resume", "ana-r").id,
        jd_document_id=_doc(session, "jd", "ana-j").id,
        pipeline_version="v",
        extraction_schema_version="resume.v1|jd.v1",
        prompt_version="h:a",
        ruleset_version="r:b",
        scoring_version="s:c",
        llm_model="deepseek-chat",
        embedding_model=None,
        config_snapshot={"k": "v"},
    )


def test_analysis_profile_binding_with_fencing(session: Session) -> None:
    analysis = _queued_analysis(session)
    claimed = analyses_repo.claim_next(session, worker_id="w1", ttl_seconds=120)
    assert claimed is not None and claimed.id == analysis.id
    assert claimed.status == AnalysisStatus.RUNNING.value and claimed.claim_token is not None
    token = claimed.claim_token

    resume_doc = session.get(models.Document, analysis.resume_document_id)
    parsed = _parsed(session, resume_doc.id)
    profile, _ = art_repo.get_or_create_resume_profile(
        session,
        document_id=resume_doc.id,
        parsed_document_id=parsed.id,
        pipeline_version="v",
        extraction_schema_version="resume.v1",
        prompt_version="h:a",
        llm_model="deepseek-chat",
        full_dump={},
    )
    assert analyses_repo.fenced_bind_profile(
        session, analysis_id=analysis.id, claim_token=token, profile_kind="resume", profile_id=profile.id
    ) is True
    # 错误 token（被 reclaim 语义）=> False，不绑定
    assert analyses_repo.fenced_bind_profile(
        session, analysis_id=analysis.id, claim_token=uuid.uuid4(), profile_kind="resume", profile_id=profile.id
    ) is False
    session.refresh(analysis)
    assert analysis.resume_profile_id == profile.id


def test_heartbeat_refuses_expired_lease(session: Session) -> None:
    analysis = _queued_analysis(session)
    claimed = analyses_repo.claim_next(session, worker_id="w1", ttl_seconds=120)
    assert claimed is not None and claimed.claim_token is not None
    assert analyses_repo.heartbeat(session, analysis.id, claimed.claim_token, ttl_seconds=120) is True
    # 人为让 lease 过期
    session.execute(
        text("UPDATE analyses SET lease_expires_at = clock_timestamp() - interval '10 seconds' WHERE id = :id"),
        {"id": analysis.id},
    )
    session.commit()
    assert analyses_repo.heartbeat(session, analysis.id, claimed.claim_token, ttl_seconds=120) is False


def test_reservation_monotonic_and_budget(session: Session) -> None:
    analysis = _queued_analysis(session)
    claimed = analyses_repo.claim_next(session, worker_id="w1", ttl_seconds=120)
    assert claimed is not None and claimed.claim_token is not None
    token = claimed.claim_token

    n1 = analyses_repo.reserve_llm_attempt(session, analysis_id=analysis.id, claim_token=token, max_llm_attempts=2)
    n2 = analyses_repo.reserve_llm_attempt(session, analysis_id=analysis.id, claim_token=token, max_llm_attempts=2)
    assert n1 == 1 and n2 == 2  # 单调递增

    with pytest.raises(ReservationFailed):
        analyses_repo.reserve_llm_attempt(session, analysis_id=analysis.id, claim_token=token, max_llm_attempts=2)

    log_count = session.execute(
        text("SELECT count(*) FROM llm_attempt_log WHERE analysis_id = :id"), {"id": analysis.id}
    ).scalar_one()
    assert log_count == 2
    session.refresh(analysis)
    assert analysis.llm_attempts_used == 2


def test_reservation_rejected_without_valid_lease(session: Session) -> None:
    analysis = _queued_analysis(session)
    analyses_repo.claim_next(session, worker_id="w1", ttl_seconds=120)
    session.execute(
        text("UPDATE analyses SET status='queued', claim_token=NULL WHERE id=:id"), {"id": analysis.id}
    )
    session.commit()
    with pytest.raises(ReservationFailed):
        analyses_repo.reserve_llm_attempt(
            session, analysis_id=analysis.id, claim_token=uuid.uuid4(), max_llm_attempts=5
        )


def test_recover_stale_requeues_expired(session: Session) -> None:
    analysis = _queued_analysis(session)
    analyses_repo.claim_next(session, worker_id="w1", ttl_seconds=120)
    session.execute(
        text("UPDATE analyses SET lease_expires_at = clock_timestamp() - interval '10 seconds' WHERE id = :id"),
        {"id": analysis.id},
    )
    session.commit()
    recovered = analyses_repo.recover_stale(session)
    assert analysis.id in recovered
    session.refresh(analysis)
    assert analysis.status == AnalysisStatus.QUEUED.value

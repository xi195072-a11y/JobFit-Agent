"""Phase 5 目录型只读 API：analysis 列表 + 可复用 profile 候选。

产品化（Dashboard / `/jobs` / `/analyses/new`）需要的只读视图：

- `GET /analyses`：分页、确定性顺序的 analysis 列表（Dashboard）。
- `GET /profiles`：可复用的 immutable profile artifact 候选（创建分析时选择），
  **PII-safe**——只返回 id / document 引用 / 版本五元组，无姓名/电话/邮箱/原文。

两者都不写任何业务表（presentation-only read model）。
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from jobfit.api.deps import get_session
from jobfit.api.errors import ERROR_RESPONSES
from jobfit.api.schemas import (
    AnalysisListRead,
    AnalysisRead,
    ProfileListRead,
    ProfileSummaryRead,
)
from jobfit.db import models
from jobfit.db.repositories import analyses as analyses_repo
from jobfit.db.repositories import artifacts as artifacts_repo

router = APIRouter(tags=["phase5-catalog"], responses=ERROR_RESPONSES)


def _profile_summary(kind: str, row: models.ResumeProfile | models.JDProfile) -> ProfileSummaryRead:
    """把 profile 行映射为 PII-safe 摘要（reference 为非 PII 稳定标签）。"""
    profile_id = uuid.UUID(str(row.id))
    return ProfileSummaryRead(
        profile_id=profile_id,
        kind=kind,
        document_id=uuid.UUID(str(row.document_id)),
        parsed_document_id=uuid.UUID(str(row.parsed_document_id)),
        reference=f"{kind}-{str(profile_id)[:8]}",
        pipeline_version=row.pipeline_version,
        extraction_schema_version=row.extraction_schema_version,
        prompt_version=row.prompt_version,
        llm_model=row.llm_model,
        extracted_at=row.extracted_at,
        warning_count=len(row.extraction_warnings or []),
    )


@router.get("/analyses", response_model=AnalysisListRead, responses=ERROR_RESPONSES)
def list_analyses_endpoint(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    status: str | None = Query(default=None, max_length=32),
    session: Session = Depends(get_session),
) -> AnalysisListRead:
    """分页列出 analyses（Dashboard / `/jobs`）。顺序：`created_at DESC, id DESC`。"""
    rows, total = analyses_repo.list_analyses(
        session, limit=limit, offset=offset, status=status
    )
    return AnalysisListRead(
        items=[AnalysisRead.model_validate(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/profiles", response_model=ProfileListRead, responses=ERROR_RESPONSES)
def list_profiles_endpoint(
    kind: str = Query(pattern="^(resume|jd)$", description="profile 类型"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
) -> ProfileListRead:
    """列出可复用的 profile 候选（Analysis 创建页选择用）。返回 PII-safe 摘要。"""
    rows, total = artifacts_repo.list_profiles(session, kind=kind, limit=limit, offset=offset)
    return ProfileListRead(
        kind=kind,
        items=[_profile_summary(kind, row) for row in rows],
        total=total,
    )


__all__ = ["router"]

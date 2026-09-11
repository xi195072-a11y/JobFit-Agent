"""FastAPI 应用工厂（Phase 1 最小 API）。

Phase 1 端点：/health /health/ready /documents /analyses。
upload 只做校验/去重/存储/记录（ingestion stage）；不执行 parsing/extraction，
不会伪造任何分析结果（analysis 创建后停在 queued，等待后续 Phase 的 Worker）。
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, File, Form, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from jobfit.api.deps import get_session
from jobfit.api.errors import ERROR_RESPONSES, ErrorCode, error_payload
from jobfit.api.middleware import RequestIDMiddleware
from jobfit.api.schemas import AnalysisCreate, AnalysisRead, DocumentRead
from jobfit.api.services import queue_analysis
from jobfit.api.v1.analysis import router as phase3_router
from jobfit.api.v1.catalog import router as catalog_router
from jobfit.api.v1.extractions import router as phase2_router
from jobfit.api.v1.phase4 import router as phase4_router
from jobfit.config.settings import Settings, get_settings
from jobfit.core.errors import (
    ConfigurationError,
    Conflict,
    JobFitError,
    LeaseLost,
    NotFound,
    ParseFailure,
    ReservationFailed,
    ValidationFailed,
)
from jobfit.core.version import app_version
from jobfit.db.repositories import analyses as analyses_repo
from jobfit.db.repositories import documents as docs_repo
from jobfit.ingestion.service import ingest_document
from jobfit.ingestion.validate import read_with_limit
from jobfit.observability.logging import configure_logging

_OPENAPI_TAGS: list[dict[str, str]] = [
    {"name": "phase1", "description": "上传 / analysis 创建 / 健康检查"},
    {"name": "phase2", "description": "解析与结构化抽取（immutable artifacts）"},
    {"name": "phase3", "description": "确定性分析：硬条件 / 技能 / 证据 / 决策链 / 评分"},
    {"name": "phase4", "description": "LLM critique / 引用校验 / 报告 / HITL review"},
    {"name": "phase5-catalog", "description": "只读目录：analysis 列表 / profile 候选"},
]


def _request_id(request: Request | None) -> str | None:
    if request is None:
        return None
    return getattr(request.state, "request_id", None)


def _json_error(
    status_code: int,
    *,
    code: str,
    message: str,
    details: object = None,
    detail: object = None,
    request: Request | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=error_payload(
            code=code,
            message=message,
            details=details,
            request_id=_request_id(request),
            detail=detail,
        ),
    )


def _code_for_status(status_code: int) -> str:
    return {
        404: ErrorCode.NOT_FOUND.value,
        409: ErrorCode.CONFLICT.value,
        422: ErrorCode.VALIDATION_FAILED.value,
        503: ErrorCode.CONFIGURATION_ERROR.value,
    }.get(status_code, ErrorCode.HTTP_ERROR.value)


def _register_error_handlers(app: FastAPI) -> None:
    """统一错误契约（Phase 5 §46）：`error.{code,message,details,request_id,retryable}`
    + 保留 Phase 1–4 的 `detail`；绝不回传 traceback。"""

    @app.exception_handler(ValidationFailed)
    async def _validation(request: Request, exc: ValidationFailed) -> JSONResponse:
        return _json_error(
            422, code=ErrorCode.VALIDATION_FAILED.value, message=str(exc), request=request
        )

    @app.exception_handler(ParseFailure)
    async def _parse(request: Request, exc: ParseFailure) -> JSONResponse:
        return _json_error(
            422, code=ErrorCode.PARSE_FAILED.value, message=str(exc), request=request
        )

    @app.exception_handler(NotFound)
    async def _not_found(request: Request, exc: NotFound) -> JSONResponse:
        return _json_error(
            404, code=ErrorCode.NOT_FOUND.value, message=str(exc), request=request
        )

    @app.exception_handler(Conflict)
    async def _conflict(request: Request, exc: Conflict) -> JSONResponse:
        return _json_error(409, code=ErrorCode.CONFLICT.value, message=str(exc), request=request)

    @app.exception_handler(ReservationFailed)
    async def _reservation(request: Request, exc: ReservationFailed) -> JSONResponse:
        return _json_error(
            409, code=ErrorCode.RESERVATION_FAILED.value, message=str(exc), request=request
        )

    @app.exception_handler(LeaseLost)
    async def _lease(request: Request, exc: LeaseLost) -> JSONResponse:
        return _json_error(
            409, code=ErrorCode.LEASE_LOST.value, message=str(exc), request=request
        )

    @app.exception_handler(ConfigurationError)
    async def _config(request: Request, exc: ConfigurationError) -> JSONResponse:
        return _json_error(
            503, code=ErrorCode.CONFIGURATION_ERROR.value, message=str(exc), request=request
        )

    @app.exception_handler(JobFitError)
    async def _jobfit(request: Request, exc: JobFitError) -> JSONResponse:
        return _json_error(
            500, code=ErrorCode.INTERNAL_ERROR.value, message=str(exc), request=request
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        detail = exc.detail
        is_text = isinstance(detail, str)
        return _json_error(
            exc.status_code,
            code=_code_for_status(exc.status_code),
            message=detail if is_text else "request could not be completed",
            details=None if is_text else detail,
            detail=detail,
            request=request,
        )

    @app.exception_handler(RequestValidationError)
    async def _request_validation(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        details = [
            {"loc": list(err.get("loc", ())), "msg": err.get("msg"), "type": err.get("type")}
            for err in exc.errors()
        ]
        return _json_error(
            422,
            code=ErrorCode.REQUEST_VALIDATION_FAILED.value,
            message="request validation failed",
            details=details,
            request=request,
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        # 不泄露异常类型/消息/traceback（§46）；细节只进服务端日志。
        return _json_error(
            500,
            code=ErrorCode.INTERNAL_ERROR.value,
            message="internal server error",
            details={"type": type(exc).__name__},
            request=request,
        )


def create_app() -> FastAPI:
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        configure_logging(settings.log_level)
        yield

    app = FastAPI(
        title="JobFit Agent API",
        version=settings.pipeline_version,
        lifespan=lifespan,
        openapi_tags=_OPENAPI_TAGS,
    )
    # 中间件：RequestID（内层，供错误处理器读取）→ CORS（最外层，保证错误响应也带 CORS 头）。
    app.add_middleware(RequestIDMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,  # 显式白名单，绝不使用 "*"（§39）
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
    )
    _register_error_handlers(app)
    app.include_router(catalog_router)
    app.include_router(phase2_router)
    app.include_router(phase3_router)
    app.include_router(phase4_router)

    # ------------------------------------------------------------ health

    @app.get("/health")
    def health(active_settings: Settings = Depends(lambda: settings)) -> dict[str, str]:
        # §17：只暴露 status / version / pipeline_version；
        # 不泄露运行环境名、数据库连接串、任何凭证。
        return {
            "status": "ok",
            "version": app_version(),
            "pipeline_version": active_settings.pipeline_version,
        }

    @app.get("/health/ready")
    def readiness(session: Session = Depends(get_session)) -> JSONResponse:
        try:
            session.execute(text("SELECT 1"))
        except Exception:
            return JSONResponse(status_code=503, content={"status": "unavailable"})
        return JSONResponse(content={"status": "ready"})

    # ------------------------------------------------------------ documents

    @app.post("/documents", response_model=DocumentRead)
    def upload_document(
        request: Request,
        kind: str = Form(...),
        file: UploadFile = File(...),
        session: Session = Depends(get_session),
    ) -> JSONResponse:
        del request  # 未使用
        settings_for_upload = get_settings()
        data = read_with_limit(file.file, settings_for_upload.max_upload_bytes)
        filename = file.filename or "upload"
        doc, created, _sniffed = ingest_document(
            session,
            settings_for_upload,
            kind=kind,
            filename=filename,
            data=data,
        )
        payload = DocumentRead.model_validate(doc).model_dump(mode="json")
        return JSONResponse(status_code=201 if created else 200, content=payload)

    @app.get("/documents/{document_id}", response_model=DocumentRead, responses=ERROR_RESPONSES)
    def get_document(
        document_id: uuid.UUID,
        session: Session = Depends(get_session),
    ) -> Any:
        doc = docs_repo.get_by_id(session, document_id)
        if doc is None:
            raise NotFound(f"document {document_id} not found")
        return doc

    # ------------------------------------------------------------ analyses

    @app.post("/analyses", response_model=AnalysisRead)
    def create_analysis(
        body: AnalysisCreate,
        session: Session = Depends(get_session),
    ) -> JSONResponse:
        settings_for_api = get_settings()
        analysis, reused = queue_analysis(
            session,
            settings_for_api,
            resume_document_id=body.resume_document_id,
            jd_document_id=body.jd_document_id,
            resume_profile_id=body.resume_profile_id,
            jd_profile_id=body.jd_profile_id,
            idempotency_key=body.idempotency_key,
        )
        payload = AnalysisRead.model_validate(analysis).model_dump(mode="json")
        payload["reused"] = reused
        return JSONResponse(status_code=200 if reused else 201, content=payload)

    @app.get("/analyses/{analysis_id}", response_model=AnalysisRead)
    def get_analysis(analysis_id: uuid.UUID, session: Session = Depends(get_session)) -> Any:
        analysis = analyses_repo.get_analysis(session, analysis_id)
        if analysis is None:
            raise NotFound(f"analysis {analysis_id} not found")
        return analysis

    return app


app = create_app()

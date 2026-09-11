"""请求关联中间件（Phase 5 §47）。

为每个请求分配/透传 `X-Request-ID`：

- 读取入站 `X-Request-ID`（前端→后端 correlation），缺失则生成 uuid4；
- 写入 `request.state.request_id`（错误处理器读取）；
- 绑定到 structlog contextvars（日志可追踪，且日志层已做 PII 脱敏）；
- 回写到响应头 `X-Request-ID`。

不读取/记录请求体，避免 PII 进入日志（§41）。
"""

from __future__ import annotations

import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

REQUEST_ID_HEADER = "X-Request-ID"


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[no-untyped-def]
        incoming = request.headers.get(REQUEST_ID_HEADER)
        request_id = incoming.strip() if incoming and incoming.strip() else str(uuid.uuid4())
        request.state.request_id = request_id

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id, path=request.url.path)
        try:
            response = await call_next(request)
        finally:
            pass
        response.headers[REQUEST_ID_HEADER] = request_id
        return response


__all__ = ["REQUEST_ID_HEADER", "RequestIDMiddleware"]

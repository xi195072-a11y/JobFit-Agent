"""统一 API 错误契约（Phase 5 §46）。

每个错误响应同时包含：

- `error`：`{code, message, details, request_id, retryable}` —— 前端与机器消费的统一契约；
- `detail`：Phase 1–4 既有字段，**原样保留**以免破坏既有客户端与回归测试。

红线：绝不把 Python traceback / 内部异常类型 / 密钥细节发给浏览器（§46/§40）。
"""

from __future__ import annotations

from enum import Enum
from typing import Any


class ErrorCode(str, Enum):
    """稳定的机器可读错误码（前端按 code 决定 UI 文案与重试策略）。"""

    VALIDATION_FAILED = "VALIDATION_FAILED"
    REQUEST_VALIDATION_FAILED = "REQUEST_VALIDATION_FAILED"
    PARSE_FAILED = "PARSE_FAILED"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    RESERVATION_FAILED = "RESERVATION_FAILED"
    LEASE_LOST = "LEASE_LOST"
    CONFIGURATION_ERROR = "CONFIGURATION_ERROR"
    HTTP_ERROR = "HTTP_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"


#: 可安全重试的错误码（幂等重放 / lease 重领可能成功）。
RETRYABLE_CODES: frozenset[str] = frozenset(
    {ErrorCode.LEASE_LOST.value, ErrorCode.CONFLICT.value}
)

#: 错误响应 OpenAPI 描述（供各端点 responses= 复用，§22）。
ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    404: {"description": "资源不存在（error.code=NOT_FOUND）"},
    409: {"description": "冲突 / 非法状态转移 / lease 丢失（error.code=CONFLICT|LEASE_LOST）"},
    422: {"description": "请求或领域校验失败（error.code=VALIDATION_FAILED）"},
    500: {"description": "内部错误（不泄露 traceback）"},
    503: {"description": "依赖不可用 / 未配置凭证（error.code=CONFIGURATION_ERROR）"},
}


def error_payload(
    *,
    code: str,
    message: str,
    details: Any = None,
    request_id: str | None = None,
    detail: Any = None,
) -> dict[str, Any]:
    """构造统一错误响应体（保留 `detail` 向后兼容）。"""
    return {
        "detail": message if detail is None else detail,
        "error": {
            "code": code,
            "message": message,
            "details": details,
            "request_id": request_id,
            "retryable": code in RETRYABLE_CODES,
        },
    }


__all__ = ["ERROR_RESPONSES", "RETRYABLE_CODES", "ErrorCode", "error_payload"]

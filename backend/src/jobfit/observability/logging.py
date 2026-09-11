"""structlog 配置：默认 PII-safe。

禁止记录：resume/JD 全文、电话、邮箱、API key。
结构化字段（调用方注入）：request_id / analysis_id / phase / worker_id。
"""

from __future__ import annotations

import logging
from typing import Any

import structlog

from jobfit.observability.redact import is_sensitive_key, redact_text


def _redact_processor(
    _logger: Any, _method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in event_dict.items():
        if is_sensitive_key(key):
            out[key] = "[REDACTED]"
            continue
        if isinstance(value, str):
            out[key] = redact_text(value)
        else:
            out[key] = value
    return out


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(level=level.upper(), format="%(message)s")
    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        _redact_processor,
        structlog.processors.JSONRenderer(),
    ]
    structlog.configure(
        processors=processors,
        # 走 stdlib logging：便于统一 handler / 审计捕获（仍为默认零外部依赖）
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.make_filtering_bound_logger(level.upper()),
        cache_logger_on_first_use=True,
    )


def get_logger(*, name: str = "jobfit") -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)

"""PII 脱敏 + PII-safe 日志（ADR-013/ADR-020）。"""

from __future__ import annotations

from jobfit.observability.logging import _redact_processor, configure_logging
from jobfit.observability.redact import is_sensitive_key, redact_text


def test_redact_email() -> None:
    assert "xiao.li@example.com" not in redact_text("contact xiao.li@example.com now")


def test_redact_phone() -> None:
    out = redact_text("call me 13800138000 please")
    assert "13800138000" not in out


def test_redact_url() -> None:
    assert "http://secret.example/a" not in redact_text("see http://secret.example/a end")


def test_redact_long_token_like_string() -> None:
    token = "sk-0123456789abcdef0123456789abcdef0123456789abcdef"
    assert token not in redact_text(f"key={token}")


def test_sensitive_keys_masked_by_processor() -> None:
    event = {"api_key": "sk-abc", "analysis_id": "a-1", "note": "ok"}
    out = _redact_processor(None, "info", dict(event))
    assert out["api_key"] == "[REDACTED]"
    assert out["analysis_id"] == "a-1"
    assert is_sensitive_key("deepseek_api_key") is True


def test_text_redacted_in_processor() -> None:
    out = _redact_processor(None, "info", {"msg": "sent to a@b.com"})
    assert "a@b.com" not in out["msg"]


def test_configure_logging_runs() -> None:
    configure_logging("INFO")  # 不应抛错
    from jobfit.observability.logging import get_logger

    get_logger(name="test").info("health", phase="unit")

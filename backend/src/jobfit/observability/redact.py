"""PII 脱敏（与 decision-trace excerpt 共用同一实现思路；ADR-013/020）。"""

from __future__ import annotations

import re

REDACTED = "[REDACTED]"

_PHONE_RE = re.compile(
    r"(?<!\w)(?:\+?86[- ]?)?1[3-9]\d{9}|(?:\+\d{1,3}[- ]?)?(?:\(?\d{2,4}\)?[- ]?)\d{3,4}[- ]?\d{3,4}"
)
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_URL_RE = re.compile(r"https?://[^\s<>\"']+")
_LONG_RE = re.compile(r"\b[A-Za-z0-9_\-\.]{40,}\b")  # 疑似 token/key/long hash


def redact_text(text: str) -> str:
    """把电话/邮箱/URL/疑似长密钥替换为 [REDACTED]。

    用于日志与 trace 文本；不承诺覆盖任意人名（本项目只保证
    日志不打印简历/JD 全文、电话、邮箱、API key）。
    """
    out = _EMAIL_RE.sub(REDACTED, text)
    out = _PHONE_RE.sub(REDACTED, out)
    out = _URL_RE.sub(REDACTED, out)
    out = _LONG_RE.sub(REDACTED, out)
    return out


SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "deepseek_api_key",
        "openai_api_key",
        "password",
        "passwd",
        "secret",
        "authorization",
        "pii_enc_key",
        "phone",
        "email",
        "resume_text",
        "jd_text",
        "full_dump",
        "content",
        "config_snapshot",
    }
)


def is_sensitive_key(key: str) -> bool:
    return key.lower() in SENSITIVE_KEYS

"""LLM 结构化输出解析 + Pydantic validation（ADR-002）。

调用链：provider HTTP -> content text -> parse_structured_json -> T。
任何失败抛 StructuredOutputError，由上层降级（UNKNOWN/UNAVAILABLE），
禁止把未校验 dict 放入 domain layer。
"""

from __future__ import annotations

import json
import re
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from jobfit.core.errors import StructuredOutputError

T = TypeVar("T", bound=BaseModel)

_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


def extract_json_text(content: str) -> str:
    text = content.strip()
    m = _FENCE_RE.match(text)
    if m:
        return m.group(1).strip()
    if text.startswith("{") and text.endswith("}"):
        return text
    # 宽松回退：截取首个 { 到最后一个 } 之间的内容
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise StructuredOutputError("no JSON object found in model output")
    return text[start : end + 1]


def parse_structured_json(content: str, schema: type[T]) -> T:
    """把模型文本解析并校验为 schema 实例。校验失败即抛错（供 repair/degrade）。"""
    try:
        payload = json.loads(extract_json_text(content))
    except json.JSONDecodeError as exc:
        raise StructuredOutputError(f"invalid JSON from model: {exc}") from exc
    try:
        return schema.model_validate(payload)
    except ValidationError as exc:
        raise StructuredOutputError(f"schema validation failed: {exc}") from exc

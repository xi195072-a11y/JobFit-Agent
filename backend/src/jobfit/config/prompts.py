"""Prompt artifact 加载与版本化（architecture §12 config/prompts.py，ADR-019）。

prompt_version = 模板文件内容的 SHA-256（与 config/loader.py 的口径一致：文件名字节 + 文件内容）。
模板是纯文本，占位符使用 ``{{name}}``（不做模板引擎求值，避免引入额外依赖与注入面）。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from jobfit.core.errors import ConfigurationError

RESUME_PROMPT = "extract_resume.j2"
JD_PROMPT = "extract_jd.j2"
# schema 校验失败时的纠错重试模板（ADR-001/ADR-018，`max_repair_retries`）。
# 不参与 `combined_version()`：artifact identity 仍由两个抽取模板决定。
REPAIR_PROMPT = "repair.j2"


@dataclass(frozen=True)
class PromptTemplate:
    name: str
    text: str
    version: str

    def render(self, **values: str) -> str:
        """把 ``{{key}}`` 替换为给定值；未提供的占位符原样保留（显式可见）。"""
        out = self.text
        for key, value in values.items():
            out = out.replace("{{" + key + "}}", value)
        return out


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.name.encode("utf-8"))
    digest.update(path.read_bytes())
    return f"h:{digest.hexdigest()[:16]}"


class PromptRegistry:
    """一次加载 prompts 目录；版本由内容决定，内容变更即版本变更。"""

    def __init__(self, prompts_dir: Path) -> None:
        self.prompts_dir = Path(prompts_dir)
        self._cache: dict[str, PromptTemplate] = {}

    def get(self, name: str) -> PromptTemplate:
        if name in self._cache:
            return self._cache[name]
        path = self.prompts_dir / name
        if not path.is_file():
            raise ConfigurationError(f"missing prompt template: {path}")
        text = path.read_text(encoding="utf-8")
        if "{{chunks}}" not in text:
            raise ConfigurationError(f"prompt template must contain {{{{chunks}}}} placeholder: {path}")
        template = PromptTemplate(name=name, text=text, version=_hash_file(path))
        self._cache[name] = template
        return template

    def versions(self) -> dict[str, str]:
        return {name: self.get(name).version for name in (RESUME_PROMPT, JD_PROMPT)}

    def combined_version(self) -> str:
        """两个抽取模板的共同版本：任一模板变化 => 版本变化。"""
        parts = "".join(f"{name}={self.get(name).version}|" for name in (RESUME_PROMPT, JD_PROMPT))
        return f"h:{hashlib.sha256(parts.encode('utf-8')).hexdigest()[:16]}"

"""ingestion（upload 阶段专用）：validate + dedupe + storage。

不属于 workflow；architecture v0.2.3 规定图从 load_documents 开始。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from jobfit.core.errors import ValidationFailed

MAX_SNIFF_BYTES = 8 * 1024


@dataclass(frozen=True)
class SniffedFile:
    kind: str  # resume | jd
    mime_type: str
    actual_kind: str  # pdf | docx | txt


_PDF_MAGIC = b"%PDF"
_ZIP_MAGIC = b"PK\x03\x04"


def sniff_file(head: bytes, *, declared_kind: str, filename: str) -> SniffedFile:
    """magic bytes 嗅探 + 类型白名单。不信扩展名（ADR-011/ADR-021）。"""
    if declared_kind not in {"resume", "jd"}:
        raise ValidationFailed(f"kind must be resume|jd, got {declared_kind!r}")

    if head.startswith(_PDF_MAGIC):
        return SniffedFile(kind=declared_kind, mime_type="application/pdf", actual_kind="pdf")
    if head.startswith(_ZIP_MAGIC) and filename.lower().endswith(".docx"):
        return SniffedFile(
            kind=declared_kind, mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            actual_kind="docx",
        )
    # 纯文本兜底：必须能按 UTF-8 解码，且样本不含 NUL、可打印率足够高
    if b"\x00" in head:
        raise ValidationFailed(
            "unsupported file: binary content (NUL byte) is not accepted"
        )
    try:
        decoded = head.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValidationFailed(
            "unsupported file: only PDF(text-layer)/DOCX/TXT accepted (magic bytes mismatch)"
        ) from exc
    if decoded:
        printable = sum(1 for ch in decoded if ch.isprintable() or ch in "\t\n\r")
        if printable / len(decoded) < 0.9:
            raise ValidationFailed(
                "unsupported file: mostly non-printable content rejected (not a text document)"
            )
    return SniffedFile(kind=declared_kind, mime_type="text/plain", actual_kind="txt")


def compute_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def enforce_size(data_size: int, max_bytes: int) -> None:
    if data_size > max_bytes:
        raise ValidationFailed(f"file too large: {data_size} > max {max_bytes} bytes")


def read_with_limit(content_iter, max_bytes: int) -> bytes:
    """流式读取到上限（防 oversized upload，见 architecture §8.2/§8.6）。"""
    chunks: list[bytes] = []
    total = 0
    for chunk in content_iter:
        total += len(chunk)
        if total > max_bytes:
            raise ValidationFailed(f"file too large (>{max_bytes} bytes)")
        chunks.append(chunk)
        if len(chunks) > 1_000_000:
            raise ValidationFailed("file too fragmented")
    return b"".join(chunks)


class LocalStorage:
    """本地文件存储（owner: upload stage）。路径以 sha256 组织。"""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def store(self, sha256: str, data: bytes) -> str:
        rel = Path(sha256[:2]) / f"{sha256}.bin"
        path = self.root / rel
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        return str(rel)

    def load(self, rel: str) -> bytes:
        path = (self.root / rel).resolve()
        root = self.root.resolve()
        if root not in path.parents and path != root:
            raise ValidationFailed(f"storage path escapes root: {rel}")  # 防路径穿越
        if not path.is_file():
            raise ValidationFailed(f"stored file missing: {rel}")
        return path.read_bytes()

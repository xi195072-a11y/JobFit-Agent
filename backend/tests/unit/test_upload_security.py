"""unit: 上传安全审计（Phase 6 §55/§56）—— 无 DB，纯函数 + 临时目录。

覆盖：magic bytes 嗅探（不信扩展名）、NUL/可打印率、大小上限、碎片化上限、
本地存储路径穿越防护、以及"扩展名伪装"回归（Phase 1 已修复的问题不得回归）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jobfit.core.errors import ValidationFailed
from jobfit.ingestion.validate import (
    LocalStorage,
    compute_sha256,
    enforce_size,
    read_with_limit,
    sniff_file,
)

# ---------------------------------------------------------------- magic bytes


def test_pdf_magic_is_accepted_and_extension_is_not_trusted() -> None:
    sniffed = sniff_file(b"%PDF-1.7\n...", declared_kind="resume", filename="cv.pdf")
    assert sniffed.actual_kind == "pdf"
    assert sniffed.mime_type == "application/pdf"

    # 扩展名伪装为 .pdf 但内容是文本 => 按文本处理，绝不因为扩展名就当成 PDF
    spoofed = sniff_file("纯文本内容".encode(), declared_kind="resume", filename="fake.pdf")
    assert spoofed.actual_kind == "txt"


def test_docx_zip_magic_requires_docx_extension() -> None:
    ok = sniff_file(b"PK\x03\x04rest", declared_kind="jd", filename="jd.docx")
    assert ok.actual_kind == "docx"

    # ZIP 头但扩展名不是 .docx => 落到文本分支 => 二进制被拒
    with pytest.raises(ValidationFailed):
        sniff_file(b"PK\x03\x04rest", declared_kind="jd", filename="jd.zip")


def test_plain_text_is_accepted() -> None:
    sniffed = sniff_file("李明\nPython 熟练\n".encode(), declared_kind="resume", filename="r.txt")
    assert sniffed.actual_kind == "txt"
    assert sniffed.kind == "resume"


# ---------------------------------------------------------------- binary / NUL


def test_nul_byte_content_is_rejected() -> None:
    with pytest.raises(ValidationFailed, match="NUL"):
        sniff_file(b"abc\x00def", declared_kind="resume", filename="x.txt")


def test_mostly_non_printable_content_is_rejected() -> None:
    payload = bytes([1, 2, 3, 4, 5, 6, 7, 8] * 32)
    with pytest.raises(ValidationFailed, match="non-printable"):
        sniff_file(payload, declared_kind="resume", filename="x.txt")


def test_invalid_utf8_is_rejected() -> None:
    with pytest.raises(ValidationFailed):
        sniff_file(b"\xff\xfe\xfa\xfb\xfc", declared_kind="resume", filename="x.txt")


def test_invalid_declared_kind_is_rejected() -> None:
    with pytest.raises(ValidationFailed, match="kind must be"):
        sniff_file(b"%PDF-1.7", declared_kind="cover_letter", filename="x.pdf")


# ---------------------------------------------------------------- size limits


def test_enforce_size_boundary() -> None:
    enforce_size(100, 100)  # 边界等于上限 => 允许
    with pytest.raises(ValidationFailed, match="too large"):
        enforce_size(101, 100)


def test_read_with_limit_stops_oversized_stream() -> None:
    def chunks(count: int, size: int):
        for _ in range(count):
            yield b"x" * size

    assert read_with_limit(chunks(2, 10), 20) == b"x" * 20
    with pytest.raises(ValidationFailed, match="too large"):
        read_with_limit(chunks(3, 10), 20)


def test_read_with_limit_rejects_absurd_fragmentation() -> None:
    def tiny_chunks():
        for _ in range(1_000_002):
            yield b"a"

    with pytest.raises(ValidationFailed, match="fragmented"):
        read_with_limit(tiny_chunks(), 10_000_000)


# ---------------------------------------------------------------- storage


def test_storage_round_trip_and_sha256_layout(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    data = b"hello"
    digest = compute_sha256(data)
    rel = storage.store(digest, data)
    assert rel.startswith(digest[:2])
    assert storage.load(rel) == data
    # 重复 store 幂等（不报错、内容一致）
    assert storage.store(digest, data) == rel


def test_storage_rejects_path_traversal(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    for evil in ("../secrets.txt", "..\\secrets.txt", "../../etc/passwd"):
        with pytest.raises(ValidationFailed):
            storage.load(evil)


def test_storage_missing_file_is_explicit_failure(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    with pytest.raises(ValidationFailed, match="missing"):
        storage.load("ab/abcdef.bin")

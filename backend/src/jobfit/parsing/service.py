"""parse 阶段 service：storage 读取 → 类型嗅探 → parser 分派 → chunker → 不可变 artifact 落库。

事务边界：本模块只做短事务，不执行 LLM/外部 HTTP。
parser_version 覆盖 parser 代码版本 + 第三方库版本 + 解析参数 + chunker 配置，
因此 chunk 结构变化必然产生新的 parse artifact，不会覆盖历史 artifact。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from jobfit.config.settings import Settings
from jobfit.core.errors import ParseFailure
from jobfit.db import models
from jobfit.db.repositories import artifacts as artifacts_repo
from jobfit.db.repositories import parse_artifacts as pa_repo
from jobfit.ingestion.validate import LocalStorage, sniff_file
from jobfit.parsing import docx as docx_parser
from jobfit.parsing import pdf as pdf_parser
from jobfit.parsing import txt as txt_parser
from jobfit.parsing.base import PARSER_CODE_VERSION, ParsedText
from jobfit.parsing.chunker import CHUNK_OVERLAP, CHUNK_SIZE, CHUNKER_VERSION, chunk_text


def parse_version() -> str:
    """确定性 parse artifact 版本（同一环境 + 同一输入 => 同一值）。"""
    return (
        f"{PARSER_CODE_VERSION}+{CHUNKER_VERSION}"
        f"+size{CHUNK_SIZE}+ovl{CHUNK_OVERLAP}"
        f"+pypdf{pdf_parser.PDF_LIB_VERSION}+docx{docx_parser.DOCX_LIB_VERSION}"
    )


@dataclass(frozen=True)
class ParseOutcome:
    parsed_document_id: uuid.UUID
    parser_version: str
    chunk_count: int
    chunks_created: int
    actual_kind: str
    reused_artifact: bool


def _dispatch(actual_kind: str, data: bytes) -> ParsedText:
    if actual_kind == "pdf":
        return pdf_parser.parse_pdf(data)
    if actual_kind == "docx":
        return docx_parser.parse_docx(data)
    if actual_kind == "txt":
        return txt_parser.parse_txt(data)
    raise ParseFailure(f"unsupported actual kind: {actual_kind}")


def parse_document(
    session: Session, settings: Settings, document: models.Document
) -> ParseOutcome:
    """解析并（按需）创建 parse artifact + chunks；相同 (document, parser_version) 复用。"""
    data = LocalStorage(settings.storage_dir).load(document.storage_path)
    sniffed = sniff_file(
        data[: 8 * 1024], declared_kind=document.kind, filename=document.original_filename
    )
    parser_version = parse_version()

    existing = pa_repo.get_parsed_document(
        session, document_id=document.id, parser_version=parser_version
    )
    if existing is not None:
        chunks = pa_repo.list_chunks(session, existing.id)
        return ParseOutcome(
            parsed_document_id=existing.id,
            parser_version=parser_version,
            chunk_count=len(chunks),
            chunks_created=0,
            actual_kind=sniffed.actual_kind,
            reused_artifact=True,
        )

    parsed = _dispatch(sniffed.actual_kind, data)
    pages_payload = {
        "pages": [span.as_dict() for span in parsed.pages],
        "parser": parsed.parser_meta,
        "actual_kind": parsed.actual_kind,
        "char_length": len(parsed.text),
    }
    row, created = artifacts_repo.get_or_create_parsed_document(
        session,
        document_id=document.id,
        parser_version=parser_version,
        text=parsed.text,
        pages=pages_payload,
    )
    specs = chunk_text(parsed.text, parsed.pages)
    chunks_created = pa_repo.create_or_reuse_chunks(
        session, parsed_document_id=row.id, specs=specs
    )
    chunk_count = len(pa_repo.list_chunks(session, row.id))
    return ParseOutcome(
        parsed_document_id=row.id,
        parser_version=parser_version,
        chunk_count=chunk_count,
        chunks_created=chunks_created,
        actual_kind=sniffed.actual_kind,
        reused_artifact=not created,
    )

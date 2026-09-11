"""JD DTO → 领域 JDProfile（含 grounding 与 UNKNOWN 语义）。

- 每个 requirement 必须能回溯到 source_quote 所在 chunk；否则丢弃 + warning。
- preferred_qualifications 一律 is_hard=False（确定性规则，不依赖模型判断）。
- 缺失信息保持 None / 空集合：没有写 visa/salary/degree => UNKNOWN（不是 False）。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from jobfit.core.enums import DocumentKind
from jobfit.core.schemas import JDProfile, JDRequirement
from jobfit.extraction.anchors import ChunkLike, find_literal, ground_quotes
from jobfit.extraction.dto import LLMJdProfile


@dataclass
class JdBuildResult:
    profile: JDProfile
    warnings: list[str] = field(default_factory=list)


def _verify_scalar(
    *,
    name: str,
    value: str | None,
    chunks: Sequence[ChunkLike],
    parsed_document_id: str,
    warnings: list[str],
) -> str | None:
    if value is None:
        return None
    anchor, _ = find_literal(
        literal=value,
        chunks=chunks,
        parsed_document_id=parsed_document_id,
        doc_kind=DocumentKind.JD,
    )
    if anchor is None:
        warnings.append(f"ungrounded_scalar:{name}")
        return None
    return value


def build_jd_profile(
    *,
    dto: LLMJdProfile,
    document_id: str,
    parsed_document_id: str,
    chunks: Sequence[ChunkLike],
) -> JdBuildResult:
    warnings: list[str] = list(dto.extraction_warnings)

    def _requirements(items, *, prefix: str, force_not_hard: bool) -> list[JDRequirement]:
        out: list[JDRequirement] = []
        for index, item in enumerate(items):
            grounding = ground_quotes(
                quotes=[item.source_quote],
                chunks=chunks,
                parsed_document_id=parsed_document_id,
                doc_kind=DocumentKind.JD,
            )
            if not grounding.grounded:
                warnings.append(f"ungrounded_item:{prefix}[{index}]")
                continue
            out.append(
                JDRequirement(
                    req_type=item.req_type,
                    operator=item.operator,
                    value=dict(item.value),
                    weight=1.0,
                    is_hard=False if force_not_hard else bool(item.is_hard),
                    source_text=item.source_quote,
                    anchors=grounding.anchors,
                )
            )
        return out

    requirements = _requirements(dto.requirements, prefix="requirements", force_not_hard=False)
    preferred = _requirements(
        dto.preferred_qualifications, prefix="preferred_qualifications", force_not_hard=True
    )

    responsibilities: list[str] = []
    for index, value in enumerate(dto.responsibilities):
        anchor, _ = find_literal(
            literal=value,
            chunks=chunks,
            parsed_document_id=parsed_document_id,
            doc_kind=DocumentKind.JD,
        )
        if anchor is None:
            warnings.append(f"ungrounded_scalar:responsibilities[{index}]")
            continue
        responsibilities.append(value)

    profile = JDProfile(
        document_id=document_id,
        parsed_document_id=parsed_document_id,
        company=_verify_scalar(
            name="company",
            value=dto.company,
            chunks=chunks,
            parsed_document_id=parsed_document_id,
            warnings=warnings,
        ),
        role_title=_verify_scalar(
            name="role_title",
            value=dto.role_title,
            chunks=chunks,
            parsed_document_id=parsed_document_id,
            warnings=warnings,
        ),
        location=_verify_scalar(
            name="location",
            value=dto.location,
            chunks=chunks,
            parsed_document_id=parsed_document_id,
            warnings=warnings,
        ),
        requirements=requirements,
        preferred_qualifications=preferred,
        responsibilities=responsibilities,
        extraction_warnings=warnings,
    )
    return JdBuildResult(profile=profile, warnings=warnings)

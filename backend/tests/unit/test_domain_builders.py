# mypy: disable-error-code="arg-type,attr-defined,assignment,union-attr,index"
"""DTO → 领域 artifact 构建单测：grounding 失败即丢弃 + warning，绝不补全/猜测。"""

from __future__ import annotations

from jobfit.core.enums import DocumentKind
from jobfit.extraction.dto import LLMJdProfile, LLMResumeProfile
from jobfit.extraction.jd import build_jd_profile
from jobfit.extraction.resume import build_resume_profile
from support import chunks_from_text, read_fixture_bytes

RESUME_TEXT = read_fixture_bytes("resume.txt").decode("utf-8")
JD_TEXT = read_fixture_bytes("jd.txt").decode("utf-8")


def test_resume_builder_keeps_only_grounded_facts() -> None:
    dto = LLMResumeProfile.model_validate(
        {
            "name": "张三",
            "email": "zhangsan@example.com",
            "location": None,
            "education": [
                {
                    "school": "南京大学",
                    "degree": "本科",
                    "major": "计算机科学与技术",
                    "evidence_quotes": ["南京大学  计算机科学与技术  本科"],
                }
            ],
            "experiences": [
                {
                    "company": "腾讯科技（深圳）",
                    "title": "后端开发工程师",
                    "bullets": ["使用 Python 与 FastAPI 开发推荐服务接口"],
                    "evidence_quotes": ["腾讯科技（深圳）  后端开发工程师"],
                }
            ],
            "skills": [{"skill_raw": "PostgreSQL", "evidence_quotes": ["Python, FastAPI, PostgreSQL, Docker"]}],
            "languages": ["英语 CET-6"],
            "certifications": [],
        }
    )
    result = build_resume_profile(
        dto=dto,
        document_id="doc-1",
        parsed_document_id="parsed-1",
        chunks=chunks_from_text(RESUME_TEXT),
    )
    profile = result.profile
    assert profile.name == "张三"
    assert profile.email == "zhangsan@example.com"
    assert profile.location is None  # 未提供 => UNKNOWN，不猜
    assert profile.education[0].school == "南京大学"
    assert profile.education[0].gpa is None  # 未提取 => None
    assert profile.experiences[0].bullets == ["使用 Python 与 FastAPI 开发推荐服务接口"]
    assert profile.skills[0].skill_raw == "PostgreSQL"
    assert profile.skills[0].evidence_ids
    assert profile.languages == ["英语 CET-6"]


def test_resume_builder_drops_ungrounded_scalar_with_warning() -> None:
    dto = LLMResumeProfile.model_validate({"name": "李四"})
    result = build_resume_profile(
        dto=dto,
        document_id="doc-1",
        parsed_document_id="parsed-1",
        chunks=chunks_from_text(RESUME_TEXT),
    )
    assert result.profile.name is None
    assert "ungrounded_scalar:name" in result.warnings


def test_resume_builder_drops_ungrounded_items_and_bullets() -> None:
    dto = LLMResumeProfile.model_validate(
        {
            "education": [
                {"school": "不存在的大学", "evidence_quotes": ["不存在的大学"]},
            ],
            "experiences": [
                {
                    "company": "腾讯科技（深圳）",
                    "bullets": ["这条 bullet 不在原文中"],
                    "evidence_quotes": ["腾讯科技（深圳）"],
                }
            ],
            "skills": [{"skill_raw": "Rust", "evidence_quotes": ["Rust"]}],
            "certifications": ["PMP"],
        }
    )
    result = build_resume_profile(
        dto=dto,
        document_id="doc-1",
        parsed_document_id="parsed-1",
        chunks=chunks_from_text(RESUME_TEXT),
    )
    assert result.profile.education == []
    assert result.profile.skills == []
    assert result.profile.certifications == []
    assert result.profile.experiences[0].bullets == []
    assert "ungrounded_item:education[0]" in result.warnings
    assert "ungrounded_item:skills[0]" in result.warnings
    assert "ungrounded_bullet:experiences[0][0]" in result.warnings
    assert "ungrounded_scalar:certifications[0]" in result.warnings


def test_jd_builder_grounds_requirements_and_forces_preferred_not_hard() -> None:
    dto = LLMJdProfile.model_validate(
        {
            "company": "腾讯科技",
            "role_title": "高级后端开发工程师",
            "location": "深圳",
            "requirements": [
                {
                    "req_type": "degree",
                    "operator": ">=",
                    "value": {"degree": "本科"},
                    "is_hard": True,
                    "source_quote": "本科及以上学历，计算机相关专业",
                }
            ],
            "preferred_qualifications": [
                {
                    "req_type": "other",
                    "is_hard": True,
                    "source_quote": "有大规模分布式系统经验",
                }
            ],
            "responsibilities": ["负责推荐系统后端服务设计与开发"],
        }
    )
    result = build_jd_profile(
        dto=dto,
        document_id="doc-2",
        parsed_document_id="parsed-2",
        chunks=chunks_from_text(JD_TEXT),
    )
    profile = result.profile
    assert profile.company == "腾讯科技"
    assert profile.requirements[0].is_hard is True
    assert profile.requirements[0].anchors
    assert profile.preferred_qualifications[0].is_hard is False  # 确定性规则覆盖
    assert profile.responsibilities == ["负责推荐系统后端服务设计与开发"]


def test_jd_builder_drops_ungrounded_requirement() -> None:
    dto = LLMJdProfile.model_validate(
        {
            "requirements": [
                {
                    "req_type": "years_experience",
                    "value": {"years": 10},
                    "is_hard": True,
                    "source_quote": "需要 10 年工作经验",
                }
            ]
        }
    )
    result = build_jd_profile(
        dto=dto,
        document_id="doc-2",
        parsed_document_id="parsed-2",
        chunks=chunks_from_text(JD_TEXT),
    )
    assert result.profile.requirements == []
    assert "ungrounded_item:requirements[0]" in result.warnings


def test_jd_builder_unknown_when_absent() -> None:
    dto = LLMJdProfile.model_validate({"requirements": []})
    result = build_jd_profile(
        dto=dto,
        document_id="doc-2",
        parsed_document_id="parsed-2",
        chunks=chunks_from_text(JD_TEXT),
    )
    assert result.profile.company is None
    assert result.profile.requirements == []
    assert DocumentKind.JD.value == "jd"

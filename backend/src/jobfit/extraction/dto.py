"""LLM 抽取 DTO：只描述"模型被允许返回什么"。

领域 schema（core/schemas.py 的 ResumeProfile/JDProfile）不因 LLM 而扩张；
LLM 返回先经这里严格校验，再由 anchors.py 做确定性 grounding 后映射为领域 artifact。

`evidence_quotes` / `source_quote` 强制 min_length=1：没有逐字引用的条目直接校验失败，
而不是让模型"补一个"。
"""

from __future__ import annotations

from datetime import date
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from jobfit.core.enums import RequirementType


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LLMEducation(_StrictModel):
    school: str | None = None
    degree: str | None = None
    major: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    gpa: float | None = None
    honor: str | None = None
    evidence_quotes: list[str] = Field(min_length=1)


class LLMExperience(_StrictModel):
    company: str | None = None
    title: str | None = None
    location: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    bullets: list[str] = Field(default_factory=list)
    evidence_quotes: list[str] = Field(min_length=1)


class LLMSkill(_StrictModel):
    skill_raw: str = Field(min_length=1)
    category: str | None = None
    proficiency: str | None = None
    evidence_quotes: list[str] = Field(min_length=1)


class LLMResumeProfile(_StrictModel):
    SCHEMA_VERSION: ClassVar[str] = "llm_resume.v1"

    name: str | None = None
    phone: str | None = None
    email: str | None = None
    location: str | None = None
    education: list[LLMEducation] = Field(default_factory=list)
    experiences: list[LLMExperience] = Field(default_factory=list)
    skills: list[LLMSkill] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    extraction_warnings: list[str] = Field(default_factory=list)


class LLMRequirement(_StrictModel):
    req_type: RequirementType
    operator: str = "unspecified"
    value: dict = Field(default_factory=dict)
    is_hard: bool = False
    source_quote: str = Field(min_length=1)


class LLMJdProfile(_StrictModel):
    SCHEMA_VERSION: ClassVar[str] = "llm_jd.v1"

    company: str | None = None
    role_title: str | None = None
    location: str | None = None
    requirements: list[LLMRequirement] = Field(default_factory=list)
    preferred_qualifications: list[LLMRequirement] = Field(default_factory=list)
    responsibilities: list[str] = Field(default_factory=list)
    extraction_warnings: list[str] = Field(default_factory=list)

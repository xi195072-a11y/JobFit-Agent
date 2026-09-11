"""测试支持设施（仅存在于 tests/，绝不进入 production runtime path）。

- DeterministicProvider：test-only LLMProvider 实现，返回固定 payload（不是生产 fake）。
  生产代码路径只接受真实 provider（见 llm/factory.py）；测试通过 FastAPI
  dependency_overrides 注入。
- 提供与 tests/fixtures/*.txt 内容严格对应的 payload（quotes 必须逐字可定位）。
- Phase 4：`critique_payload` 支持 dict 或 callable(prompt)->dict，callable 可从
  prompt 的受控 <data> 块提取真实 chunk/requirement id，构造可验证通过的 critique。
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel
from sqlalchemy.orm import Session

from jobfit.config.settings import Settings
from jobfit.core.enums import AnalysisStatus
from jobfit.critique.schema import CritiqueSchema
from jobfit.db import models
from jobfit.db.repositories import analyses as analyses_repo
from jobfit.extraction.dto import LLMJdProfile, LLMResumeProfile
from jobfit.ingestion.service import ingest_document

T = TypeVar("T", bound=BaseModel)

FIXTURES = Path(__file__).resolve().parent / "fixtures"

RESUME_PAYLOAD: dict = {
    "name": "张三",
    "phone": "13800138000",
    "email": "zhangsan@example.com",
    "location": None,
    "education": [
        {
            "school": "南京大学",
            "degree": "本科",
            "major": "计算机科学与技术",
            "start_date": "2018-09-01",
            "end_date": "2022-06-01",
            "gpa": 3.7,
            "honor": "校级一等奖学金",
            "evidence_quotes": [
                "2018-09 - 2022-06  南京大学  计算机科学与技术  本科  绩点 3.7",
                "荣誉: 校级一等奖学金",
            ],
        }
    ],
    "experiences": [
        {
            "company": "腾讯科技（深圳）",
            "title": "后端开发工程师",
            "location": None,
            "start_date": "2022-07-01",
            "end_date": "2024-12-01",
            "bullets": [
                "使用 Python 与 FastAPI 开发推荐服务接口",
                "负责 PostgreSQL 数据建模与查询优化",
            ],
            "evidence_quotes": ["2022-07 - 2024-12  腾讯科技（深圳）  后端开发工程师"],
        }
    ],
    "skills": [
        {
            "skill_raw": "Python",
            "category": None,
            "proficiency": "熟练",
            "evidence_quotes": ["Python 熟练"],
        },
        {
            "skill_raw": "FastAPI",
            "category": None,
            "proficiency": None,
            "evidence_quotes": ["Python, FastAPI, PostgreSQL, Docker"],
        },
        {
            "skill_raw": "PostgreSQL",
            "category": None,
            "proficiency": None,
            "evidence_quotes": ["Python, FastAPI, PostgreSQL, Docker"],
        },
    ],
    "languages": ["英语 CET-6"],
    "certifications": [],
    "extraction_warnings": [],
}

JD_PAYLOAD: dict = {
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
        },
        {
            "req_type": "years_experience",
            "operator": ">=",
            "value": {"years": 3},
            "is_hard": True,
            "source_quote": "3 年以上后端开发经验",
        },
        {
            "req_type": "skill",
            "operator": "has",
            "value": {"skill": "Python"},
            "is_hard": True,
            "source_quote": "熟悉 Python 与 FastAPI",
        },
        {
            "req_type": "skill",
            "operator": "has",
            "value": {"skill": "PostgreSQL"},
            "is_hard": True,
            "source_quote": "熟悉 PostgreSQL 与 Docker",
        },
    ],
    "preferred_qualifications": [
        {
            "req_type": "other",
            "operator": "has",
            "value": {"note": "大规模分布式系统"},
            "is_hard": True,
            "source_quote": "有大规模分布式系统经验",
        }
    ],
    "responsibilities": ["负责推荐系统后端服务设计与开发"],
    "extraction_warnings": [],
}


@dataclass
class DeterministicProvider:
    """test-only provider：返回固定 payload，并记录调用次数/顺序。

    - resume/jd 走 Phase 2/3 抽取；
    - critique（Phase 4）走 `critique_payload`：dict（原样返回）或
      callable(prompt)->dict（可从 prompt 的 <data> 块提取真实证据 id）。
    """

    resume_payload: dict = field(default_factory=lambda: dict(RESUME_PAYLOAD))
    jd_payload: dict = field(default_factory=lambda: dict(JD_PAYLOAD))
    model_name: str = "test-deterministic"
    calls: list[str] = field(default_factory=list)
    on_call: Callable[[type[BaseModel]], None] | None = None
    critique_payload: dict | Callable[[str], dict] | None = None

    async def complete_text(self, prompt: str, *, max_tokens: int | None = None) -> str:
        del prompt, max_tokens
        self.calls.append("text")
        return json.dumps(self.resume_payload, ensure_ascii=False)

    async def complete_structured(self, prompt: str, *, schema: type[T]) -> T:
        if self.on_call is not None:
            self.on_call(schema)
        if schema is LLMResumeProfile:
            self.calls.append("resume")
            return schema.model_validate(self.resume_payload)  # type: ignore[return-value]
        if schema is LLMJdProfile:
            self.calls.append("jd")
            return schema.model_validate(self.jd_payload)  # type: ignore[return-value]
        if schema is CritiqueSchema:
            self.calls.append("critique")
            if self.critique_payload is None:
                raise AssertionError("critique_payload not configured for DeterministicProvider")
            payload = (
                self.critique_payload(prompt)
                if callable(self.critique_payload)
                else dict(self.critique_payload)
            )
            return schema.model_validate(payload)  # type: ignore[return-value]
        raise AssertionError(f"unexpected schema: {schema}")

    @property
    def call_count(self) -> int:
        return len(self.calls)


def make_document(
    session: Session,
    settings: Settings,
    *,
    kind: str,
    filename: str,
    content: bytes,
) -> models.Document:
    doc, _created, _sniffed = ingest_document(
        session, settings, kind=kind, filename=filename, data=content
    )
    return doc


BACKEND_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = BACKEND_ROOT / "config"


def make_analysis(
    session: Session,
    *,
    resume_document_id: uuid.UUID,
    jd_document_id: uuid.UUID,
    pipeline_version: str = "test-pipeline-1",
    llm_model: str = "test-deterministic",
    embedding_model: str = "hash-ngram-v1",
    resume_profile_id: uuid.UUID | None = None,
    jd_profile_id: uuid.UUID | None = None,
) -> models.Analysis:
    """创建 queued analysis，并固化**真实**的 config_snapshot（Phase 3 执行期只读快照）。"""
    from jobfit.config.loader import ConfigLoader

    versions = ConfigLoader(CONFIG_DIR).resolve_versions(
        pipeline_version=pipeline_version,
        extraction_schema_version="resume.v1|jd.v1",
        llm_model=llm_model,
        embedding_model=embedding_model,
    )
    analysis = analyses_repo.create_analysis(
        session,
        resume_document_id=resume_document_id,
        jd_document_id=jd_document_id,
        pipeline_version=versions.pipeline_version,
        extraction_schema_version=versions.extraction_schema_version,
        prompt_version=versions.prompt_version,
        ruleset_version=versions.ruleset_version,
        scoring_version=versions.scoring_version,
        llm_model=versions.llm_model,
        embedding_model=versions.embedding_model,
        config_snapshot=versions.config_snapshot,
        resume_profile_id=resume_profile_id,
        jd_profile_id=jd_profile_id,
    )
    assert analysis.status == AnalysisStatus.QUEUED.value
    return analysis


def read_fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


@dataclass(frozen=True)
class FakeChunk:
    """grounding 单测用的最小 chunk（duck-typed：id/content/char_start/char_end/page）。"""

    content: str
    char_start: int
    char_end: int
    page: int | None = 1
    id: uuid.UUID = field(default_factory=uuid.uuid4)


def chunks_from_text(text: str, *, page: int = 1) -> list[FakeChunk]:
    return [FakeChunk(content=text, char_start=0, char_end=len(text), page=page)]


def chunks_from_windows(text: str, size: int) -> list[FakeChunk]:
    out: list[FakeChunk] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        out.append(FakeChunk(content=text[start:end], char_start=start, char_end=end))
        start = end
    return out


# ================================================================ Phase 3 fixtures
# 说明：所有 evidence_quotes / source_quote 必须能在对应 fixture 文本中**逐字定位**，
# 否则 grounding 会丢弃该条目（Phase 2 语义）。

RESUME_MATCH_PAYLOAD: dict = {
    "name": "李明",
    "phone": "13900139000",
    "email": "liming@example.com",
    "location": "深圳",
    "education": [
        {
            "school": "华中科技大学",
            "degree": "本科",
            "major": "软件工程",
            "start_date": "2014-09-01",
            "end_date": "2018-06-01",
            "gpa": None,
            "honor": None,
            "evidence_quotes": ["2014-09 - 2018-06  华中科技大学  软件工程  本科"],
        }
    ],
    "experiences": [
        {
            "company": "平安科技",
            "title": "后端开发工程师",
            "location": None,
            "start_date": "2018-07-01",
            "end_date": "2020-06-01",
            "bullets": [],
            "evidence_quotes": ["2018-07 - 2020-06  平安科技  后端开发工程师"],
        },
        {
            "company": "腾讯科技（深圳）",
            "title": "高级后端开发工程师",
            "location": None,
            "start_date": "2020-07-01",
            "end_date": "2024-07-01",
            "bullets": [],
            "evidence_quotes": ["2020-07 - 2024-07  腾讯科技（深圳）  高级后端开发工程师"],
        },
    ],
    "skills": [
        {
            "skill_raw": raw,
            "category": None,
            "proficiency": None,
            "evidence_quotes": ["Python, FastAPI, PostgreSQL, Docker, Redis"],
        }
        for raw in ("Python", "FastAPI", "PostgreSQL", "Docker", "Redis")
    ],
    "languages": ["英语 CET-6"],
    "certifications": [],
    "extraction_warnings": [],
}

RESUME_MISMATCH_PAYLOAD: dict = {
    "name": "王芳",
    "phone": "13700137000",
    "email": "wangfang@example.com",
    "location": "北京",
    "education": [
        {
            "school": "北京信息职业技术学院",
            "degree": "大专",
            "major": "计算机应用",
            "start_date": "2019-09-01",
            "end_date": "2022-06-01",
            "gpa": None,
            "honor": None,
            "evidence_quotes": ["2019-09 - 2022-06  北京信息职业技术学院  计算机应用  大专"],
        }
    ],
    "experiences": [
        {
            "company": "某创业公司",
            "title": "初级开发工程师",
            "location": None,
            "start_date": "2022-07-01",
            "end_date": "2023-07-01",
            "bullets": ["使用 Redis 做缓存优化"],
            "evidence_quotes": ["2022-07 - 2023-07  某创业公司  初级开发工程师"],
        }
    ],
    # 注意：Redis 只出现在经历文本里、**没有**结构化技能行 —— 用于 PARTIAL 证据路径。
    "skills": [
        {"skill_raw": "Java", "category": None, "proficiency": None, "evidence_quotes": ["Java, Spring Boot"]},
        {
            "skill_raw": "Spring Boot",
            "category": None,
            "proficiency": None,
            "evidence_quotes": ["Java, Spring Boot"],
        },
    ],
    "languages": [],
    "certifications": [],
    "extraction_warnings": [],
}

JD_MATCH_PAYLOAD: dict = {
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
        },
        {
            "req_type": "years_experience",
            "operator": ">=",
            "value": {"years": 3},
            "is_hard": True,
            "source_quote": "3 年以上后端开发经验",
        },
        {
            "req_type": "location",
            "operator": "in",
            "value": {"location": "深圳"},
            "is_hard": True,
            "source_quote": "工作地点: 深圳",
        },
        {
            "req_type": "skill",
            "operator": "has",
            "value": {"skill": "Python"},
            "is_hard": True,
            "source_quote": "熟悉 Python",
        },
        {
            "req_type": "skill",
            "operator": "has",
            "value": {"skill": "PostgreSQL"},
            "is_hard": True,
            "source_quote": "熟悉 PostgreSQL",
        },
        {
            "req_type": "language",
            "operator": ">=",
            "value": {"language": "英语", "level": "CET-6"},
            "is_hard": True,
            "source_quote": "英语 CET-6",
        },
    ],
    "preferred_qualifications": [],
    "responsibilities": [],
    "extraction_warnings": [],
}

JD_MISMATCH_PAYLOAD: dict = {
    "company": "某研究院",
    "role_title": "算法工程师",
    "location": "上海",
    "requirements": [
        {
            "req_type": "degree",
            "operator": ">=",
            "value": {"degree": "硕士"},
            "is_hard": True,
            "source_quote": "硕士及以上学历",
        },
        {
            "req_type": "years_experience",
            "operator": ">=",
            "value": {"years": 5},
            "is_hard": True,
            "source_quote": "5 年以上算法开发经验",
        },
        {
            "req_type": "location",
            "operator": "in",
            "value": {"location": "上海"},
            "is_hard": True,
            "source_quote": "工作地点: 上海",
        },
        {
            "req_type": "skill",
            "operator": "has",
            "value": {"skill": "PyTorch"},
            "is_hard": True,
            "source_quote": "熟悉 PyTorch",
        },
    ],
    "preferred_qualifications": [],
    "responsibilities": [],
    "extraction_warnings": [],
}

JD_UNKNOWN_PAYLOAD: dict = {
    "company": "某外企",
    "role_title": "数据工程师",
    "location": None,
    "requirements": [
        # schema 未建模的类别（工作签证）=> 必须 UNKNOWN（绝不交给 LLM 猜）
        {
            "req_type": "other",
            "operator": "has",
            "value": {"note": "有效工作签证"},
            "is_hard": True,
            "source_quote": "需要有效工作签证",
        },
        {
            "req_type": "skill",
            "operator": "has",
            "value": {"skill": "Kubernetes"},
            "is_hard": True,
            "source_quote": "熟悉 Kubernetes",
        },
        {
            "req_type": "skill",
            "operator": "has",
            "value": {"skill": "Redis"},
            "is_hard": True,
            "source_quote": "熟悉 Redis",
        },
    ],
    "preferred_qualifications": [],
    "responsibilities": [],
    "extraction_warnings": [],
}


@dataclass(frozen=True)
class AnalysisPair:
    analysis: models.Analysis
    resume_document: models.Document
    jd_document: models.Document


def make_analysis_pair(
    session: Session,
    settings: Settings,
    *,
    resume_fixture: str,
    jd_fixture: str,
    **analysis_kwargs,
) -> AnalysisPair:
    """用两个 fixture 文件建 document + queued analysis（未绑定 profile）。"""
    resume = make_document(
        session, settings, kind="resume", filename=resume_fixture, content=read_fixture_bytes(resume_fixture)
    )
    jd = make_document(
        session, settings, kind="jd", filename=jd_fixture, content=read_fixture_bytes(jd_fixture)
    )
    analysis = make_analysis(
        session,
        resume_document_id=uuid.UUID(str(resume.id)),
        jd_document_id=uuid.UUID(str(jd.id)),
        **analysis_kwargs,
    )
    return AnalysisPair(analysis=analysis, resume_document=resume, jd_document=jd)


def run_analysis(
    *,
    session_factory,
    settings: Settings,
    analysis_id,
    provider: DeterministicProvider | None = None,
    embedding_provider=None,
    ttl_seconds: int | None = None,
):
    """执行一次完整 analysis（run_analysis_pipeline 的同步包装）。"""
    from jobfit.evidence.embeddings import HashingEmbeddingProvider
    from jobfit.workflow.runner import run_analysis_pipeline

    return asyncio.run(
        run_analysis_pipeline(
            session_factory=session_factory,
            settings=settings,
            provider=provider or DeterministicProvider(),
            embedding_provider=(
                embedding_provider if embedding_provider is not None else HashingEmbeddingProvider()
            ),
            analysis_id=analysis_id,
            ttl_seconds=ttl_seconds,
        )
    )


def run_phase4(
    *,
    session_factory,
    settings: Settings,
    analysis_id,
    provider: DeterministicProvider | None = None,
    ttl_seconds: int | None = None,
):
    """执行 Phase 4 续接（run_phase4_pipeline 的同步包装）。"""
    from jobfit.workflow.runner import run_phase4_pipeline

    return asyncio.run(
        run_phase4_pipeline(
            session_factory=session_factory,
            settings=settings,
            provider=provider,
            analysis_id=analysis_id,
            ttl_seconds=ttl_seconds,
        )
    )


# ================================================================ Phase 4 helpers
# 以下 helper 仅存在于 tests/：从 prompt 的受控 <data> 块提取真实 id，
# 构造能通过 CitationValidator 的 critique payload（或按参数构造攻击样本）。


def extract_prompt_data(prompt: str) -> dict[str, Any]:
    """从 critique prompt 的 `<data>...</data>` 块解析受控 context（test-only）。"""
    m = re.search(r"<data>\n(.*?)\n</data>", prompt, re.DOTALL)
    assert m is not None, "critique prompt missing <data> block"
    return json.loads(m.group(1))


def build_valid_critique_payload(
    prompt: str,
    *,
    unknown_claim: bool = False,
    not_met_claim: bool = False,
    fabricated_chunk_id: str | None = None,
) -> dict[str, Any]:
    """构造 critique payload。

    - 默认引用 prompt 中真实 evidence_pool 的 chunk + 一个 MET requirement
      => 可通过 CitationValidator；
    - unknown_claim=True：对 UNKNOWN requirement 断言 SUPPORTED（injection/越权样本，
      必须被 validator 拒绝）；
    - not_met_claim=True：对 NOT_MET requirement 断言 SUPPORTED（critique 与确定性
      结论矛盾的样本；确定性表必须保持权威，不得被改写）；
    - fabricated_chunk_id：引用池外 chunk（跨 analysis / fabricated 样本，必须被拒）。
    """
    data = extract_prompt_data(prompt)
    pool = list(data.get("evidence_pool", {}).values())
    chunk_ids = [entry.get("source_chunk_id") for entry in pool if entry.get("source_chunk_id")]
    constraints = data.get("hard_constraints", [])
    met_ids = [c["requirement_id"] for c in constraints if c.get("result") == "MET"]
    not_met_ids = [c["requirement_id"] for c in constraints if c.get("result") == "NOT_MET"]
    unknown_ids = [c["requirement_id"] for c in constraints if c.get("result") == "UNKNOWN"]

    if fabricated_chunk_id is not None:
        ref_chunk = fabricated_chunk_id
    elif chunk_ids:
        ref_chunk = chunk_ids[0]
    else:
        ref_chunk = None
    refs = [{"source_chunk_id": ref_chunk}] if ref_chunk is not None else []

    observations: list[dict[str, Any]] = []
    if unknown_claim and unknown_ids:
        observations.append(
            {
                "requirement_id": unknown_ids[0],
                "observation": "该硬条件满足。",
                "claim_type": "supported",
                "evidence_refs": refs,
            }
        )
    elif not_met_claim and not_met_ids:
        observations.append(
            {
                "requirement_id": not_met_ids[0],
                "observation": "该硬条件已满足。",
                "claim_type": "supported",
                "evidence_refs": refs,
            }
        )
    elif met_ids:
        observations.append(
            {
                "requirement_id": met_ids[0],
                "observation": "该硬条件已满足。",
                "claim_type": "supported",
                "evidence_refs": refs,
            }
        )

    payload: dict[str, Any] = {
        "schema_version": "critique.v1",
        "overall_assessment": "候选人总体匹配良好，主要差距在于分布式系统经验。",
        "strengths": (
            [
                {
                    "category": "strength",
                    "claim": "学历满足岗位要求。",
                    "claim_type": "supported",
                    "evidence_refs": refs,
                }
            ]
            if refs
            else []
        ),
        "gaps": [],
        "risks": [],
        "ambiguities": [],
        "questions": [],
        "constraint_observations": observations,
        "skill_observations": [],
        "unknown_acknowledgements": list(unknown_ids),
    }
    return payload

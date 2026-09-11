"""领域枚举。values 即入库字符串（DB 列按此存储）。"""

from __future__ import annotations

from enum import Enum


class Verdict(str, Enum):
    """三值匹配结论：MET/NOT_MET/UNKNOWN（UNKNOWN 不得自动映射）。"""

    MET = "MET"
    NOT_MET = "NOT_MET"
    UNKNOWN = "UNKNOWN"


class ConstraintBasis(str, Enum):
    DETERMINISTIC = "deterministic"
    LLM_EXTRACTED = "llm_extracted"
    HUMAN = "human"


class DocumentKind(str, Enum):
    RESUME = "resume"
    JD = "jd"


class AnalysisStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    # 确定性引擎完成的成功终态（Phase 3）。awaiting_review/finalized/rejected
    # 属于 HITL 阶段（ADR-009），在引入人工评审前不会被写入。
    SUCCEEDED = "succeeded"
    AWAITING_REVIEW = "awaiting_review"
    FINALIZED = "finalized"
    REJECTED = "rejected"
    FAILED = "failed"


class RequirementType(str, Enum):
    DEGREE = "degree"
    YEARS_EXPERIENCE = "years_experience"
    SKILL = "skill"
    CERTIFICATION = "certification"
    LANGUAGE = "language"
    LOCATION = "location"
    SECURITY_CLEARANCE = "security_clearance"
    OTHER = "other"


class SkillMatchStatus(str, Enum):
    """技能匹配结论。

    与 requirement verdict 的映射：MATCHED↔MET、PARTIAL↔部分证据、UNKNOWN↔UNKNOWN。
    `MISSING` 对应 NO_MATCH，但**只有在存在显式否定证据时**才允许写；
    当前 resume schema 无法表达"候选人没有某技能"的正面否定事实，
    因此 Phase 3 不会产生 MISSING（absence of evidence ≠ FALSE，ADR-008/§10）。
    """

    MATCHED = "matched"
    MISSING = "missing"
    PARTIAL = "partial"
    CLAIMED_ONLY = "claimed_only"
    UNKNOWN = "unknown"


class EvidenceTier(str, Enum):
    """证据优先级（§21）：structured > source chunk > retrieved semantic > absence。

    absence **不是** positive evidence，只用于解释 UNKNOWN。
    """

    STRUCTURED = "structured"
    SOURCE_CHUNK = "source_chunk"
    RETRIEVED = "retrieved"
    ABSENCE = "absence"


class ConstraintReason(str, Enum):
    """硬条件判定的确定性 reason code（非自然语言，可枚举、可断言）。"""

    DEGREE_AT_LEAST_MET = "DEGREE_AT_LEAST_MET"
    DEGREE_BELOW_REQUIRED = "DEGREE_BELOW_REQUIRED"
    DEGREE_UNKNOWN = "DEGREE_UNKNOWN"
    EXPERIENCE_THRESHOLD_MET = "EXPERIENCE_THRESHOLD_MET"
    EXPERIENCE_BELOW_THRESHOLD = "EXPERIENCE_BELOW_THRESHOLD"
    EXPERIENCE_UNKNOWN = "EXPERIENCE_UNKNOWN"
    LOCATION_MATCH = "LOCATION_MATCH"
    LOCATION_MISMATCH = "LOCATION_MISMATCH"
    LOCATION_UNKNOWN = "LOCATION_UNKNOWN"
    LANGUAGE_MATCH = "LANGUAGE_MATCH"
    LANGUAGE_BELOW_REQUIRED = "LANGUAGE_BELOW_REQUIRED"
    LANGUAGE_UNKNOWN = "LANGUAGE_UNKNOWN"
    CERTIFICATION_PRESENT = "CERTIFICATION_PRESENT"
    CERTIFICATION_UNKNOWN = "CERTIFICATION_UNKNOWN"
    SKILL_EVIDENCE_PRESENT = "SKILL_EVIDENCE_PRESENT"
    SKILL_UNKNOWN = "SKILL_UNKNOWN"
    UNSUPPORTED_REQUIREMENT_TYPE = "UNSUPPORTED_REQUIREMENT_TYPE"
    UNSPECIFIED_OPERATOR = "UNSPECIFIED_OPERATOR"


class SkillReason(str, Enum):
    """技能匹配的确定性 reason code。"""

    NORMALIZED_MATCH = "NORMALIZED_MATCH"
    CLAIMED_ONLY_MATCH = "CLAIMED_ONLY_MATCH"
    RETRIEVED_EVIDENCE_PARTIAL = "RETRIEVED_EVIDENCE_PARTIAL"
    NO_EVIDENCE_UNKNOWN = "NO_EVIDENCE_UNKNOWN"
    MISSING_REQUIRED_SKILL = "MISSING_REQUIRED_SKILL"
    UNSUPPORTED_REQUIREMENT_TYPE = "UNSUPPORTED_REQUIREMENT_TYPE"


class RetrievalMethod(str, Enum):
    """检索方式（写入 retrieval 结果与 metadata，便于复现）。"""

    VECTOR = "vector"
    LEXICAL = "lexical"


class DecisionType(str, Enum):
    CONSTRAINT = "constraint"
    SKILL_MATCH = "skill_match"
    SCORE_COMPONENT = "score_component"
    OTHER = "other"


class ScoreKind(str, Enum):
    BASE = "base"
    COUNTERFACTUAL = "counterfactual"


class ReviewDecision(str, Enum):
    APPROVE = "approve"
    REQUEST_CHANGES = "request_changes"
    REJECT = "reject"


class CritiqueStatus(str, Enum):
    """critiques.status：critique 是否成功产出（与 validation 独立）。"""

    OK = "ok"
    UNAVAILABLE = "unavailable"


class ValidationStatus(str, Enum):
    """critique citation/semantic validation 结论（Phase 4 §10）。"""

    PENDING = "pending"
    VALIDATED = "validated"
    REJECTED = "rejected"


class ClaimType(str, Enum):
    """critique claim 的事实类别（Phase 4 §13）。INFERENTIAL ≠ FACT。"""

    SUPPORTED = "supported"
    INFERENTIAL = "inferential"
    UNCERTAIN = "uncertain"
    CONTRADICTED = "contradicted"


class CritiqueCategory(str, Enum):
    """critique point 分区（与 schema 的 sections 一一对应）。"""

    STRENGTH = "strength"
    GAP = "gap"
    RISK = "risk"
    AMBIGUITY = "ambiguity"
    QUESTION = "question"


class ReportStage(str, Enum):
    """reports.stage：draft（已构建）→ validated（ReportValidator 通过）→ final（审批后发布）。

    final 不可再改；每次重新生成 = 新 version 行（§25）。
    """

    DRAFT = "draft"
    VALIDATED = "validated"
    FINAL = "final"


class ReviewOverride(str, Enum):
    """人工对单项硬条件 verdict 的覆盖（三值一致）。"""

    MET = "MET"
    NOT_MET = "NOT_MET"
    UNKNOWN = "UNKNOWN"


class NodeErrorSeverity(str, Enum):
    """节点错误致命性：fatal = 确定性失败进入 failed；degradable = 可降级继续。"""

    FATAL = "fatal"
    DEGRADABLE = "degradable"

"""核心异常层级。"""

from __future__ import annotations

from jobfit.core.enums import NodeErrorSeverity


class JobFitError(Exception):
    """项目统一基类错误。"""


class ConfigurationError(JobFitError):
    """配置/加载失败。"""


class ValidationFailed(JobFitError):
    """领域校验失败（输入/上传）。"""


class ParseFailure(JobFitError):
    """文档解析失败（显式失败；禁止"解析失败→当空文本继续"或猜测 Profile）。"""


class GroundingFailure(JobFitError):
    """抽取结果无法回溯到源文本（evidence quote 未在 chunks 中命中）。"""


class ReservationFailed(JobFitError):
    """LLM attempt reservation 失败（预算耗尽或失去 lease/fencing）。"""


class LeaseLost(JobFitError):
    """Worker 已失去对该 analysis 的独占权（fencing 断言失败）。"""


class StructuredOutputError(JobFitError):
    """LLM 结构化输出解析/校验失败（降级为 UNKNOWN/UNAVAILABLE 的触发点）。"""


class NotFound(JobFitError):
    """实体不存在。"""


class Conflict(JobFitError):
    """幂等/唯一冲突（应由调用方决定复用或报错）。"""


class DuplicateDocument(Conflict):
    """同 sha256 文档已存在。"""


class NodeFailure(JobFitError):
    """图节点失败。携带 severity，供上层决定 fatal / degradable。"""

    def __init__(self, node: str, severity: NodeErrorSeverity, message: str) -> None:
        self.node = node
        self.severity = severity
        super().__init__(f"[{severity.value}] node={node}: {message}")

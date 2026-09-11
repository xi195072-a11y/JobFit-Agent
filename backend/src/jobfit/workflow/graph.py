"""LangGraph 图装配。

- `build_extraction_graph`：Phase 2 子图（load_documents → parse → extract_resume → extract_jd → finalize）。
- `build_analysis_graph`：Phase 3 全流程（在抽取之后追加 bindings 校验 → 检索 → 确定性分析 → 落库 → succeeded）。

两图共用 `workflow/nodes.py` 的同一套节点实现——不重复建设第二套执行引擎（Phase 3 §6）。
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph
from sqlalchemy.orm import Session, sessionmaker

from jobfit.config.settings import Settings
from jobfit.evidence.embeddings import EmbeddingProvider
from jobfit.llm.provider import LLMProvider
from jobfit.matching.service import DEFAULT_TOP_K
from jobfit.workflow import nodes
from jobfit.workflow.state import WorkflowState


def build_extraction_graph(
    *,
    session_factory: sessionmaker[Session],
    settings: Settings,
    provider: LLMProvider,
    max_llm_attempts: int,
    ttl_seconds: int,
):
    graph = StateGraph(WorkflowState)
    graph.add_node("load_documents", nodes.load_documents_node(session_factory))
    graph.add_node("parse_documents", nodes.parse_documents_node(session_factory, settings))
    graph.add_node(
        "extract_resume",
        nodes.extract_resume_node(session_factory, settings, provider, max_llm_attempts),
    )
    graph.add_node(
        "extract_jd", nodes.extract_jd_node(session_factory, settings, provider, max_llm_attempts)
    )
    graph.add_node("finalize", nodes.finalize_extraction_node(session_factory))
    graph.add_edge(START, "load_documents")
    graph.add_edge("load_documents", "parse_documents")
    graph.add_edge("parse_documents", "extract_resume")
    graph.add_edge("extract_resume", "extract_jd")
    graph.add_edge("extract_jd", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile()


def build_analysis_graph(
    *,
    session_factory: sessionmaker[Session],
    settings: Settings,
    provider: LLMProvider,
    embedding_provider: EmbeddingProvider | None,
    max_llm_attempts: int,
    ttl_seconds: int,
    top_k: int = DEFAULT_TOP_K,
    phase4: bool = False,
):
    """Phase 3 确定性分析图；`phase4=True` 追加 critique → report → awaiting_review 链。

    phase4=False 时保持 Phase 3 行为（终态 succeeded），Phase 3 契约不变；
    phase4=True 时 provider 允许为 None（EXTERNAL CREDENTIAL BLOCKED，critique 落
    unavailable，不伪造 live 验证）。
    """
    holder = nodes.AnalysisContext()
    graph = StateGraph(WorkflowState)
    graph.add_node("load_documents", nodes.load_documents_node(session_factory))
    graph.add_node("parse_documents", nodes.parse_documents_node(session_factory, settings))
    graph.add_node(
        "extract_resume",
        nodes.extract_resume_node(session_factory, settings, provider, max_llm_attempts),
    )
    graph.add_node(
        "extract_jd", nodes.extract_jd_node(session_factory, settings, provider, max_llm_attempts)
    )
    graph.add_node(
        "load_analysis_context",
        nodes.load_analysis_context_node(holder, session_factory, ttl_seconds=ttl_seconds),
    )
    graph.add_node(
        "index_and_retrieve",
        nodes.index_and_retrieve_node(
            holder,
            session_factory,
            provider=embedding_provider,
            top_k=top_k,
            ttl_seconds=ttl_seconds,
        ),
    )
    graph.add_node("compute_analysis", nodes.make_compute_node(holder))
    graph.add_node(
        "persist_results",
        nodes.make_persist_node(holder, session_factory, ttl_seconds=ttl_seconds),
    )

    if phase4:
        critique_provider = provider if provider is not None else None
        graph.add_node(
            "llm_critique",
            nodes.llm_critique_node(
                holder,
                session_factory,
                critique_provider,
                max_llm_attempts,
                ttl_seconds=ttl_seconds,
            ),
        )
        graph.add_node(
            "compile_report",
            nodes.compile_report_node(session_factory, ttl_seconds=ttl_seconds),
        )
        graph.add_node("awaiting_review", nodes.mark_awaiting_review_node(session_factory))
    else:
        graph.add_node("finalize", nodes.finalize_analysis_node(session_factory))

    graph.add_edge(START, "load_documents")
    graph.add_edge("load_documents", "parse_documents")
    graph.add_edge("parse_documents", "extract_resume")
    graph.add_edge("extract_resume", "extract_jd")
    graph.add_edge("extract_jd", "load_analysis_context")
    graph.add_edge("load_analysis_context", "index_and_retrieve")
    graph.add_edge("index_and_retrieve", "compute_analysis")
    graph.add_edge("compute_analysis", "persist_results")

    if phase4:
        graph.add_edge("persist_results", "llm_critique")
        graph.add_edge("llm_critique", "compile_report")
        graph.add_edge("compile_report", "awaiting_review")
        graph.add_edge("awaiting_review", END)
    else:
        graph.add_edge("persist_results", "finalize")
        graph.add_edge("finalize", END)
    return graph.compile()


def build_phase4_resume_graph(
    *,
    session_factory: sessionmaker[Session],
    provider: LLMProvider | None,
    max_llm_attempts: int,
    ttl_seconds: int,
):
    """Phase 4 续接图：succeeded -> (critique -> report -> awaiting_review)（§27/§39）。

    只消费**已落库**的确定性结果（绝不重新触发抽取/检索/重算——Phase 3 的
    succeeded 结果不可变），节点实现与主图完全同一套（同一执行引擎，不重复建设）。
    provider=None => critique 落 unavailable（EXTERNAL CREDENTIAL BLOCKED）。
    """
    holder = nodes.AnalysisContext()
    graph = StateGraph(WorkflowState)
    graph.add_node(
        "llm_critique",
        nodes.llm_critique_node(
            holder,
            session_factory,
            provider,
            max_llm_attempts,
            ttl_seconds=ttl_seconds,
        ),
    )
    graph.add_node(
        "compile_report",
        nodes.compile_report_node(session_factory, ttl_seconds=ttl_seconds),
    )
    graph.add_node("awaiting_review", nodes.mark_awaiting_review_node(session_factory))
    graph.add_edge(START, "llm_critique")
    graph.add_edge("llm_critique", "compile_report")
    graph.add_edge("compile_report", "awaiting_review")
    graph.add_edge("awaiting_review", END)
    return graph.compile()

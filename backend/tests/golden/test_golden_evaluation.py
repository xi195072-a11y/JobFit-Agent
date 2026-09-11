# mypy: disable-error-code="arg-type,attr-defined,assignment,union-attr,return-value,index,call-overload"
"""Golden Evaluation Set（Phase 4 §34 / Phase 5 §27–§30）——声明式、可执行、确定性。

结构：

```
tests/golden/
  cases/golden_cases.json        # 每个 case 的 input + 声明的 metrics
  expected/golden_expected.json  # 每个 case 的 expected_* （deterministic/critique/report/review）
  fixtures/*.txt                 # 完全合成、无 PII 的输入文档
  README.md                      # 结构与指标语义
```

设计原则（§34/§54）：

- **不比对 LLM 自然语言逐字一致**；只断言语义不变量；
- 指标全部由**代码**计算（deterministic metrics），绝不让 LLM 自评；
- 默认使用 test-only `DeterministicProvider`（ADR-029），**不需要 live LLM**；
- 设置 `JOBFIT_GOLDEN_METRICS_PATH` 时，session 结束写出机器可读指标，
  供 `python -m jobfit.evaluation` 渲染 `phase5-evaluation.{json,md}`。
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from jobfit.core.enums import ReviewDecision
from jobfit.critique.validation import load_validation_store
from jobfit.db import models
from jobfit.db.repositories import critiques as critiques_repo
from jobfit.db.repositories import reports as reports_repo
from jobfit.db.repositories import reviews as reviews_repo
from jobfit.review.service import apply_review
from support import (
    JD_MATCH_PAYLOAD,
    JD_MISMATCH_PAYLOAD,
    JD_UNKNOWN_PAYLOAD,
    RESUME_MATCH_PAYLOAD,
    RESUME_MISMATCH_PAYLOAD,
    DeterministicProvider,
    build_valid_critique_payload,
    make_analysis,
    make_document,
    run_analysis,
    run_phase4,
)

#: 声明式 case 通过 artifact 名称引用 payload（避免 import 整个 support 模块）。
PAYLOADS: dict[str, dict] = {
    "RESUME_MATCH_PAYLOAD": RESUME_MATCH_PAYLOAD,
    "RESUME_MISMATCH_PAYLOAD": RESUME_MISMATCH_PAYLOAD,
    "JD_MATCH_PAYLOAD": JD_MATCH_PAYLOAD,
    "JD_MISMATCH_PAYLOAD": JD_MISMATCH_PAYLOAD,
    "JD_UNKNOWN_PAYLOAD": JD_UNKNOWN_PAYLOAD,
}

pytestmark = pytest.mark.db

GOLDEN_DIR = Path(__file__).resolve().parent
FIXTURES = GOLDEN_DIR / "fixtures"

CASES: list[dict] = json.loads(
    (GOLDEN_DIR / "cases" / "golden_cases.json").read_text(encoding="utf-8")
)
EXPECTED: dict[str, dict] = json.loads(
    (GOLDEN_DIR / "expected" / "golden_expected.json").read_text(encoding="utf-8")
)
CASE_INDEX: dict[str, dict] = {case["name"]: case for case in CASES}
CASE_NAMES: list[str] = [case["name"] for case in CASES]

#: 运行期收集（由 session fixture 写出机器可读指标）。
RUN_METRICS: dict[str, dict[str, bool]] = {}
RUN_REASONS: dict[str, list[str]] = {}

DERIVED_SECTIONS = frozenset({"strengths", "gaps", "risks", "critique_unknowns"})


@pytest.fixture(scope="session", autouse=True)
def _write_golden_metrics():
    """session 结束时写出机器可读指标（仅当显式指定路径时，避免污染本地运行）。"""
    yield
    target = os.getenv("JOBFIT_GOLDEN_METRICS_PATH")
    if not target:
        return
    payload = {
        "cases": [
            {
                "name": name,
                "declared_metrics": CASE_INDEX[name]["metrics"],
                "metrics": RUN_METRICS.get(name, {}),
                "passed": bool(RUN_METRICS.get(name))
                and all(RUN_METRICS[name].values()),
                "reasons": RUN_REASONS.get(name, []),
            }
            for name in CASE_NAMES
        ]
    }
    Path(target).parent.mkdir(parents=True, exist_ok=True)
    Path(target).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


# ================================================================ harness


def _fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _run_phase3(session, session_factory, settings, case: dict):
    resume = make_document(
        session,
        settings,
        kind="resume",
        filename=case["resume_fixture"],
        content=_fixture_bytes(case["resume_fixture"]),
    )
    jd = make_document(
        session,
        settings,
        kind="jd",
        filename=case["jd_fixture"],
        content=_fixture_bytes(case["jd_fixture"]),
    )
    analysis = make_analysis(
        session,
        resume_document_id=uuid.UUID(str(resume.id)),
        jd_document_id=uuid.UUID(str(jd.id)),
    )
    result = run_analysis(
        session_factory=session_factory,
        settings=settings,
        analysis_id=analysis.id,
        provider=DeterministicProvider(
            resume_payload=dict(PAYLOADS[case["resume_payload"]]),
            jd_payload=dict(PAYLOADS[case["jd_payload"]]),
        ),
    )
    assert result.status == "completed"
    session.expire_all()
    return uuid.UUID(str(analysis.id)), result


def _provider(mode: str, *, foreign_chunk: str | None = None) -> DeterministicProvider:
    if mode == "valid":
        return DeterministicProvider(critique_payload=build_valid_critique_payload)
    if mode == "fabricated":
        return DeterministicProvider(
            critique_payload=lambda p: build_valid_critique_payload(
                p, fabricated_chunk_id=str(uuid.uuid4())
            )
        )
    if mode == "unknown_override":
        return DeterministicProvider(
            critique_payload=lambda p: build_valid_critique_payload(p, unknown_claim=True)
        )
    if mode == "contradicted":
        return DeterministicProvider(
            critique_payload=lambda p: build_valid_critique_payload(p, not_met_claim=True)
        )
    if mode == "cross_analysis":
        assert foreign_chunk is not None, "cross_analysis mode needs a foreign chunk id"
        return DeterministicProvider(
            critique_payload=lambda p: build_valid_critique_payload(
                p, fabricated_chunk_id=foreign_chunk
            )
        )
    raise AssertionError(f"unknown golden critique mode: {mode}")


def _foreign_chunk_id(session, session_factory, settings) -> str:
    """跑一个独立 analysis（不同简历 => 不同证据池），取它的 chunk 作为"外来"引用。"""
    pair_case = {
        "resume_fixture": "resume_mismatch.txt",
        "jd_fixture": "jd_match.txt",
        "resume_payload": "RESUME_MISMATCH_PAYLOAD",
        "jd_payload": "JD_MATCH_PAYLOAD",
    }
    other_id, _ = _run_phase3(session, session_factory, settings, pair_case)
    with session_factory() as s:
        store = load_validation_store(s, analysis_id=other_id)
    assert store.chunks
    return next(iter(store.chunks))


def _verdicts(session_factory, analysis_id) -> list[str]:
    """返回 distinct verdict 集合（排序）。"""
    with session_factory() as s:
        values = (
            s.execute(
                select(models.HardConstraintResult.result).where(
                    models.HardConstraintResult.analysis_id == analysis_id
                )
            )
            .scalars()
            .all()
        )
    return sorted({str(v) for v in values})


def _skills(session_factory, analysis_id) -> dict[str, str]:
    with session_factory() as s:
        return dict(
            s.execute(
                select(models.SkillMatchResult.norm_used, models.SkillMatchResult.status).where(
                    models.SkillMatchResult.analysis_id == analysis_id
                )
            ).all()
        )


def _constraint_rows(session_factory, analysis_id) -> list[tuple[str, object, object]]:
    with session_factory() as s:
        rows = s.execute(
            select(
                models.HardConstraintResult.requirement_id,
                models.HardConstraintResult.result,
                models.HardConstraintResult.trace_id,
            ).where(models.HardConstraintResult.analysis_id == analysis_id)
        ).all()
    return [(str(r[0]), r[1], r[2]) for r in rows]


def _trace_count(session_factory, analysis_id) -> int:
    with session_factory() as s:
        return len(
            s.execute(
                select(models.DecisionTrace.id).where(
                    models.DecisionTrace.analysis_id == analysis_id
                )
            )
            .scalars()
            .all()
        )


class _Snapshot:
    def __init__(self, *, status, critique, report, sections):
        self.analysis_status = status
        self.critique_validation = critique.validation_status if critique else None
        self.citations_validated = critique.citations_validated if critique else None
        self.report_stage = report.stage if report else None
        self.section_types = sections
        self.report_unknown_ids = set()
        if report is not None:
            for section in (report.content or {}).get("sections", []):
                if section.get("type") == "unknowns":
                    self.report_unknown_ids = {
                        str(row["requirement_id"]) for row in section.get("rows", [])
                    }


def _snapshot(session_factory, analysis_id) -> _Snapshot:
    with session_factory() as s:
        analysis = s.get(models.Analysis, analysis_id)
        critique = critiques_repo.get_latest_critique(s, analysis_id)
        report = reports_repo.get_latest_report(s, analysis_id)
        sections = (
            frozenset(sec["type"] for sec in (report.content or {}).get("sections", []))
            if report is not None
            else frozenset()
        )
        return _Snapshot(
            status=str(analysis.status),
            critique=critique,
            report=report,
            sections=sections,
        )


def _unknown_requirement_ids(session_factory, analysis_id) -> set[str]:
    with session_factory() as s:
        return {
            str(row)
            for row in s.execute(
                select(models.HardConstraintResult.requirement_id).where(
                    models.HardConstraintResult.analysis_id == analysis_id,
                    models.HardConstraintResult.result == "UNKNOWN",
                )
            )
            .scalars()
            .all()
        }


# ================================================================ golden driver


@pytest.mark.parametrize("name", CASE_NAMES)
def test_golden_case(session, session_factory, db_settings, name: str) -> None:
    case = CASE_INDEX[name]
    expectation = EXPECTED[name]
    det = expectation["deterministic"]
    cri = expectation["critique"]
    rep = expectation["report"]

    metrics: dict[str, bool] = {key: False for key in case["metrics"]}
    reasons: list[str] = []

    # ---------------- phase 3：确定性契约 + 决策链完整性 ----------------
    analysis_id, result = _run_phase3(session, session_factory, db_settings, case)
    verdicts = _verdicts(session_factory, analysis_id)
    skills = _skills(session_factory, analysis_id)
    constraint_rows = _constraint_rows(session_factory, analysis_id)

    exact = (
        result.gate == det["gate"]
        and verdicts == sorted(det["verdicts"])
        and len(constraint_rows) == det["constraint_count"]
    )
    if not exact:
        reasons.append(
            f"deterministic mismatch: gate={result.gate!r} verdicts={verdicts} "
            f"count={len(constraint_rows)} (expected gate={det['gate']!r} "
            f"verdicts={sorted(det['verdicts'])} count={det['constraint_count']})"
        )
    metrics["hard_constraint_exact_agreement"] = exact

    skill_ok = skills == det["skills"]
    if not skill_ok:
        reasons.append(f"skill mismatch: {skills} != {det['skills']}")
    metrics["skill_match_agreement"] = skill_ok

    trace_ok = (
        _trace_count(session_factory, analysis_id) > 0
        and all(row[2] is not None for row in constraint_rows)
    )
    if not trace_ok:
        reasons.append("decision trace incomplete: 存在无 trace_id 的约束或无 trace")
    metrics["decision_trace_completeness"] = trace_ok

    # ---------------- phase 4：citation 契约 + 报告不变量 ----------------
    foreign = None
    if cri["mode"] == "cross_analysis":
        foreign = _foreign_chunk_id(session, session_factory, db_settings)

    run = run_phase4(
        session_factory=session_factory,
        settings=db_settings,
        analysis_id=analysis_id,
        provider=_provider(cri["mode"], foreign_chunk=foreign),
    )
    assert run.status == "completed"
    snap = _snapshot(session_factory, analysis_id)

    citation_ok = (
        snap.critique_validation == cri["validation"]
        and snap.citations_validated == cri["citations_validated"]
    )
    if not citation_ok:
        reasons.append(
            f"citation contract mismatch: validation={snap.critique_validation!r} "
            f"citations_validated={snap.citations_validated!r} "
            f"(expected {cri['validation']!r}/{cri['citations_validated']!r})"
        )
    metrics["citation_validity"] = citation_ok

    unknown_ids = _unknown_requirement_ids(session_factory, analysis_id)
    unknown_ok = snap.report_unknown_ids == unknown_ids
    if cri["citations_validated"] and unknown_ids:
        unknown_ok = unknown_ok and ("critique_unknowns" in snap.section_types)
    if not unknown_ok:
        reasons.append(
            f"UNKNOWN not preserved: report={sorted(snap.report_unknown_ids)} "
            f"expected={sorted(unknown_ids)}"
        )
    metrics["unknown_preservation"] = unknown_ok

    required = set(rep["required_sections"])
    forbidden = set(rep["forbidden_sections"])
    if not cri["derived_sections"]:
        required -= DERIVED_SECTIONS
    report_ok = (
        snap.report_stage == rep["stage"]
        and required <= snap.section_types
        and not (forbidden & set(snap.section_types))
    )
    if not report_ok:
        reasons.append(
            f"report invariant mismatch: stage={snap.report_stage!r} "
            f"missing={sorted(required - set(snap.section_types))} "
            f"leaked={sorted(forbidden & set(snap.section_types))}"
        )
    metrics["report_invariant_validity"] = report_ok

    # ---------------- review 生命周期 ----------------
    review = expectation.get("review")
    if review is not None:
        decision = review["decision"]
        outcome = apply_review(
            session_factory,
            analysis_id=analysis_id,
            decision=decision,
            comments="golden review" if decision != ReviewDecision.REJECT.value else "证据不足，需补充",
            reviewed_by="golden-reviewer",
        )
        post = _snapshot(session_factory, analysis_id)
        with session_factory() as s:
            history = reviews_repo.list_reviews(s, analysis_id)
        review_ok = (
            outcome.to_state == review["final_status"]
            and post.analysis_status == review["final_status"]
            and post.report_stage == review["report_stage"]
            and len(history) == 1
            and history[0].decision == decision
        )
        if not review_ok:
            reasons.append(
                f"review lifecycle mismatch: to_state={outcome.to_state!r} "
                f"status={post.analysis_status!r} report_stage={post.report_stage!r} "
                f"reviews={len(history)}"
            )
        metrics["review_lifecycle"] = review_ok

    # ---------------- 记录 + 断言 ----------------
    RUN_METRICS[name] = {key: bool(metrics[key]) for key in case["metrics"]}
    RUN_REASONS[name] = reasons
    failed = [key for key in case["metrics"] if not metrics[key]]
    assert not failed, f"golden case {name} failed metrics {failed}: {reasons}"


def test_golden_dataset_covers_required_scenarios() -> None:
    """§30：核心场景必须全部存在且可自动执行。"""
    required = {
        "perfect_match",
        "obvious_mismatch",
        "unknown",
        "mixed_constraints",
        "skill_partial_match",
        "citation_failure",
        "cross_analysis_citation",
        "prompt_injection",
        "contradictory_evidence",
        "review_rejection",
        "review_approval",
    }
    assert required <= set(CASE_NAMES)
    assert set(EXPECTED) == set(CASE_NAMES)
    for case in CASES:
        assert case["metrics"], f"case {case['name']} declares no metrics"


def test_golden_metrics_names_are_known() -> None:
    """§29：指标集合受控（避免凭空发明模型质量指标）。"""
    known = {
        "hard_constraint_exact_agreement",
        "skill_match_agreement",
        "unknown_preservation",
        "citation_validity",
        "report_invariant_validity",
        "decision_trace_completeness",
        "review_lifecycle",
    }
    declared = {metric for case in CASES for metric in case["metrics"]}
    assert declared <= known
    # §29 要求的六个指标都必须至少被一个 case 覆盖
    assert {
        "hard_constraint_exact_agreement",
        "skill_match_agreement",
        "unknown_preservation",
        "citation_validity",
        "report_invariant_validity",
        "decision_trace_completeness",
    } <= declared

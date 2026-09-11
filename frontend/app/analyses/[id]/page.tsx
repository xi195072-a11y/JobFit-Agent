"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";

import { EmptyBlock, ErrorBlock, Loading, NotFoundBlock, useAsync } from "@/components/AsyncState";
import { ConstraintsTable } from "@/components/ConstraintsTable";
import { CritiquePanel } from "@/components/CritiquePanel";
import { EvidenceList } from "@/components/EvidenceList";
import { ScorePanel } from "@/components/ScorePanel";
import { SkillsTable } from "@/components/SkillsTable";
import { TraceList } from "@/components/TraceList";
import { type ApiError, normalizeError } from "@/lib/api/errors";
import { analysisStatusLabel, formatScore, reportStageLabel, truncateId, verdictClass, verdictDescription } from "@/lib/api/format";
import {
  buildReport,
  getAnalysis,
  getCritique,
  getReport,
  getScoreOrNull,
  listConstraints,
  listEvidence,
  listReviews,
  listSkillMatches,
  listTrace,
  runAnalysis,
  triggerCritique,
} from "@/lib/api/resources";
import type {
  Analysis,
  Constraint,
  Critique,
  DecisionTrace,
  EvidenceList as EvidenceListData,
  Report,
  Review,
  ScoreSnapshot,
  SkillMatch,
} from "@/lib/api/types";

interface DetailData {
  analysis: Analysis;
  constraints: Constraint[];
  skills: SkillMatch[];
  score: ScoreSnapshot | null;
  evidence: EvidenceListData;
  traces: DecisionTrace[];
  critique: Critique | null;
  report: Report | null;
  reviews: Review[];
}

async function loadDetail(id: string): Promise<DetailData> {
  const [analysis, constraints, skills, score, evidence, traces, critique, report, reviews] = await Promise.all([
    getAnalysis(id),
    listConstraints(id),
    listSkillMatches(id),
    getScoreOrNull(id),
    listEvidence(id),
    listTrace(id),
    getCritique(id),
    getReport(id),
    listReviews(id),
  ]);
  return { analysis, constraints, skills, score, evidence, traces, critique, report, reviews };
}

export default function AnalysisDetailPage() {
  const params = useParams<{ id: string }>();
  const id = params?.id ?? "";
  const detail = useAsync(`analysis-detail:${id}`, () => loadDetail(id));

  const [actionError, setActionError] = useState<ApiError | null>(null);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  async function runAction(name: string, action: () => Promise<string>) {
    setBusy(name);
    setActionError(null);
    setActionMessage(null);
    try {
      const message = await action();
      setActionMessage(message);
      detail.reload();
    } catch (error) {
      setActionError(normalizeError(error));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div data-testid="analysis-detail">
      <div className="page-header">
        <h1>Analysis {truncateId(id, 12)}</h1>
        <p>
          <Link href={`/review/${id}`}>打开评审页</Link>
        </p>
      </div>

      {detail.state.status === "loading" ? <Loading /> : null}
      {detail.state.status === "error" ? (
        detail.state.error.status === 404 ? (
          <NotFoundBlock message="未找到该 analysis。" />
        ) : (
          <ErrorBlock error={detail.state.error} onRetry={detail.reload} />
        )
      ) : null}

      {detail.state.status === "ready" ? (
        <DetailBody
          id={id}
          data={detail.state.data}
          busy={busy}
          actionError={actionError}
          actionMessage={actionMessage}
          onRun={() =>
            runAction("run", async () => {
              const result = await runAnalysis(id, {});
              return `run: status=${result.status} · gate=${result.gate ?? "—"} · score_total=${formatScore(
                result.score_total,
              )} · constraints=${result.constraint_count} · skills=${result.skill_match_count} · traces=${result.trace_count}`;
            })
          }
          onCritique={() =>
            runAction("critique", async () => {
              const result = await triggerCritique(id);
              return `critique: status=${result.status} · critique_status=${
                result.critique_status ?? "—"
              } · validation=${result.critique_validation ?? "—"} · report_stage=${result.report_stage ?? "—"}`;
            })
          }
          onBuildReport={() =>
            runAction("report", async () => {
              const result = await buildReport(id);
              return `report: version=${result.version} · stage=${result.stage} · valid=${result.valid} · reused=${result.reused}`;
            })
          }
        />
      ) : null}
    </div>
  );
}

function DetailBody({
  id,
  data,
  busy,
  actionError,
  actionMessage,
  onRun,
  onCritique,
  onBuildReport,
}: {
  id: string;
  data: DetailData;
  busy: string | null;
  actionError: ApiError | null;
  actionMessage: string | null;
  onRun: () => void;
  onCritique: () => void;
  onBuildReport: () => void;
}) {
  const { analysis, constraints, skills, score, evidence, traces, critique, report, reviews } = data;

  return (
    <>
      <div className="panel">
        <h2>状态</h2>
        <dl className="kv">
          <div>
            <dt>analysis_status</dt>
            <dd>
              <span data-testid="analysis-status" className="status-text">
                {analysis.status}
              </span>
              <span className="muted"> · {analysisStatusLabel(analysis.status)}</span>
            </dd>
          </div>
          <div>
            <dt>current_phase</dt>
            <dd>{analysis.current_phase ?? "—"}</dd>
          </div>
          <div>
            <dt>pipeline_version</dt>
            <dd>{analysis.pipeline_version}</dd>
          </div>
          <div>
            <dt>ruleset / scoring</dt>
            <dd>
              {analysis.ruleset_version ?? "—"} / {analysis.scoring_version ?? "—"}
            </dd>
          </div>
          <div>
            <dt>llm_model</dt>
            <dd>{analysis.llm_model ?? "—"}</dd>
          </div>
          <div>
            <dt>report</dt>
            <dd data-testid="report-status">
              {report ? `${reportStageLabel(report.stage)} (v${report.version})` : "尚无报告"}
            </dd>
          </div>
          <div>
            <dt>review</dt>
            <dd data-testid="review-status">
              {analysis.status} · reviews={reviews.length}
            </dd>
          </div>
        </dl>

        <div className="actions">
          <button type="button" data-testid="run-analysis" onClick={onRun} disabled={busy !== null}>
            {busy === "run" ? "执行中…" : "运行确定性分析"}
          </button>
          <button type="button" data-testid="generate-critique" onClick={onCritique} disabled={busy !== null}>
            {busy === "critique" ? "生成中…" : "生成 critique"}
          </button>
          <button type="button" data-testid="build-report" onClick={onBuildReport} disabled={busy !== null}>
            {busy === "report" ? "构建中…" : "构建报告"}
          </button>
          <Link href={`/reports/${id}`}>查看报告</Link>
        </div>

        <div aria-live="polite">
          {actionError ? <ErrorBlock error={actionError} /> : null}
          {actionMessage ? <p className="muted">{actionMessage}</p> : null}
        </div>
      </div>

      <section className="section-block" data-testid="deterministic-section">
        <h2>确定性分析（deterministic）</h2>

        <div className="panel">
          <h3>硬性条件（constraints）</h3>
          <ul className="legend">
            <li>
              <span className={verdictClass("MET")}>{verdictDescription("MET")}</span>
            </li>
            <li>
              <span className={verdictClass("NOT_MET")}>{verdictDescription("NOT_MET")}</span>
            </li>
            <li>
              <span className={verdictClass("UNKNOWN")}>{verdictDescription("UNKNOWN")}</span>
            </li>
          </ul>
          <div className="table-scroll">
            <ConstraintsTable constraints={constraints} traces={traces} />
          </div>
        </div>

        <div className="panel">
          <h3>技能匹配（skills）</h3>
          <div className="table-scroll">
            <SkillsTable skills={skills} />
          </div>
        </div>

        <ScorePanel score={score} />

        <EvidenceList evidence={evidence} />

        <TraceList traces={traces} />
      </section>

      <section className="section-block" data-testid="llm-section">
        <h2>LLM 解释层（critique）</h2>
        <CritiquePanel critique={critique} />
      </section>

      {reviews.length === 0 && constraints.length === 0 && skills.length === 0 ? (
        <EmptyBlock message="该 analysis 尚无确定性结果，请先运行分析。" />
      ) : null}
    </>
  );
}

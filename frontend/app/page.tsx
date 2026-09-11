"use client";

import { AnalysisRow } from "@/components/AnalysisRow";
import { EmptyBlock, ErrorBlock, Loading, useAsync } from "@/components/AsyncState";
import { getHealth, listAnalyses } from "@/lib/api/resources";

const PAGE_LIMIT = 10;

export default function DashboardPage() {
  const health = useAsync("dashboard:health", getHealth);
  const analyses = useAsync(`dashboard:analyses:0:${PAGE_LIMIT}`, () =>
    listAnalyses({ limit: PAGE_LIMIT, offset: 0 }),
  );

  return (
    <div data-testid="dashboard">
      <div className="page-header">
        <h1>Dashboard</h1>
        <p aria-live="polite">
          {health.state.status === "ready" ? (
            <>
              pipeline_version: <code data-testid="pipeline-version">{health.state.data.pipeline_version}</code>
            </>
          ) : null}
          {health.state.status === "loading" ? <span className="muted">pipeline_version: 加载中…</span> : null}
        </p>
      </div>

      {health.state.status === "error" ? (
        <ErrorBlock error={health.state.error} onRetry={health.reload} />
      ) : null}

      <h2>最近分析</h2>

      {analyses.state.status === "loading" ? <Loading /> : null}
      {analyses.state.status === "error" ? (
        <ErrorBlock error={analyses.state.error} onRetry={analyses.reload} />
      ) : null}

      {analyses.state.status === "ready" ? (
        analyses.state.data.items.length === 0 ? (
          <EmptyBlock message="暂无分析记录，请先在 /analyses/new 创建分析。" />
        ) : (
          <div className="table-scroll">
            <table className="data-table" data-testid="analyses-table">
              <caption className="sr-only">最近分析</caption>
              <thead>
                <tr>
                  <th scope="col">analysis</th>
                  <th scope="col">status</th>
                  <th scope="col">gate</th>
                  <th scope="col">score</th>
                  <th scope="col">created_at (UTC)</th>
                  <th scope="col">pipeline_version</th>
                </tr>
              </thead>
              <tbody>
                {analyses.state.data.items.map((analysis) => (
                  <AnalysisRow key={analysis.id} analysis={analysis} rowTestId="analysis-row" />
                ))}
              </tbody>
            </table>
          </div>
        )
      ) : null}
    </div>
  );
}

"use client";

import { AnalysisRow } from "@/components/AnalysisRow";
import { EmptyBlock, ErrorBlock, Loading, useAsync } from "@/components/AsyncState";
import { listAnalyses } from "@/lib/api/resources";
import { useState } from "react";

const PAGE_LIMIT = 10;

export default function JobsPage() {
  const [offset, setOffset] = useState(0);
  const analyses = useAsync(`jobs:analyses:${offset}:${PAGE_LIMIT}`, () =>
    listAnalyses({ limit: PAGE_LIMIT, offset }),
  );

  const total = analyses.state.status === "ready" ? analyses.state.data.total : 0;
  const hasPrev = offset > 0;
  const hasNext = analyses.state.status === "ready" ? offset + PAGE_LIMIT < total : false;

  return (
    <div data-testid="jobs-page">
      <div className="page-header">
        <h1>Analysis Jobs</h1>
        <p>
          总数 <strong data-testid="total-count">{analyses.state.status === "ready" ? total : "…"}</strong>
        </p>
      </div>

      {analyses.state.status === "loading" ? <Loading /> : null}
      {analyses.state.status === "error" ? (
        <ErrorBlock error={analyses.state.error} onRetry={analyses.reload} />
      ) : null}

      {analyses.state.status === "ready" ? (
        analyses.state.data.items.length === 0 ? (
          <EmptyBlock message="当前页暂无 analysis。" />
        ) : (
          <div className="table-scroll">
            <table className="data-table">
              <caption className="sr-only">Analysis 列表</caption>
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
                  <AnalysisRow key={analysis.id} analysis={analysis} rowTestId="job-row" />
                ))}
              </tbody>
            </table>
          </div>
        )
      ) : null}

      <div className="actions" style={{ marginTop: "1rem" }}>
        <button type="button" data-testid="prev-page" disabled={!hasPrev} onClick={() => setOffset((value) => Math.max(0, value - PAGE_LIMIT))}>
          上一页
        </button>
        <button type="button" data-testid="next-page" disabled={!hasNext} onClick={() => setOffset((value) => value + PAGE_LIMIT)}>
          下一页
        </button>
        <span className="muted">
          当前 {offset + 1}-{offset + PAGE_LIMIT} / 共 {total}
        </span>
      </div>
    </div>
  );
}

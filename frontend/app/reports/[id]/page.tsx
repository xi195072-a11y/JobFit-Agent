"use client";

import Link from "next/link";
import { useParams } from "next/navigation";

import { EmptyBlock, ErrorBlock, Loading, NotFoundBlock, useAsync } from "@/components/AsyncState";
import { ReportSection } from "@/components/ReportSection";
import { formatDateTime, reportStageLabel, truncateId } from "@/lib/api/format";
import { getReport } from "@/lib/api/resources";

export default function ReportViewPage() {
  const params = useParams<{ id: string }>();
  const id = params?.id ?? "";
  const report = useAsync(`report-view:${id}`, () => getReport(id));

  const sections = report.state.status === "ready" ? report.state.data?.content?.sections ?? [] : [];

  return (
    <div data-testid="report-view">
      <div className="page-header">
        <h1>报告 {truncateId(id, 12)}</h1>
        <p>
          <Link href={`/analyses/${id}`}>返回分析详情</Link>
        </p>
      </div>

      {report.state.status === "loading" ? <Loading /> : null}
      {report.state.status === "error" ? (
        <ErrorBlock error={report.state.error} onRetry={report.reload} />
      ) : null}

      {report.state.status === "ready" ? (
        report.state.data === null ? (
          <NotFoundBlock message="未找到该报告（可能尚未生成）。" />
        ) : (
          <>
            <div className="panel">
              <h2>报告元信息</h2>
              <dl className="kv">
                <div>
                  <dt>stage</dt>
                  <dd data-testid="report-stage">{reportStageLabel(report.state.data.stage)}</dd>
                </div>
                <div>
                  <dt>version</dt>
                  <dd>{report.state.data.version}</dd>
                </div>
                <div>
                  <dt>fingerprint</dt>
                  <dd>
                    <code>{truncateId(report.state.data.fingerprint, 12)}</code>
                  </dd>
                </div>
                <div>
                  <dt>published_at</dt>
                  <dd>{report.state.data.published_at ? formatDateTime(report.state.data.published_at) : "—"}</dd>
                </div>
              </dl>
              <p className="muted">以下内容全部来自后端 report sections，前端不做重新组织。</p>
            </div>

            {sections.length === 0 ? (
              <EmptyBlock message="报告尚无 sections。" />
            ) : (
              sections.map((section, index) => (
                <ReportSection key={`${section.type}-${index}`} section={section} />
              ))
            )}
          </>
        )
      ) : null}
    </div>
  );
}

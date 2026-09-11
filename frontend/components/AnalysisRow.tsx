"use client";

import Link from "next/link";

import { analysisStatusClass, analysisStatusLabel, formatDateTime, formatScore, truncateId } from "@/lib/api/format";
import { getScoreOrNull } from "@/lib/api/resources";
import type { Analysis } from "@/lib/api/types";

import { useAsync } from "./AsyncState";

/**
 * 单行 analysis：gate / score 只能来自后端 /score（前端不得计算）。
 * 未生成评分（404）或读取失败时显示占位符。
 */
export function AnalysisRow({ analysis, rowTestId }: { analysis: Analysis; rowTestId: string }) {
  const score = useAsync(`analysis-score:${analysis.id}`, () => getScoreOrNull(analysis.id));
  const snapshot = score.state.status === "ready" ? score.state.data : null;

  return (
    <tr data-testid={rowTestId}>
      <td>
        <Link href={`/analyses/${analysis.id}`}>{truncateId(analysis.id, 10)}</Link>
      </td>
      <td data-testid="analysis-status">
        <span className={analysisStatusClass(analysis.status)} title={analysisStatusLabel(analysis.status)}>
          {analysis.status}
        </span>
      </td>
      <td data-testid="analysis-gate">{snapshot ? snapshot.gate : "—"}</td>
      <td data-testid="analysis-score">{snapshot ? formatScore(snapshot.total) : "—"}</td>
      <td>{formatDateTime(analysis.created_at)}</td>
      <td>{analysis.pipeline_version}</td>
    </tr>
  );
}

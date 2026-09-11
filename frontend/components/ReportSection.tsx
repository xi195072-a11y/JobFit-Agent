"use client";

import { formatCellValue, sectionTitle } from "@/lib/api/format";
import type { ReportSectionData } from "@/lib/api/types";

function columnsOf(rows: Record<string, unknown>[]): string[] {
  const columns: string[] = [];
  for (const row of rows) {
    for (const key of Object.keys(row)) {
      if (!columns.includes(key)) {
        columns.push(key);
      }
    }
  }
  return columns;
}

function ContentView({ content }: { content: Record<string, unknown> }) {
  const entries = Object.entries(content);
  if (entries.length === 0) {
    return <p className="muted">无内容。</p>;
  }
  return (
    <dl className="kv">
      {entries.map(([key, value]) => (
        <div key={key}>
          <dt>{key}</dt>
          <dd>
            {value !== null && typeof value === "object" ? (
              // 可滚动区域必须可键盘聚焦（a11y：scrollable-region-focusable）
              <pre className="json-block" tabIndex={0} aria-label={`${key}（可滚动内容）`}>
                {JSON.stringify(value, null, 2)}
              </pre>
            ) : (
              formatCellValue(value)
            )}
          </dd>
        </div>
      ))}
    </dl>
  );
}

/** 按后端 section.type 原样渲染；不重新组织叙述。 */
export function ReportSection({ section }: { section: ReportSectionData }) {
  const rows = Array.isArray(section.rows) ? section.rows : [];
  const content = section.content;
  const columns = columnsOf(rows);

  return (
    <section className="panel" data-testid={`report-section-${section.type}`}>
      <h3>{sectionTitle(section.type)}</h3>
      {content ? <ContentView content={content} /> : null}
      {rows.length > 0 && columns.length > 0 ? (
        <table className="data-table">
          <caption className="sr-only">{sectionTitle(section.type)}</caption>
          <thead>
            <tr>
              {columns.map((column) => (
                <th key={column} scope="col">
                  {column}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => (
              <tr key={index}>
                {columns.map((column) => (
                  <td key={column}>{formatCellValue(row[column])}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
      {!content && rows.length === 0 ? <p className="muted">无内容。</p> : null}
    </section>
  );
}

"use client";

import { formatSpan, truncateId } from "@/lib/api/format";
import type { DecisionTrace } from "@/lib/api/types";

export function TraceList({ traces }: { traces: DecisionTrace[] }) {
  if (traces.length === 0) {
    return (
      <div className="panel">
        <h3>决策链</h3>
        <p className="muted">暂无决策链记录。</p>
      </div>
    );
  }

  return (
    <div className="panel" data-testid="trace-list">
      <h3>决策链（{traces.length}）</h3>
      <ul className="trace-items">
        {traces.map((trace) => {
          const chain = trace.chain ?? {};
          const rings = Array.isArray(chain.evidence) ? chain.evidence : [];
          return (
            <li key={`${trace.decision_type}:${trace.decision_key}`} data-testid="trace-item">
              <div className="trace-head">
                <span className="ref-chip">{trace.decision_type}</span>
                <code>{trace.decision_key}</code>
              </div>
              <div>
                rule: <code>{chain.rule?.rule_id ?? "—"}</code> · reason_code:{" "}
                <code>{chain.decision?.reason_code ?? "—"}</code> · result:{" "}
                <code>{chain.decision?.result ?? "—"}</code>
              </div>
              <div>
                evidence:{" "}
                {rings.length === 0 ? (
                  <span className="muted">无</span>
                ) : (
                  rings.map((ring, index) => (
                    <span key={`${ring.source_chunk_id ?? "ring"}-${index}`} className="ref-chip">
                      <code>{truncateId(ring.source_chunk_id ?? null, 6)}</code>{" "}
                      {formatSpan(ring.char_start, ring.char_end)}
                      {ring.tier ? ` · ${ring.tier}` : ""}
                    </span>
                  ))
                )}
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

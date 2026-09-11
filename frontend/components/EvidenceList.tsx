"use client";

import { formatSpan, truncateHash, truncateId } from "@/lib/api/format";
import type { EvidenceList as EvidenceListData } from "@/lib/api/types";

export function EvidenceList({ evidence }: { evidence: EvidenceListData | null }) {
  if (evidence === null || evidence.items.length === 0) {
    return (
      <div className="panel" data-testid="evidence-list">
        <h3>证据引用</h3>
        <p className="muted">暂无证据引用。</p>
      </div>
    );
  }

  return (
    <div className="panel" data-testid="evidence-list">
      <h3>证据引用（共 {evidence.total}）</h3>
      <ul className="evidence-items">
        {evidence.items.map((item) => (
          <li key={item.source_chunk_id} data-testid="evidence-item">
            <div>
              source_chunk_id: <code>{truncateId(item.source_chunk_id)}</code>
              {item.resolvable ? null : <span className="warn">（不可解析）</span>}
            </div>
            <div>
              span: <code>{formatSpan(item.char_start, item.char_end)}</code> · page:{" "}
              {item.page ?? "—"}
            </div>
            <div>
              span_sha256: <code>{truncateHash(item.span_sha256)}</code>
            </div>
            <div>
              referenced_by:{" "}
              {item.referenced_by.length === 0 ? (
                <span className="muted">无</span>
              ) : (
                item.referenced_by.map((ref, index) => (
                  <span key={`${ref.kind}-${ref.ref_id}-${index}`} className="ref-chip">
                    {ref.kind}:{truncateId(ref.ref_id, 6)}
                    {ref.label ? ` (${ref.label})` : ""}
                    {ref.tier ? ` · ${ref.tier}` : ""}
                  </span>
                ))
              )}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

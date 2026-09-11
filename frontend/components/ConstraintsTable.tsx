"use client";

import { Fragment, useState } from "react";

import { formatSpan, isUnknownVerdict, truncateId, verdictClass, verdictLabel } from "@/lib/api/format";
import type { Constraint, DecisionTrace } from "@/lib/api/types";

/** 按 decision_key（req:{requirement_id}）关联 constraint 的决策链。 */
function findConstraintTrace(traces: DecisionTrace[], constraint: Constraint): DecisionTrace | null {
  const wanted = constraint.requirement_id;
  const matches = (trace: DecisionTrace) =>
    trace.decision_key === `req:${wanted}` ||
    trace.chain?.decision_key === `req:${wanted}` ||
    trace.chain?.requirement?.requirement_id === wanted;
  return (
    traces.find((trace) => trace.decision_type === "constraint" && matches(trace)) ??
    traces.find(matches) ??
    null
  );
}

function TraceChainView({ trace }: { trace: DecisionTrace }) {
  const chain = trace.chain ?? {};
  const rings = Array.isArray(chain.evidence) ? chain.evidence : [];
  return (
    <div className="why-panel" data-testid="why-panel">
      <h4>决策链（deterministic trace）</h4>
      <p>
        decision_key: <code>{trace.decision_key}</code> · node: <code>{chain.node ?? "—"}</code>
      </p>
      <p>
        rule: <code>{chain.rule?.rule_id ?? "—"}</code> · reason_code:{" "}
        <code>{chain.decision?.reason_code ?? "—"}</code> · result:{" "}
        <code>{chain.decision?.result ?? "—"}</code>
      </p>
      {rings.length === 0 ? (
        <p>该决策无 evidence 环（absence of evidence 只解释 UNKNOWN，不作为正面证据）。</p>
      ) : (
        <ul className="plain-list">
          {rings.map((ring, index) => (
            <li key={`${ring.source_chunk_id ?? "ring"}-${index}`}>
              source_chunk_id: <code>{truncateId(ring.source_chunk_id ?? null)}</code> · span:{" "}
              {formatSpan(ring.char_start, ring.char_end)} · tier: {ring.tier ?? "—"}
              {ring.resolvable === false ? " · 不可解析" : ""}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function ConstraintsTable({
  constraints,
  traces,
}: {
  constraints: Constraint[];
  traces: DecisionTrace[];
}) {
  const [openRequirementId, setOpenRequirementId] = useState<string | null>(null);

  if (constraints.length === 0) {
    return <p className="muted">无硬条件结果。</p>;
  }

  return (
    <table className="data-table" data-testid="constraints-table">
      <caption className="sr-only">硬条件判定结果</caption>
      <thead>
        <tr>
          <th scope="col">requirement</th>
          <th scope="col">类型</th>
          <th scope="col">verdict</th>
          <th scope="col">basis</th>
          <th scope="col">reason_code</th>
          <th scope="col">evidence</th>
          <th scope="col">trace</th>
        </tr>
      </thead>
      <tbody>
        {constraints.map((constraint) => {
          const unknown = isUnknownVerdict(constraint.result);
          const open = openRequirementId === constraint.requirement_id;
          const trace = findConstraintTrace(traces, constraint);
          return (
            <Fragment key={constraint.requirement_id}>
              <tr data-testid="constraint-row">
                <td data-testid="constraint-requirement">
                  <code>{truncateId(constraint.requirement_id)}</code>
                </td>
                <td>{constraint.constraint_type}</td>
                <td data-testid="constraint-verdict" className={verdictClass(constraint.result)}>
                  {verdictLabel(constraint.result)}
                </td>
                <td>{constraint.basis}</td>
                <td data-testid="constraint-reason">{constraint.reason_code}</td>
                <td data-testid="constraint-evidence">
                  {constraint.evidence_ids.length === 0 ? (
                    <span className="muted">0</span>
                  ) : (
                    <span>
                      {constraint.evidence_ids.length} ·{" "}
                      <code>{constraint.evidence_ids.map((id) => truncateId(id, 6)).join(", ")}</code>
                    </span>
                  )}
                </td>
                <td>
                  {unknown ? (
                    <button
                      type="button"
                      data-testid="why-unknown"
                      aria-expanded={open}
                      aria-controls={`why-${constraint.requirement_id}`}
                      onClick={() =>
                        setOpenRequirementId(open ? null : constraint.requirement_id)
                      }
                    >
                      {open ? "收起原因" : "为何 UNKNOWN"}
                    </button>
                  ) : (
                    <span className="muted">{constraint.trace_id ? "已记录" : "—"}</span>
                  )}
                </td>
              </tr>
              {open && unknown ? (
                <tr>
                  <td colSpan={7} id={`why-${constraint.requirement_id}`}>
                    {trace ? (
                      <TraceChainView trace={trace} />
                    ) : (
                      <div className="why-panel" data-testid="why-panel">
                        未找到该 constraint 的决策链记录。
                      </div>
                    )}
                  </td>
                </tr>
              ) : null}
            </Fragment>
          );
        })}
      </tbody>
    </table>
  );
}

"use client";

import { formatScore, formatWeight } from "@/lib/api/format";
import type { ScoreSnapshot } from "@/lib/api/types";

export function ScorePanel({ score }: { score: ScoreSnapshot | null }) {
  if (score === null) {
    return (
      <div className="panel" data-testid="score-panel">
        <h3>评分</h3>
        <p className="muted">尚无评分快照。</p>
      </div>
    );
  }

  return (
    <div className="panel" data-testid="score-panel">
      <h3>评分</h3>
      <dl className="kv">
        <div>
          <dt>total</dt>
          <dd data-testid="score-total">{formatScore(score.total)}</dd>
        </div>
        <div>
          <dt>gate</dt>
          <dd data-testid="score-gate">{score.gate}</dd>
        </div>
        <div>
          <dt>scoring_version</dt>
          <dd>{score.scoring_version ?? "—"}</dd>
        </div>
        <div>
          <dt>flags</dt>
          <dd>{score.flags.length > 0 ? score.flags.join(", ") : "—"}</dd>
        </div>
      </dl>
      {score.per_section.length > 0 ? (
        <table className="data-table">
          <caption className="sr-only">各分项评分</caption>
          <thead>
            <tr>
              <th scope="col">section</th>
              <th scope="col">status</th>
              <th scope="col">score</th>
              <th scope="col">weight</th>
              <th scope="col">applied_weight</th>
              <th scope="col">determinable</th>
            </tr>
          </thead>
          <tbody>
            {score.per_section.map((section) => (
              <tr key={section.section} data-testid="score-section">
                <td>{section.section}</td>
                <td>{section.status}</td>
                <td>{formatScore(section.score)}</td>
                <td>{formatWeight(section.weight)}</td>
                <td>{formatWeight(section.applied_weight)}</td>
                <td>
                  {section.items_determinable}/{section.items_total}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
    </div>
  );
}

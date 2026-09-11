"use client";

import { citationStatusLabel, critiqueStatusLabel, isCritiqueTrusted, truncateId } from "@/lib/api/format";
import type { Critique, CritiqueObservation, CritiquePoint } from "@/lib/api/types";

const DISCLAIMER = "LLM critique does not override deterministic analysis.";

function PointList({ title, points }: { title: string; points?: CritiquePoint[] }) {
  if (!points || points.length === 0) {
    return null;
  }
  return (
    <div className="critique-group">
      <h4>{title}</h4>
      <ul className="plain-list">
        {points.map((point, index) => (
          <li key={`${title}-${index}`}>
            <span className="ref-chip">{point.claim_type ?? "unknown"}</span>{" "}
            {point.claim ?? "—"}
          </li>
        ))}
      </ul>
    </div>
  );
}

function ObservationList({
  title,
  observations,
}: {
  title: string;
  observations?: CritiqueObservation[];
}) {
  if (!observations || observations.length === 0) {
    return null;
  }
  return (
    <div className="critique-group">
      <h4>{title}</h4>
      <ul className="plain-list">
        {observations.map((observation, index) => (
          <li key={`${title}-${index}`}>
            <span className="ref-chip">{observation.claim_type ?? "unknown"}</span>{" "}
            <code>{truncateId(observation.requirement_id ?? null, 6)}</code>{" "}
            {observation.observation ?? "—"}
          </li>
        ))}
      </ul>
    </div>
  );
}

export function CritiquePanel({ critique }: { critique: Critique | null }) {
  const trusted = critique ? isCritiqueTrusted(critique.validation_status, critique.citations_validated) : false;
  const isUnavailable = critique?.status === "unavailable";
  // unavailable 是 EXTERNAL CREDENTIAL BLOCKED（独立状态），不得渲染成"校验失败"。
  const untrusted = critique
    ? !isUnavailable && (critique.validation_status === "rejected" || !critique.citations_validated)
    : false;
  const content = critique?.content ?? {};

  return (
    <div className="panel" data-testid="critique-panel">
      <h3>LLM Critique（解释层，不覆盖确定性结论）</h3>

      {critique === null ? (
        <p className="muted">尚无 critique。</p>
      ) : (
        <>
          <dl className="kv">
            <div>
              <dt>status</dt>
              <dd data-testid="critique-status">{critiqueStatusLabel(critique.status)}</dd>
            </div>
            <div>
              <dt>citation</dt>
              <dd data-testid="citation-status">
                {citationStatusLabel(critique.status, critique.validation_status)}
              </dd>
            </div>
            <div>
              <dt>model</dt>
              <dd>{critique.model}</dd>
            </div>
            <div>
              <dt>version</dt>
              <dd>{critique.version}</dd>
            </div>
          </dl>

          {critique.status === "unavailable" ? (
            <p className="warn" data-testid="critique-unavailable">
              critique unavailable（EXTERNAL CREDENTIAL BLOCKED）
            </p>
          ) : null}

          {untrusted ? (
            <p className="warn" data-testid="critique-untrusted" role="alert">
              Critique not trusted / validation failed —— 该 critique 未通过 citation 校验，仅作留档，不得作为可信证据。
            </p>
          ) : null}

          {trusted ? (
            <div className="critique-content">
              {content.overall_assessment ? (
                <p className="critique-overall">{content.overall_assessment}</p>
              ) : null}
              <PointList title="优势（strengths）" points={content.strengths} />
              <PointList title="差距（gaps）" points={content.gaps} />
              <PointList title="风险（risks）" points={content.risks} />
              <PointList title="歧义（ambiguities）" points={content.ambiguities} />
              <PointList title="待确认问题（questions）" points={content.questions} />
              <ObservationList title="硬条件观察" observations={content.constraint_observations} />
              <ObservationList title="技能观察" observations={content.skill_observations} />
              {content.unknown_acknowledgements && content.unknown_acknowledgements.length > 0 ? (
                <div className="critique-group">
                  <h4>保留的 UNKNOWN</h4>
                  <ul className="plain-list">
                    {content.unknown_acknowledgements.map((item, index) => (
                      <li key={`unknown-${index}`}>{item}</li>
                    ))}
                  </ul>
                </div>
              ) : null}
            </div>
          ) : null}
        </>
      )}

      <p className="disclaimer" data-testid="critique-disclaimer">
        {DISCLAIMER}
      </p>
    </div>
  );
}

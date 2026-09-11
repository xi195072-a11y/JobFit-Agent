// 各资源的类型化调用。所有请求经 client.ts；不在此层做任何计算/推导。

import { apiFetch } from "./client";
import { ApiError } from "./errors";
import type {
  Analysis,
  AnalysisCreateRequest,
  AnalysisList,
  AnalysisRunRequest,
  AnalysisRunResult,
  Constraint,
  Critique,
  DecisionTrace,
  EvidenceList,
  HealthResponse,
  Phase4RunResult,
  ProfileList,
  Report,
  ReportBuildResult,
  RejectRequest,
  Review,
  ReviewRequest,
  ScoreSnapshot,
  SkillMatch,
} from "./types";

type QueryValue = string | number | boolean | undefined | null;

function buildQuery(params: Record<string, QueryValue>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") {
      continue;
    }
    search.set(key, String(value));
  }
  const qs = search.toString();
  return qs ? `?${qs}` : "";
}

function encodeId(id: string): string {
  return encodeURIComponent(id);
}

/** 404 视为「尚无」时返回 null，其它错误继续抛出。 */
async function nullable<T>(loader: () => Promise<T>): Promise<T | null> {
  try {
    return await loader();
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) {
      return null;
    }
    throw error;
  }
}

export function getHealth(): Promise<HealthResponse> {
  return apiFetch<HealthResponse>("/health");
}

export function listAnalyses(
  params: { limit?: number; offset?: number; status?: string } = {},
): Promise<AnalysisList> {
  return apiFetch<AnalysisList>(`/analyses${buildQuery(params)}`);
}

export function getAnalysis(id: string): Promise<Analysis> {
  return apiFetch<Analysis>(`/analyses/${encodeId(id)}`);
}

export function createAnalysis(body: AnalysisCreateRequest): Promise<Analysis> {
  return apiFetch<Analysis>("/analyses", { method: "POST", body });
}

export function runAnalysis(id: string, body: AnalysisRunRequest = {}): Promise<AnalysisRunResult> {
  return apiFetch<AnalysisRunResult>(`/analyses/${encodeId(id)}/run`, { method: "POST", body });
}

export function listConstraints(id: string): Promise<Constraint[]> {
  return apiFetch<Constraint[]>(`/analyses/${encodeId(id)}/constraints`);
}

export function listSkillMatches(id: string): Promise<SkillMatch[]> {
  return apiFetch<SkillMatch[]>(`/analyses/${encodeId(id)}/skill-matches`);
}

export function listTrace(id: string): Promise<DecisionTrace[]> {
  return apiFetch<DecisionTrace[]>(`/analyses/${encodeId(id)}/trace`);
}

export function getScore(id: string): Promise<ScoreSnapshot> {
  return apiFetch<ScoreSnapshot>(`/analyses/${encodeId(id)}/score`);
}

/** score 尚未生成（404）时返回 null。 */
export function getScoreOrNull(id: string): Promise<ScoreSnapshot | null> {
  return nullable(() => getScore(id));
}

export function listEvidence(id: string): Promise<EvidenceList> {
  return apiFetch<EvidenceList>(`/analyses/${encodeId(id)}/evidence`);
}

export function triggerCritique(id: string): Promise<Phase4RunResult> {
  return apiFetch<Phase4RunResult>(`/analyses/${encodeId(id)}/critique`, { method: "POST", body: {} });
}

/** critique 尚未生成（404）时返回 null。 */
export function getCritique(id: string): Promise<Critique | null> {
  return nullable(() => apiFetch<Critique>(`/analyses/${encodeId(id)}/critique`));
}

export function buildReport(id: string): Promise<ReportBuildResult> {
  return apiFetch<ReportBuildResult>(`/analyses/${encodeId(id)}/report`, { method: "POST", body: {} });
}

/** report 尚未生成（404）时返回 null。 */
export function getReport(id: string): Promise<Report | null> {
  return nullable(() => apiFetch<Report>(`/analyses/${encodeId(id)}/report`));
}

export function listReviews(id: string): Promise<Review[]> {
  return apiFetch<Review[]>(`/analyses/${encodeId(id)}/reviews`);
}

export function submitReview(id: string, body: ReviewRequest): Promise<Review> {
  return apiFetch<Review>(`/analyses/${encodeId(id)}/review`, { method: "POST", body });
}

export function rejectAnalysis(id: string, body: RejectRequest): Promise<Review> {
  return apiFetch<Review>(`/analyses/${encodeId(id)}/reject`, { method: "POST", body });
}

export function listProfiles(
  kind: "resume" | "jd",
  params: { limit?: number; offset?: number } = {},
): Promise<ProfileList> {
  return apiFetch<ProfileList>(`/profiles${buildQuery({ kind, ...params })}`);
}

// 与后端 Pydantic response 一一对应的 TS 类型。
// 前端只做展示：所有值均直接来自后端，不做任何推导/重算。

export interface HealthResponse {
  status: string;
  app_env: string;
  pipeline_version: string;
}

// ---------------------------------------------------------------- analysis

export interface Analysis {
  id: string;
  resume_document_id: string;
  jd_document_id: string;
  resume_profile_id: string | null;
  jd_profile_id: string | null;
  status: string;
  current_phase: string | null;
  idempotency_key: string | null;
  pipeline_version: string;
  extraction_schema_version: string;
  prompt_version: string | null;
  ruleset_version: string | null;
  scoring_version: string | null;
  llm_model: string | null;
  embedding_model: string | null;
  llm_attempts_used: number;
  llm_budget_exceeded: boolean;
  created_at: string;
  updated_at: string;
  reused: boolean;
}

export interface AnalysisList {
  items: Analysis[];
  total: number;
  limit: number;
  offset: number;
}

export interface AnalysisCreateRequest {
  resume_document_id: string;
  jd_document_id: string;
  resume_profile_id?: string | null;
  jd_profile_id?: string | null;
  idempotency_key?: string | null;
}

export interface AnalysisRunRequest {
  requeue?: boolean;
}

export interface AnalysisRunResult {
  analysis_id: string;
  status: string;
  gate: string | null;
  score_total: number | null;
  constraint_count: number;
  skill_match_count: number;
  trace_count: number;
  flags: string[];
  errors: string[];
}

// ---------------------------------------------------------------- constraints

export type Verdict = string;

export interface Constraint {
  requirement_id: string;
  constraint_type: string;
  result: Verdict;
  basis: string;
  ruleset_version: string;
  reason_code: string;
  evidence_ids: string[];
  trace_id: string | null;
  note: string | null;
}

// ---------------------------------------------------------------- skills

export interface SkillMatch {
  jd_requirement_id: string;
  resume_skill_id: string | null;
  status: string;
  ruleset_version: string;
  norm_used: string | null;
  score_contribution: number;
  evidence_ids: string[];
  trace_id: string | null;
}

// ---------------------------------------------------------------- trace

export interface TraceRequirement {
  requirement_id?: string;
  req_type?: string;
  operator?: string;
  value?: unknown;
  is_hard?: boolean;
  source_text?: string;
  anchors?: unknown[];
}

export interface TraceRule {
  rule_id?: string;
  ruleset_version?: string;
  params?: unknown;
}

export interface TraceDecision {
  result?: string;
  basis?: string;
  reason_code?: string;
  explanation?: string;
}

export interface TraceEvidenceRing {
  source_chunk_id?: string;
  char_start?: number;
  char_end?: number;
  page?: number | null;
  span_sha256?: string;
  tier?: string;
  resolvable?: boolean;
}

export interface TraceChain {
  decision_key?: string;
  decision_type?: string;
  node?: string;
  requirement?: TraceRequirement;
  normalization?: Record<string, unknown>;
  rule?: TraceRule;
  evidence?: TraceEvidenceRing[];
  decision?: TraceDecision;
  score_contribution?: Record<string, unknown>;
  versions?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface DecisionTrace {
  decision_type: string;
  decision_key: string;
  chain: TraceChain;
}

// ---------------------------------------------------------------- score

export interface ScoreSection {
  section: string;
  weight: number;
  applied_weight: number;
  score: number | null;
  status: string;
  items_total: number;
  items_determinable: number;
  detail: Record<string, unknown>;
}

export interface ScoreSnapshot {
  analysis_id: string;
  kind: string;
  total: number;
  gate: string;
  scoring_version: string | null;
  flags: string[];
  per_section: ScoreSection[];
}

// ---------------------------------------------------------------- evidence

export interface EvidenceReference {
  kind: string;
  ref_id: string;
  label: string | null;
  tier: string | null;
}

export interface EvidenceRef {
  source_chunk_id: string;
  char_start: number;
  char_end: number;
  page: number | null;
  span_sha256: string;
  resolvable: boolean;
  referenced_by: EvidenceReference[];
}

export interface EvidenceList {
  analysis_id: string;
  items: EvidenceRef[];
  total: number;
}

// ---------------------------------------------------------------- critique

export interface CritiquePoint {
  category?: string;
  claim?: string;
  claim_type?: string;
  evidence_refs?: unknown[];
}

export interface CritiqueObservation {
  requirement_id?: string;
  observation?: string;
  claim_type?: string;
  evidence_refs?: unknown[];
}

export interface CritiqueContent {
  schema_version?: string;
  overall_assessment?: string;
  strengths?: CritiquePoint[];
  gaps?: CritiquePoint[];
  risks?: CritiquePoint[];
  ambiguities?: CritiquePoint[];
  questions?: CritiquePoint[];
  constraint_observations?: CritiqueObservation[];
  skill_observations?: CritiqueObservation[];
  unknown_acknowledgements?: string[];
  reason?: string;
  [key: string]: unknown;
}

export interface Critique {
  id: string;
  analysis_id: string;
  version: number;
  status: string;
  provider: string;
  model: string;
  prompt_version: string | null;
  schema_version: string;
  validation_status: string;
  citations_validated: boolean;
  fingerprint: string;
  content: CritiqueContent;
  latency_ms: number | null;
  created_at: string;
}

// ---------------------------------------------------------------- report

export interface ReportSectionData {
  type: string;
  content?: Record<string, unknown>;
  rows?: Record<string, unknown>[];
  [key: string]: unknown;
}

export interface ReportContent {
  sections?: ReportSectionData[];
  meta?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface Report {
  id: string;
  analysis_id: string;
  version: number;
  stage: string;
  content: ReportContent;
  content_md: string;
  meta: Record<string, unknown>;
  fingerprint: string;
  published_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ReportBuildResult {
  analysis_id: string;
  version: number;
  stage: string;
  valid: boolean;
  reused: boolean;
  issues: string[];
}

export interface Phase4RunResult {
  analysis_id: string;
  status: string;
  critique_status: string | null;
  critique_validation: string | null;
  report_version: number | null;
  report_stage: string | null;
  errors: string[];
}

// ---------------------------------------------------------------- review

export interface Review {
  id: string;
  analysis_id: string;
  decision: string;
  comments: string | null;
  overrides: Record<string, unknown> | null;
  reviewed_by: string | null;
  from_state: string | null;
  to_state: string | null;
  version: number;
  created_at: string;
  updated_at: string;
}

export type ReviewDecision = "approve" | "reject" | "request_changes";

export interface ReviewRequest {
  decision: ReviewDecision;
  comments?: string | null;
  reviewed_by?: string | null;
  overrides?: Record<string, unknown> | null;
}

export interface RejectRequest {
  decision: "reject";
  comments: string;
  reviewed_by?: string | null;
}

// ---------------------------------------------------------------- profiles

export interface ProfileSummary {
  profile_id: string;
  kind: string;
  document_id: string;
  parsed_document_id: string;
  reference: string;
  pipeline_version: string;
  extraction_schema_version: string;
  prompt_version: string;
  llm_model: string;
  extracted_at: string;
  warning_count: number;
}

export interface ProfileList {
  kind: string;
  items: ProfileSummary[];
  total: number;
}

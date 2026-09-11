// 纯函数格式化：verdict 文案/颜色类、status 文案、时间、分数、spans。
// 只做「展示形态转换」，不改变任何语义：UNKNOWN 绝不映射为失败。

const PLACEHOLDER = "—";

// ---------------------------------------------------------------- verdict

/** 三值 verdict 原文（MET / NOT_MET / UNKNOWN），未知输入原样返回。 */
export function verdictLabel(result: string): string {
  const key = result.trim().toUpperCase();
  if (key === "MET" || key === "NOT_MET" || key === "UNKNOWN") {
    return key;
  }
  return result;
}

/** verdict 的说明文案；UNKNOWN 明确表达「证据不足」，不是失败。 */
export function verdictDescription(result: string): string {
  switch (verdictLabel(result)) {
    case "MET":
      return "满足（MET）";
    case "NOT_MET":
      return "不满足（NOT_MET）";
    case "UNKNOWN":
      return "未知：证据不足，需人工确认（UNKNOWN）";
    default:
      return "未知结论";
  }
}

/** verdict 对应的 CSS 类；UNKNOWN 拥有独立类以便与 NOT_MET 区分。 */
export function verdictClass(result: string): string {
  switch (verdictLabel(result)) {
    case "MET":
      return "verdict verdict-met";
    case "NOT_MET":
      return "verdict verdict-not-met";
    case "UNKNOWN":
      return "verdict verdict-unknown";
    default:
      return "verdict verdict-other";
  }
}

export function isUnknownVerdict(result: string): boolean {
  return verdictLabel(result) === "UNKNOWN";
}

// ---------------------------------------------------------------- skill status

/** 技能匹配状态原文（matched / partial / unknown 等）。 */
export function skillStatusLabel(status: string): string {
  const key = status.trim().toLowerCase();
  if (key === "matched" || key === "partial" || key === "unknown" || key === "missing" || key === "claimed_only") {
    return key;
  }
  return status;
}

export function skillStatusDescription(status: string): string {
  switch (skillStatusLabel(status)) {
    case "matched":
      return "已匹配（matched）";
    case "partial":
      return "部分匹配（partial）";
    case "unknown":
      return "未知：证据不足（unknown）";
    case "missing":
      return "缺失（missing）";
    case "claimed_only":
      return "仅自述（claimed_only）";
    default:
      return status;
  }
}

export function skillStatusClass(status: string): string {
  return `skill-status skill-status-${skillStatusLabel(status).replace(/[^a-z_]/g, "")}`;
}

// ---------------------------------------------------------------- analysis status

const ANALYSIS_STATUS_LABELS: Record<string, string> = {
  queued: "排队中（queued）",
  running: "执行中（running）",
  succeeded: "确定性分析完成（succeeded）",
  awaiting_review: "等待人工评审（awaiting_review）",
  finalized: "已定稿（finalized）",
  rejected: "已拒绝（rejected）",
  failed: "失败（failed）",
};

export function analysisStatusLabel(status: string): string {
  return ANALYSIS_STATUS_LABELS[status] ?? status;
}

export function analysisStatusClass(status: string): string {
  return `status-badge status-${status.replace(/[^a-z_]/g, "")}`;
}

// ---------------------------------------------------------------- critique / report

export function critiqueStatusLabel(status: string): string {
  if (status === "ok") {
    return "ok";
  }
  if (status === "unavailable") {
    return "unavailable";
  }
  return status;
}

/**
 * citation 校验文案。
 *
 * - `unavailable` 是独立的 EXTERNAL CREDENTIAL BLOCKED 状态（ADR-036），
 *   既不是 Validated 也不是 Rejected —— 必须如实展示，不得渲染成校验失败；
 * - 其余情况仅三值：Validated / Rejected / Pending。
 */
export function citationStatusLabel(critiqueStatus: string, validationStatus: string): string {
  if (critiqueStatus === "unavailable") {
    return "Unavailable（EXTERNAL CREDENTIAL BLOCKED）";
  }
  if (validationStatus === "validated") {
    return "Validated";
  }
  if (validationStatus === "rejected") {
    return "Rejected";
  }
  return "Pending";
}

export function isCritiqueTrusted(
  validationStatus: string,
  citationsValidated: boolean,
): boolean {
  return validationStatus === "validated" && citationsValidated;
}

const REPORT_STAGE_LABELS: Record<string, string> = {
  draft: "draft",
  validated: "validated",
  final: "final",
};

export function reportStageLabel(stage: string): string {
  return REPORT_STAGE_LABELS[stage] ?? stage;
}

// ---------------------------------------------------------------- numbers / time

/** 分数格式化；数值直接来自后端，null/undefined -> 占位符。 */
export function formatScore(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return PLACEHOLDER;
  }
  return String(Number(value.toFixed(2)));
}

export function formatWeight(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return PLACEHOLDER;
  }
  return String(Number(value.toFixed(4)));
}

/** ISO 时间 -> "YYYY-MM-DD HH:mm UTC"（固定 UTC，跨时区确定）。 */
export function formatDateTime(value: string | null | undefined): string {
  if (!value) {
    return PLACEHOLDER;
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    `${date.getUTCFullYear()}-${pad(date.getUTCMonth() + 1)}-${pad(date.getUTCDate())}` +
    ` ${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())} UTC`
  );
}

export function formatDate(value: string | null | undefined): string {
  if (!value) {
    return PLACEHOLDER;
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getUTCFullYear()}-${pad(date.getUTCMonth() + 1)}-${pad(date.getUTCDate())}`;
}

// ---------------------------------------------------------------- ids / spans

export function truncateId(value: string | null | undefined, head = 8): string {
  if (!value) {
    return PLACEHOLDER;
  }
  return value.length > head ? `${value.slice(0, head)}…` : value;
}

export function truncateHash(value: string | null | undefined, head = 12): string {
  if (!value) {
    return PLACEHOLDER;
  }
  return value.length > head ? `${value.slice(0, head)}…` : value;
}

/** evidence span 文本：char_start-char_end。 */
export function formatSpan(charStart: number | null | undefined, charEnd: number | null | undefined): string {
  if (charStart === null || charStart === undefined || charEnd === null || charEnd === undefined) {
    return PLACEHOLDER;
  }
  return `${charStart}-${charEnd}`;
}

// ---------------------------------------------------------------- report sections

const SECTION_TITLES: Record<string, string> = {
  summary: "摘要",
  hard_constraints: "硬性条件",
  skills: "技能匹配",
  evidence: "证据引用",
  unknowns: "未知项（UNKNOWN）",
  score: "评分",
  critique: "LLM Critique",
  next_actions: "建议下一步",
  review_status: "评审状态",
  strengths: "优势",
  gaps: "差距",
  risks: "风险",
  critique_unknowns: "Critique 未知项",
};

export function sectionTitle(type: string): string {
  const known = SECTION_TITLES[type];
  if (known) {
    return `${known}（${type}）`;
  }
  return type;
}

/** 通用值 -> 可读文本（primitive 直出，其余 JSON）。 */
export function formatCellValue(value: unknown): string {
  if (value === null || value === undefined) {
    return PLACEHOLDER;
  }
  if (typeof value === "string") {
    return value;
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  if (Array.isArray(value)) {
    if (value.length === 0) {
      return PLACEHOLDER;
    }
    return value.map((item) => formatCellValue(item)).join(", ");
  }
  return JSON.stringify(value);
}

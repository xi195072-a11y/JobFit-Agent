// 统一错误契约：ApiError + normalizeError + 错误码/状态码文案映射（纯函数，无副作用）。
// 错误响应体：{ detail: any, error: { code, message, details, request_id, retryable } }

export interface ApiErrorBody {
  code?: unknown;
  message?: unknown;
  details?: unknown;
  request_id?: unknown;
  retryable?: unknown;
}

const STATUS_CODES: Record<number, string> = {
  400: "VALIDATION_FAILED",
  404: "NOT_FOUND",
  409: "CONFLICT",
  422: "VALIDATION_FAILED",
  500: "INTERNAL_ERROR",
  503: "CONFIGURATION_ERROR",
};

/** 可安全重试的错误码（与后端 RETRYABLE_CODES 保持一致）。 */
export const RETRYABLE_CODES: ReadonlySet<string> = new Set(["CONFLICT", "LEASE_LOST"]);

const CODE_MESSAGES: Record<string, string> = {
  VALIDATION_FAILED: "请求校验失败（422）",
  REQUEST_VALIDATION_FAILED: "请求参数不合法（422）",
  PARSE_FAILED: "文档解析失败",
  NOT_FOUND: "资源不存在（404）",
  CONFLICT: "状态冲突或并发抢占（409）",
  RESERVATION_FAILED: "资源预约失败（409）",
  LEASE_LOST: "执行租约丢失，可重试",
  CONFIGURATION_ERROR: "依赖未配置或不可用（503）",
  HTTP_ERROR: "请求失败",
  INTERNAL_ERROR: "服务器内部错误（500）",
  NETWORK_ERROR: "网络错误：无法连接后端",
};

/** HTTP 状态码 -> 错误码兜底映射（响应体无 error.code 时使用）。 */
export function errorCodeForStatus(status: number | null | undefined): string {
  if (status === null || status === undefined || status < 100) {
    return "NETWORK_ERROR";
  }
  return STATUS_CODES[status] ?? "HTTP_ERROR";
}

/** 错误码 -> 面向用户的文案；未知 code 回退到 message 或通用文案。 */
export function errorMessageForCode(code: string, fallback?: string | null): string {
  return CODE_MESSAGES[code] ?? (fallback && fallback.trim()) ?? "请求失败";
}

export interface ApiErrorOptions {
  code: string;
  message: string;
  status?: number | null;
  details?: unknown;
  requestId?: string | null;
  retryable?: boolean;
}

export class ApiError extends Error {
  readonly code: string;
  readonly status: number | null;
  readonly details: unknown;
  readonly requestId: string | null;
  readonly retryable: boolean;

  constructor(options: ApiErrorOptions) {
    super(options.message);
    this.name = "ApiError";
    this.code = options.code;
    this.status = options.status ?? null;
    this.details = options.details;
    this.requestId = options.requestId ?? null;
    this.retryable = options.retryable ?? false;
  }
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null ? (value as Record<string, unknown>) : null;
}

/**
 * 把任意输入归一化为 ApiError：
 * - 已是 ApiError -> 原样返回；
 * - 后端错误体 { error: { code, message, details, request_id, retryable } } -> 解析；
 * - { detail: "纯文本" | object } -> 作为 message / details；
 * - Error / string -> message；
 * - HTTP 状态码兜底 code。
 */
export function normalizeError(input: unknown, status?: number | null): ApiError {
  if (input instanceof ApiError) {
    return input;
  }

  const record = asRecord(input);
  const errorBody = record ? asRecord(record.error) : null;

  let code: string | null = null;
  let message: string | null = null;
  let details: unknown;
  let requestId: string | null = null;
  let retryable: boolean | null = null;

  if (errorBody) {
    if (typeof errorBody.code === "string" && errorBody.code) {
      code = errorBody.code;
    }
    if (typeof errorBody.message === "string" && errorBody.message) {
      message = errorBody.message;
    }
    if ("details" in errorBody) {
      details = errorBody.details;
    }
    if (typeof errorBody.request_id === "string" && errorBody.request_id) {
      requestId = errorBody.request_id;
    }
    if (typeof errorBody.retryable === "boolean") {
      retryable = errorBody.retryable;
    }
  }

  if (message === null && record && "detail" in record) {
    const detail = record.detail;
    if (typeof detail === "string" && detail) {
      message = detail;
    } else if (detail !== null && detail !== undefined) {
      details = details === undefined ? detail : details;
      const detailRecord = asRecord(detail);
      if (detailRecord) {
        const nested = asRecord(detailRecord.error);
        if (nested && typeof nested.message === "string" && nested.message) {
          message = nested.message;
        }
      }
    }
  }

  const resolvedStatus = status ?? null;
  if (code === null) {
    code = errorCodeForStatus(resolvedStatus);
  }
  if (message === null) {
    if (input instanceof Error && input.message) {
      message = input.message;
    } else if (typeof input === "string" && input) {
      message = input;
    }
  }
  if (message === null) {
    message = errorMessageForCode(code);
  }
  if (retryable === null) {
    retryable = resolvedStatus === null ? true : RETRYABLE_CODES.has(code);
  }

  return new ApiError({
    code,
    message,
    status: resolvedStatus,
    details,
    requestId,
    retryable,
  });
}

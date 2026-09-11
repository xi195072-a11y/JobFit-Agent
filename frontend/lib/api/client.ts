// 唯一 HTTP 出口：base URL、JSON、错误归一、X-Request-ID 透传。
// 页面/组件不得直接调用 fetch。

import { ApiError, normalizeError } from "./errors";

/** 后端 base URL（来自 NEXT_PUBLIC_API_BASE_URL，缺失时不回退硬编码地址）。 */
export function getApiBaseUrl(): string {
  const raw = process.env.NEXT_PUBLIC_API_BASE_URL ?? "";
  return raw.trim().replace(/\/+$/, "");
}

export interface ApiFetchOptions {
  method?: string;
  body?: unknown;
  headers?: Record<string, string>;
  signal?: AbortSignal;
  /** 显式透传到后端请求头 X-Request-ID（非 PII）。 */
  requestId?: string;
}

async function readJson(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text) {
    return null;
  }
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return text;
  }
}

export async function apiFetch<T>(path: string, options: ApiFetchOptions = {}): Promise<T> {
  const base = getApiBaseUrl();
  if (!base) {
    throw new ApiError({
      code: "CONFIGURATION_ERROR",
      message: "未配置 NEXT_PUBLIC_API_BASE_URL，无法连接后端",
      retryable: false,
    });
  }

  const url = /^https?:\/\//i.test(path) ? path : `${base}${path.startsWith("/") ? path : `/${path}`}`;
  const headers: Record<string, string> = { Accept: "application/json" };
  if (options.headers) {
    Object.assign(headers, options.headers);
  }
  if (options.requestId) {
    headers["X-Request-ID"] = options.requestId;
  }

  const init: RequestInit = {
    method: options.method ?? "GET",
    headers,
    cache: "no-store",
  };
  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(options.body);
  }
  if (options.signal) {
    init.signal = options.signal;
  }

  let response: Response;
  try {
    response = await fetch(url, init);
  } catch (error) {
    throw normalizeError(error, null);
  }

  const headerRequestId = response.headers.get("X-Request-ID");
  const payload = response.status === 204 ? null : await readJson(response);

  if (!response.ok) {
    const normalized = normalizeError(payload, response.status);
    throw new ApiError({
      code: normalized.code,
      message: normalized.message,
      status: response.status,
      details: normalized.details,
      requestId: normalized.requestId ?? headerRequestId,
      retryable: normalized.retryable,
    });
  }

  return payload as T;
}

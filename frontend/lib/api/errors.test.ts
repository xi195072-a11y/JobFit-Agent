import { describe, expect, it } from "vitest";

import {
  ApiError,
  RETRYABLE_CODES,
  errorCodeForStatus,
  errorMessageForCode,
  normalizeError,
} from "./errors";

describe("normalizeError", () => {
  it("解析后端统一错误体 { error: { code, message, details, request_id, retryable } }", () => {
    const error = normalizeError(
      {
        detail: "phase 4 前置条件不满足",
        error: {
          code: "CONFLICT",
          message: "illegal state transition",
          details: { from: "running", to: "finalized" },
          request_id: "req-123",
          retryable: true,
        },
      },
      409,
    );

    expect(error).toBeInstanceOf(ApiError);
    expect(error.code).toBe("CONFLICT");
    expect(error.message).toBe("illegal state transition");
    expect(error.details).toEqual({ from: "running", to: "finalized" });
    expect(error.requestId).toBe("req-123");
    expect(error.retryable).toBe(true);
    expect(error.status).toBe(409);
  });

  it("能处理纯文本 detail", () => {
    const error = normalizeError({ detail: "rejection requires a reason" }, 422);

    expect(error.message).toBe("rejection requires a reason");
    expect(error.code).toBe("VALIDATION_FAILED");
    expect(error.retryable).toBe(false);
  });

  it("能在缺省 retryable 时按错误码判定 retryable", () => {
    const error = normalizeError({ error: { code: "CONFLICT", message: "conflict" } }, 409);

    expect(error.retryable).toBe(true);
    expect(RETRYABLE_CODES.has("CONFLICT")).toBe(true);
  });

  it("能用 HTTP 状态码兜底错误码（404 -> NOT_FOUND）", () => {
    const error = normalizeError({ detail: "not found" }, 404);

    expect(error.code).toBe("NOT_FOUND");
    expect(error.status).toBe(404);
    expect(errorCodeForStatus(404)).toBe("NOT_FOUND");
    expect(errorCodeForStatus(503)).toBe("CONFIGURATION_ERROR");
    expect(errorCodeForStatus(500)).toBe("INTERNAL_ERROR");
  });

  it("网络错误（无 status）归类为可重试", () => {
    const error = normalizeError(new Error("Failed to fetch"), null);

    expect(error.code).toBe("NETWORK_ERROR");
    expect(error.message).toBe("Failed to fetch");
    expect(error.retryable).toBe(true);
    expect(error.status).toBeNull();
  });

  it("已是 ApiError 时原样返回", () => {
    const original = new ApiError({ code: "NOT_FOUND", message: "x", status: 404 });

    expect(normalizeError(original)).toBe(original);
  });

  it("提供错误码文案映射", () => {
    expect(errorMessageForCode("NOT_FOUND")).toContain("404");
    expect(errorMessageForCode("UNKNOWN_CODE", "自定义")).toBe("自定义");
  });
});

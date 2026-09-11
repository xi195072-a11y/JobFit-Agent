"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { type ApiError, normalizeError } from "@/lib/api/errors";

// 通用异步状态：loading / error / ready + 重试。所有数据页面复用它。
export type AsyncState<T> =
  | { status: "loading" }
  | { status: "error"; error: ApiError }
  | { status: "ready"; data: T };

/**
 * 按 key 触发加载；key 变化或调用 reload() 时重新请求。
 * 加载态由 (key, token) 派生，避免在 effect 内同步 setState。
 * loader 通过 ref 持有，避免因函数标识变化造成重复请求。
 */
export function useAsync<T>(key: string, loader: () => Promise<T>) {
  const [token, setToken] = useState(0);
  const [entry, setEntry] = useState<{ key: string; token: number; state: AsyncState<T> } | null>(
    null,
  );
  const loaderRef = useRef(loader);

  useEffect(() => {
    loaderRef.current = loader;
  });

  useEffect(() => {
    let active = true;
    loaderRef.current().then(
      (data) => {
        if (active) {
          setEntry({ key, token, state: { status: "ready", data } });
        }
      },
      (error: unknown) => {
        if (active) {
          setEntry({ key, token, state: { status: "error", error: normalizeError(error) } });
        }
      },
    );
    return () => {
      active = false;
    };
  }, [key, token]);

  const state: AsyncState<T> =
    entry && entry.key === key && entry.token === token ? entry.state : { status: "loading" };

  const reload = useCallback(() => setToken((value) => value + 1), []);
  return { state, reload };
}

export function Loading({ label = "加载中…" }: { label?: string }) {
  return (
    <div className="state-block" data-testid="loading" role="status" aria-live="polite">
      {label}
    </div>
  );
}

export function ErrorBlock({ error, onRetry }: { error: ApiError; onRetry?: () => void }) {
  return (
    <div className="state-block state-error" data-testid="error" role="alert">
      <p className="state-error-message">{error.message}</p>
      <p className="state-error-meta">
        <span>
          code: <code>{error.code}</code>
        </span>
        {error.status !== null ? <span>HTTP {error.status}</span> : null}
        {error.retryable ? <span>可重试</span> : null}
      </p>
      {error.requestId ? (
        <p className="state-error-meta">
          request_id: <code>{error.requestId}</code>
        </p>
      ) : null}
      {onRetry ? (
        <button type="button" data-testid="retry" onClick={onRetry}>
          重试
        </button>
      ) : null}
    </div>
  );
}

export function EmptyBlock({ message = "暂无数据" }: { message?: string }) {
  return (
    <div className="state-block" data-testid="empty" role="status" aria-live="polite">
      {message}
    </div>
  );
}

export function NotFoundBlock({ message = "未找到" }: { message?: string }) {
  return (
    <div className="state-block" data-testid="not-found" role="status" aria-live="polite">
      {message}
    </div>
  );
}

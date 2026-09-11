"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";

import { ErrorBlock, Loading, NotFoundBlock, useAsync } from "@/components/AsyncState";
import { ReviewHistory } from "@/components/ReviewHistory";
import { type ApiError, normalizeError } from "@/lib/api/errors";
import { analysisStatusLabel, truncateId } from "@/lib/api/format";
import { getAnalysis, listReviews, rejectAnalysis, submitReview } from "@/lib/api/resources";
import type { Analysis, Review } from "@/lib/api/types";

interface ReviewData {
  analysis: Analysis;
  reviews: Review[];
}

async function loadReview(id: string): Promise<ReviewData> {
  const [analysis, reviews] = await Promise.all([getAnalysis(id), listReviews(id)]);
  return { analysis, reviews };
}

export default function ReviewPage() {
  const params = useParams<{ id: string }>();
  const id = params?.id ?? "";
  const review = useAsync(`review:${id}`, () => loadReview(id));

  const [reason, setReason] = useState("");
  const [localError, setLocalError] = useState("");
  const [actionError, setActionError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const data = review.state.status === "ready" ? review.state.data : null;
  const canReview = data?.analysis.status === "awaiting_review";

  async function handleApprove() {
    if (!canReview) {
      return;
    }
    setBusy("approve");
    setActionError(null);
    setLocalError("");
    try {
      await submitReview(id, { decision: "approve" });
      review.reload();
    } catch (error) {
      setActionError(normalizeError(error));
    } finally {
      setBusy(null);
    }
  }

  async function handleReject() {
    if (!canReview) {
      return;
    }
    const trimmed = reason.trim();
    if (trimmed === "") {
      setLocalError("拒绝必须填写理由（comments），不会发送空理由。");
      return;
    }
    setBusy("reject");
    setActionError(null);
    setLocalError("");
    try {
      await rejectAnalysis(id, { decision: "reject", comments: trimmed });
      setReason("");
      review.reload();
    } catch (error) {
      setActionError(normalizeError(error));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div data-testid="review-page">
      <div className="page-header">
        <h1>人工评审 {truncateId(id, 12)}</h1>
        <p>
          <Link href={`/analyses/${id}`}>返回分析详情</Link>
        </p>
      </div>

      {review.state.status === "loading" ? <Loading /> : null}
      {review.state.status === "error" ? (
        review.state.error.status === 404 ? (
          <NotFoundBlock message="未找到该 analysis。" />
        ) : (
          <ErrorBlock error={review.state.error} onRetry={review.reload} />
        )
      ) : null}

      {data ? (
        <>
          <div className="panel">
            <h2>当前状态</h2>
            <p>
              <span data-testid="review-status">{data.analysis.status}</span>
              <span className="muted"> · {analysisStatusLabel(data.analysis.status)}</span>
            </p>

            {!canReview ? (
              <p className="warn" role="status">
                当前状态为 {data.analysis.status}，仅 awaiting_review 允许 approve / reject；按钮已禁用。
              </p>
            ) : null}

            <div className="actions">
              <button
                type="button"
                data-testid="approve"
                disabled={!canReview || busy !== null}
                onClick={handleApprove}
              >
                {busy === "approve" ? "提交中…" : "通过（approve）"}
              </button>
            </div>

            <div className="field">
              <label htmlFor="reject-reason">拒绝理由（必填）</label>
              <textarea
                id="reject-reason"
                data-testid="reject-reason"
                rows={3}
                value={reason}
                onChange={(event) => setReason(event.target.value)}
                aria-describedby="reject-reason-help"
                disabled={!canReview || busy !== null}
              />
              <span id="reject-reason-help" className="muted">
                理由为空时拒绝按钮不可用，且不会发送空理由。
              </span>
            </div>
            <div className="actions">
              <button
                type="button"
                data-testid="reject"
                disabled={!canReview || busy !== null || reason.trim() === ""}
                onClick={handleReject}
              >
                {busy === "reject" ? "提交中…" : "拒绝（reject）"}
              </button>
            </div>

            <div aria-live="polite">
              {localError ? (
                <p className="warn" role="alert">
                  {localError}
                </p>
              ) : null}
              {actionError ? <ErrorBlock error={actionError} /> : null}
            </div>
          </div>

          <ReviewHistory reviews={data.reviews} />
        </>
      ) : null}
    </div>
  );
}

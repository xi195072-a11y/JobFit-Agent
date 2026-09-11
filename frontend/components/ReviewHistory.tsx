"use client";

import { formatDateTime } from "@/lib/api/format";
import type { Review } from "@/lib/api/types";

export function ReviewHistory({ reviews }: { reviews: Review[] }) {
  if (reviews.length === 0) {
    return (
      <div className="panel" data-testid="review-history">
        <h3>评审历史</h3>
        <p className="muted">暂无评审记录。</p>
      </div>
    );
  }

  return (
    <div className="panel" data-testid="review-history">
      <h3>评审历史（{reviews.length}）</h3>
      <table className="data-table">
        <caption className="sr-only">评审历史</caption>
        <thead>
          <tr>
            <th scope="col">decision</th>
            <th scope="col">状态转移</th>
            <th scope="col">comments</th>
            <th scope="col">reviewed_by</th>
            <th scope="col">时间</th>
          </tr>
        </thead>
        <tbody>
          {reviews.map((review) => (
            <tr key={review.id} data-testid="review-row">
              <td>{review.decision}</td>
              <td>
                {review.from_state ?? "—"} → {review.to_state ?? "—"}
              </td>
              <td>{review.comments ?? "—"}</td>
              <td>{review.reviewed_by ?? "—"}</td>
              <td>{formatDateTime(review.created_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

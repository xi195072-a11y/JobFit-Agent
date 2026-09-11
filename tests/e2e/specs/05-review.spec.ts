import { expect, test } from "@playwright/test";

import { BACKEND_URL, getJson, resetBackend, runPhase4, seedAnalysis } from "./helpers";

test.describe("HITL review 生命周期（§23-9/10、§49）", () => {
  test("Case A：awaiting_review -> approve -> finalized", async ({ page, request }) => {
    await resetBackend(request);
    const analysisId = await seedAnalysis(request, { key: "e2e-approve", extraction: "match" });
    await runPhase4(request, analysisId);

    await page.goto(`/review/${analysisId}`);
    await expect(page.getByTestId("review-page")).toBeVisible();
    await expect(page.getByTestId("review-status")).toHaveText("awaiting_review");

    await page.getByTestId("approve").click();
    await expect(page.getByTestId("review-status")).toHaveText("finalized");
    await expect(page.getByTestId("review-row")).toHaveCount(1);

    const analysis = await getJson<{ status: string }>(request, `/analyses/${analysisId}`);
    expect(analysis.status).toBe("finalized");
    const report = await getJson<{ stage: string; published_at: string | null }>(
      request,
      `/analyses/${analysisId}/report`,
    );
    expect(report.stage).toBe("final");
    expect(report.published_at).not.toBeNull();

    // final 不可再改：再次 approve 被状态机拒绝
    const again = await request.post(`${BACKEND_URL}/analyses/${analysisId}/review`, {
      data: { decision: "approve" },
    });
    expect(again.status()).toBe(422);
  });

  test("Case B/C：reject 必须填理由 -> rejected；requeue -> queued", async ({ page, request }) => {
    await resetBackend(request);
    const analysisId = await seedAnalysis(request, { key: "e2e-reject", extraction: "match" });
    await runPhase4(request, analysisId);

    await page.goto(`/review/${analysisId}`);
    await expect(page.getByTestId("review-status")).toHaveText("awaiting_review");

    // 理由为空时拒绝按钮不可用（不会发送空理由）
    await expect(page.getByTestId("reject")).toBeDisabled();
    await page.getByTestId("reject-reason").fill("证据不足，需要补充材料");
    await expect(page.getByTestId("reject")).toBeEnabled();
    await page.getByTestId("reject").click();

    await expect(page.getByTestId("review-status")).toHaveText("rejected");
    const rejected = await getJson<{ status: string }>(request, `/analyses/${analysisId}`);
    expect(rejected.status).toBe("rejected");

    // Case C：rejected -> requeue(request_changes) -> queued（API 生命周期）
    const requeue = await request.post(`${BACKEND_URL}/analyses/${analysisId}/review`, {
      data: { decision: "request_changes", comments: "requeue after rejection" },
    });
    expect(requeue.status()).toBe(200);
    const queued = await getJson<{ status: string }>(request, `/analyses/${analysisId}`);
    expect(queued.status).toBe("queued");

    // UI 如实反映：非 awaiting_review 时 approve/reject 禁用
    await page.reload();
    await expect(page.getByTestId("review-status")).toHaveText("queued");
    await expect(page.getByTestId("approve")).toBeDisabled();
    await expect(page.getByTestId("reject")).toBeDisabled();

    // 审计留痕：两次 action 各一行
    const reviews = await getJson<unknown[]>(request, `/analyses/${analysisId}/reviews`);
    expect(reviews.length).toBe(2);
  });

  test("非法转移被拒绝：succeeded 不可直接 approve（UI 禁用 + API 422）", async ({
    page,
    request,
  }) => {
    await resetBackend(request);
    const analysisId = await seedAnalysis(request, { key: "e2e-illegal", extraction: "match" });

    // 未走 critique 管线 => succeeded，不能直接 approve
    const illegal = await request.post(`${BACKEND_URL}/analyses/${analysisId}/review`, {
      data: { decision: "approve" },
    });
    expect(illegal.status()).toBe(422);
    const body = (await illegal.json()) as { error?: { code?: string } };
    expect(body.error?.code).toBe("VALIDATION_FAILED");

    // finalize 前置：无 validated 报告 => 422
    const finalize = await request.post(`${BACKEND_URL}/analyses/${analysisId}/finalize`, {
      data: { decision: "approve" },
    });
    expect(finalize.status()).toBe(422);

    await page.goto(`/review/${analysisId}`);
    await expect(page.getByTestId("review-status")).toHaveText("succeeded");
    await expect(page.getByTestId("approve")).toBeDisabled();
    await expect(page.getByTestId("review-page")).toContainText("仅 awaiting_review 允许 approve / reject");
  });
});

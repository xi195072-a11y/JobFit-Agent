import { expect, test } from "@playwright/test";

import { getJson, resetBackend, seedAnalysis, selectFirstOption } from "./helpers";

test.describe("Phase 5 基础流程（§23-1/2）", () => {
  test("通过 UI 创建 analysis 并展示状态", async ({ page, request }) => {
    await resetBackend(request);
    // 先用 API 造出可选的 profile 候选（真实 HTTP）
    await seedAnalysis(request, { key: "e2e-create-seed" });

    await page.goto("/analyses/new");
    await expect(page.getByTestId("create-analysis")).toBeVisible();

    await selectFirstOption(page.getByTestId("resume-select"));
    await selectFirstOption(page.getByTestId("jd-select"));
    await page.getByTestId("create-submit").click();

    await expect(page.getByTestId("created-analysis")).toBeVisible();
    await expect(page.getByTestId("created-analysis-status")).toHaveText("queued");
    const analysisId = (await page.getByTestId("created-analysis-id").innerText()).trim();

    // 详情页真实展示状态
    await page.goto(`/analyses/${analysisId}`);
    await expect(page.getByTestId("analysis-detail")).toBeVisible();
    await expect(page.getByTestId("analysis-status")).toHaveText("queued");
  });

  test("Dashboard 与 /jobs 展示 analysis 列表（状态/gate/score 来自后端）", async ({
    page,
    request,
  }) => {
    await resetBackend(request);
    const analysisId = await seedAnalysis(request, { key: "e2e-list" });
    const score = await getJson<{ total: number; gate: string }>(
      request,
      `/analyses/${analysisId}/score`,
    );

    await page.goto("/");
    await expect(page.getByTestId("dashboard")).toBeVisible();
    await expect(page.getByTestId("analyses-table")).toBeVisible();
    await expect(page.getByTestId("analysis-status").first()).toHaveText("succeeded");
    await expect(page.getByTestId("analysis-gate").first()).toHaveText(score.gate);
    await expect(page.getByTestId("analysis-score").first()).toContainText(String(score.total));

    await page.goto("/jobs");
    await expect(page.getByTestId("jobs-page")).toBeVisible();
    await expect(page.getByTestId("total-count")).toHaveText(/[1-9]/);
  });
});

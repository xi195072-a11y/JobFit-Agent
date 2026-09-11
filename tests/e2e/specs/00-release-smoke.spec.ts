import { expect, test } from "@playwright/test";

import {
  BACKEND_URL,
  getJson,
  resetBackend,
  runPhase4,
  seedAnalysis,
  setScenario,
} from "./helpers";

/**
 * Release smoke（Phase 6 §22）：一条流程验证整个系统。
 *
 * fresh startup -> health -> frontend -> create/select -> analysis -> report -> review
 * 全程真实 backend（HTTP）+ 真实前端。
 */
test.describe("release smoke（系统级一条流程）", () => {
  test("health -> dashboard -> create -> analysis -> report -> review 全链路", async ({
    page,
    request,
  }) => {
    await resetBackend(request);

    // 1) backend health（含 version，§17/§38）
    const health = await getJson<{ status: string; version: string }>(request, "/health");
    expect(health.status).toBe("ok");
    expect(health.version).toMatch(/^\d+\.\d+\.\d+/);

    // 2) 前端可用（fresh render）
    await page.goto("/");
    await expect(page.getByTestId("dashboard")).toBeVisible();
    await expect(page.getByTestId("pipeline-version")).toBeVisible();

    // 3) 造数据（真实 HTTP：upload -> create -> run）
    await setScenario(request, "match", "valid");
    const analysisId = await seedAnalysis(request, { key: "e2e-release-smoke" });

    // 4) analysis 详情：确定性结论可见
    await page.goto(`/analyses/${analysisId}`);
    await expect(page.getByTestId("analysis-status")).toHaveText("succeeded");
    await expect(page.getByTestId("deterministic-section")).toBeVisible();
    await expect(page.getByTestId("constraints-table")).toBeVisible();
    await expect(page.getByTestId("skills-table")).toBeVisible();
    await expect(page.getByTestId("evidence-list")).toBeVisible();
    await expect(page.getByTestId("trace-list")).toBeVisible();

    // 5) Phase 4：critique -> report
    await runPhase4(request, analysisId);
    await page.reload();
    await expect(page.getByTestId("critique-panel")).toBeVisible();
    await expect(page.getByTestId("citation-status")).toHaveText("Validated");
    await expect(page.getByTestId("critique-disclaimer")).toContainText(
      "LLM critique does not override deterministic analysis.",
    );

    await page.goto(`/reports/${analysisId}`);
    await expect(page.getByTestId("report-view")).toBeVisible();
    await expect(page.getByTestId("report-stage")).toHaveText("validated");
    await expect(page.getByTestId("report-section-hard_constraints")).toBeVisible();

    // 6) HITL：approve -> finalized（succeeded/awaiting_review 之外的转移必须被拒）
    await page.goto(`/review/${analysisId}`);
    await expect(page.getByTestId("review-status")).toHaveText("awaiting_review");
    await page.getByTestId("approve").click();
    await expect(page.getByTestId("review-status")).toHaveText("finalized");

    const analysis = await getJson<{ status: string }>(request, `/analyses/${analysisId}`);
    expect(analysis.status).toBe("finalized");
    const report = await getJson<{ stage: string }>(request, `/analyses/${analysisId}/report`);
    expect(report.stage).toBe("final");

    await page.reload();
    await expect(page.getByTestId("approve")).toBeDisabled();
    expect(BACKEND_URL).toContain("127.0.0.1");
  });
});

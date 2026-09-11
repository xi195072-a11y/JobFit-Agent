import { expect, test } from "@playwright/test";

import { getJson, resetBackend, runPhase4, seedAnalysis } from "./helpers";

test.describe("critique 与报告（§23-7/8、§15/§16）", () => {
  test("critique 与确定性结果分区展示，citation 校验可见，报告分区来自后端", async ({
    page,
    request,
  }) => {
    await resetBackend(request);
    const analysisId = await seedAnalysis(request, { key: "e2e-critique", extraction: "match" });
    await runPhase4(request, analysisId);

    const critique = await getJson<{ validation_status: string; citations_validated: boolean }>(
      request,
      `/analyses/${analysisId}/critique`,
    );
    expect(critique.validation_status).toBe("validated");
    expect(critique.citations_validated).toBe(true);

    await page.goto(`/analyses/${analysisId}`);
    // 确定性 vs LLM 必须分区
    await expect(page.getByTestId("deterministic-section")).toBeVisible();
    await expect(page.getByTestId("llm-section")).toBeVisible();
    await expect(page.getByTestId("critique-panel")).toBeVisible();
    await expect(page.getByTestId("critique-status")).toHaveText("ok");
    await expect(page.getByTestId("citation-status")).toHaveText("Validated");
    // LLM 不是决策者
    await expect(page.getByTestId("critique-disclaimer")).toContainText(
      "LLM critique does not override deterministic analysis.",
    );
    // 已校验的 critique 才进入正文
    await expect(page.getByTestId("critique-untrusted")).toHaveCount(0);

    // 报告页：stage + 后端 sections 原样渲染
    const report = await getJson<{ stage: string; content: { sections: Array<{ type: string }> } }>(
      request,
      `/analyses/${analysisId}/report`,
    );
    await page.goto(`/reports/${analysisId}`);
    await expect(page.getByTestId("report-view")).toBeVisible();
    await expect(page.getByTestId("report-stage")).toHaveText(report.stage);
    await expect(page.getByTestId("report-section-hard_constraints")).toBeVisible();
    await expect(page.getByTestId("report-section-unknowns")).toBeVisible();
    for (const section of report.content.sections) {
      await expect(page.getByTestId(`report-section-${section.type}`).first()).toBeVisible();
    }
  });

  test("无凭证时 critique 落 unavailable，不伪造校验通过（EXTERNAL CREDENTIAL BLOCKED）", async ({
    page,
    request,
  }) => {
    await resetBackend(request);
    // 该 analysis 不触发 phase4：critique 尚不存在 => UI 显示"尚无 critique"
    const analysisId = await seedAnalysis(request, { key: "e2e-no-critique" });
    await page.goto(`/analyses/${analysisId}`);
    await expect(page.getByTestId("critique-panel")).toBeVisible();
    await expect(page.getByTestId("critique-panel")).toContainText("尚无 critique");
    await expect(page.getByTestId("critique-disclaimer")).toContainText(
      "LLM critique does not override deterministic analysis.",
    );
  });
});

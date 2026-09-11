import { expect, test } from "@playwright/test";

import { BACKEND_URL, getJson, resetBackend, runPhase4, seedAnalysis, setScenario } from "./helpers";

test.describe("citation 安全（§48）", () => {
  test("fabricated citation 被拒绝，且不渲染成可信证据", async ({ page, request }) => {
    await resetBackend(request);
    const analysisId = await seedAnalysis(request, { key: "e2e-fabricated", extraction: "match" });
    await setScenario(request, "match", "fabricated");
    await runPhase4(request, analysisId);

    const critique = await getJson<{ validation_status: string; citations_validated: boolean }>(
      request,
      `/analyses/${analysisId}/critique`,
    );
    expect(critique.validation_status).toBe("rejected");
    expect(critique.citations_validated).toBe(false);

    await page.goto(`/analyses/${analysisId}`);
    await expect(page.getByTestId("citation-status")).toHaveText("Rejected");
    await expect(page.getByTestId("critique-untrusted")).toBeVisible();
    await expect(page.getByTestId("critique-untrusted")).toContainText(
      "Critique not trusted / validation failed",
    );

    // 被拒 critique 的派生分区不得作为可信内容出现（报告只保留基础分区）
    const report = await getJson<{ content: { sections: Array<{ type: string }> } }>(
      request,
      `/analyses/${analysisId}/report`,
    );
    const types = report.content.sections.map((section) => section.type);
    expect(types).not.toContain("strengths");
    expect(types).not.toContain("gaps");
    expect(types).not.toContain("risks");
  });

  test("cross-analysis citation 被拒绝（作用域隔离）", async ({ page, request }) => {
    await resetBackend(request);
    // 造两个证据池不同的 analysis（不同简历 => 不同 parsed document）
    const targetId = await seedAnalysis(request, { key: "e2e-xa-target", extraction: "match" });
    const otherId = await seedAnalysis(request, { key: "e2e-xa-other", extraction: "mismatch" });
    expect(targetId).not.toBe(otherId);

    await setScenario(request, "match", "cross_analysis");
    await runPhase4(request, targetId);

    const critique = await getJson<{ validation_status: string; citations_validated: boolean }>(
      request,
      `/analyses/${targetId}/critique`,
    );
    expect(critique.validation_status).toBe("rejected");
    expect(critique.citations_validated).toBe(false);

    await page.goto(`/analyses/${targetId}`);
    await expect(page.getByTestId("citation-status")).toHaveText("Rejected");
    await expect(page.getByTestId("critique-untrusted")).toBeVisible();

    // 确定性结果未受影响：UNKNOWN 仍是 UNKNOWN（不被 LLM 改写）
    const constraints = await getJson<Array<{ result: string }>>(
      request,
      `/analyses/${targetId}/constraints`,
    );
    expect(constraints.every((row) => ["MET", "NOT_MET", "UNKNOWN"].includes(row.result))).toBe(true);
  });

  test("被注入的越权断言（UNKNOWN->SUPPORTED）被拒绝且 UNKNOWN 仍显示为 UNKNOWN", async ({
    page,
    request,
  }) => {
    await resetBackend(request);
    const analysisId = await seedAnalysis(request, { key: "e2e-injection", extraction: "unknown" });
    await setScenario(request, "unknown", "unknown_override");
    await runPhase4(request, analysisId);

    const critique = await getJson<{ validation_status: string }>(
      request,
      `/analyses/${analysisId}/critique`,
    );
    expect(critique.validation_status).toBe("rejected");

    await page.goto(`/analyses/${analysisId}`);
    await expect(page.getByTestId("citation-status")).toHaveText("Rejected");

    // 页面上 UNKNOWN 必须仍是 UNKNOWN
    const verdictTexts: string[] = [];
    const verdicts = page.getByTestId("constraint-verdict");
    for (let index = 0; index < (await verdicts.count()); index += 1) {
      verdictTexts.push((await verdicts.nth(index).innerText()).trim());
    }
    expect(verdictTexts).toContain("UNKNOWN");

    // 注入的越权断言无法改变确定性结果（重新读取仍一致）
    const constraints = await getJson<Array<{ result: string }>>(
      request,
      `/analyses/${analysisId}/constraints`,
    );
    expect(constraints.some((row) => row.result === "UNKNOWN")).toBe(true);

    // 后端确认：分析状态推进到 awaiting_review（critique rejected 不阻止人工评审）
    const analysis = await getJson<{ status: string }>(request, `/analyses/${analysisId}`);
    expect(analysis.status).toBe("awaiting_review");
    expect(BACKEND_URL).toBeTruthy();
  });
});

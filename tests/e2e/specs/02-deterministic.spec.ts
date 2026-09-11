import { expect, test } from "@playwright/test";

import { getJson, resetBackend, seedAnalysis } from "./helpers";

test.describe("确定性结论展示（§23-3/4/5/6）", () => {
  test("硬条件展示三值，UNKNOWN 明显区分且不等于失败", async ({ page, request }) => {
    await resetBackend(request);
    const analysisId = await seedAnalysis(request, { key: "e2e-unknown", extraction: "unknown" });

    await page.goto(`/analyses/${analysisId}`);
    await expect(page.getByTestId("constraints-table")).toBeVisible();

    const verdicts = page.getByTestId("constraint-verdict");
    const count = await verdicts.count();
    expect(count).toBeGreaterThan(0);

    const texts: string[] = [];
    for (let index = 0; index < count; index += 1) {
      texts.push((await verdicts.nth(index).innerText()).trim());
    }
    // 三值原文，且 UNKNOWN 存在
    expect(texts.every((text) => ["MET", "NOT_MET", "UNKNOWN"].includes(text))).toBe(true);
    expect(texts).toContain("UNKNOWN");
    // UNKNOWN 绝不能被渲染成失败
    expect(texts).not.toContain("FAIL");
    expect(texts.join(" ")).not.toMatch(/FAIL|FALSE/);

    // UNKNOWN 拥有独立样式类
    const unknownCell = page.locator('[data-testid="constraint-verdict"].verdict-unknown').first();
    await expect(unknownCell).toBeVisible();

    // "为什么是 UNKNOWN" 可展开确定性的 decision trace（而非"AI 觉得"）
    await page.getByTestId("why-unknown").first().click();
    const whyPanel = page.getByTestId("why-panel").first();
    await expect(whyPanel).toBeVisible();
    await expect(whyPanel).toContainText("决策链");
    await expect(whyPanel).toContainText("decision_key");
  });

  test("技能、评分、证据、决策链均可视；分数字面来自后端 score_snapshot", async ({
    page,
    request,
  }) => {
    await resetBackend(request);
    const analysisId = await seedAnalysis(request, { key: "e2e-match", extraction: "match" });
    const score = await getJson<{ total: number; gate: string; per_section: unknown[] }>(
      request,
      `/analyses/${analysisId}/score`,
    );
    const skills = await getJson<Array<{ status: string }>>(
      request,
      `/analyses/${analysisId}/skill-matches`,
    );
    const evidence = await getJson<{ total: number }>(request, `/analyses/${analysisId}/evidence`);
    const traces = await getJson<unknown[]>(request, `/analyses/${analysisId}/trace`);

    await page.goto(`/analyses/${analysisId}`);
    await expect(page.getByTestId("deterministic-section")).toBeVisible();

    // 技能：状态必须出现文字（不是只有颜色圆点）
    await expect(page.getByTestId("skills-table")).toBeVisible();
    const skillRows = page.getByTestId("skill-row");
    expect(await skillRows.count()).toBe(skills.length);
    const firstSkillText = await skillRows.first().innerText();
    expect(firstSkillText).toMatch(/matched|partial|unknown|missing|claimed_only/);

    // 评分：与后端一致，前端未重算
    await expect(page.getByTestId("score-panel")).toBeVisible();
    await expect(page.getByTestId("score-total")).toContainText(String(score.total));
    await expect(page.getByTestId("score-gate")).toHaveText(score.gate);
    expect(await page.getByTestId("score-section").count()).toBe(score.per_section.length);

    // 证据：PII-safe 定位信息
    await expect(page.getByTestId("evidence-list")).toBeVisible();
    expect(await page.getByTestId("evidence-item").count()).toBe(evidence.total);
    const evidenceText = await page.getByTestId("evidence-item").first().innerText();
    expect(evidenceText).toMatch(/span_sha256|chunk/i);

    // 决策链
    await expect(page.getByTestId("trace-list")).toBeVisible();
    expect(await page.getByTestId("trace-item").count()).toBe(traces.length);
  });
});

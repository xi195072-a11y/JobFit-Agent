import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

import { resetBackend, runPhase4, seedAnalysis } from "./helpers";

/**
 * Accessibility baseline（Phase 6 §33/§34）。
 *
 * 目标：Dashboard / Analysis / Report / Review 四个关键页面**无 critical / serious 违规**
 * （moderate / minor 会被打印出来以便跟踪；不追求 WCAG 全量认证）。
 */
const TARGETS = ["dashboard", "analysis", "report", "review"] as const;

test.describe("accessibility baseline", () => {
  test("关键页面无 critical / serious a11y violations", async ({ page, request }) => {
    await resetBackend(request);
    const analysisId = await seedAnalysis(request, { key: "e2e-a11y", extraction: "match" });
    await runPhase4(request, analysisId);

    const urls: Record<(typeof TARGETS)[number], string> = {
      dashboard: "/",
      analysis: `/analyses/${analysisId}`,
      report: `/reports/${analysisId}`,
      review: `/review/${analysisId}`,
    };

    const blocking: string[] = [];
    const tracked: string[] = [];

    for (const target of TARGETS) {
      await page.goto(urls[target]);
      await expect(page.locator("h1").first()).toBeVisible();
      const results = await new AxeBuilder({ page }).analyze();
      for (const violation of results.violations) {
        const entry = `${target}: ${violation.id} (${violation.impact}) — ${violation.help}`;
        if (violation.impact === "critical" || violation.impact === "serious") {
          blocking.push(entry);
        } else {
          tracked.push(entry);
        }
      }
    }

    if (tracked.length > 0) {
      console.log("[a11y] tracked (moderate/minor, not blocking):\n" + tracked.join("\n"));
    }
    expect(blocking, `critical/serious a11y violations:\n${blocking.join("\n")}`).toEqual([]);
  });
});

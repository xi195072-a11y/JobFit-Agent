import fs from "node:fs";
import path from "node:path";

import { expect, test } from "@playwright/test";

import { resetBackend, runPhase4, seedAnalysis } from "../specs/helpers";

/**
 * 生成 README / demo 所需的页面截图（§36）。
 *
 * - 数据全部为**合成 fixture**（无真实 PII）；
 * - 输出到 docs/screenshots/，仅截必要页面；
 * - 通过 `npm run screenshots`（独立配置）运行，不影响默认 E2E 判定。
 */
const OUT_DIR = path.resolve(__dirname, "..", "..", "..", "docs", "screenshots");

test("capture key product screens with synthetic data", async ({ page, request }) => {
  fs.mkdirSync(OUT_DIR, { recursive: true });

  await resetBackend(request);
  const matchId = await seedAnalysis(request, { key: "e2e-shot-match", extraction: "match" });
  await runPhase4(request, matchId);
  const unknownId = await seedAnalysis(request, { key: "e2e-shot-unknown", extraction: "unknown" });
  await runPhase4(request, unknownId);

  const targets: Array<{ name: string; url: string; waitFor: string }> = [
    { name: "dashboard", url: "/", waitFor: "dashboard" },
    { name: "jobs", url: "/jobs", waitFor: "jobs-page" },
    { name: "create-analysis", url: "/analyses/new", waitFor: "create-analysis" },
    { name: "analysis-detail", url: `/analyses/${matchId}`, waitFor: "constraints-table" },
    { name: "analysis-detail-unknown", url: `/analyses/${unknownId}`, waitFor: "constraints-table" },
    { name: "report", url: `/reports/${matchId}`, waitFor: "report-view" },
    { name: "review", url: `/review/${matchId}`, waitFor: "review-page" },
  ];

  for (const target of targets) {
    await page.goto(target.url);
    await expect(page.getByTestId(target.waitFor).first()).toBeVisible();
    await page.screenshot({ path: path.join(OUT_DIR, `${target.name}.png`), fullPage: true });
  }

  const written = fs.readdirSync(OUT_DIR).filter((name) => name.endsWith(".png"));
  expect(written.length).toBeGreaterThanOrEqual(targets.length);
});

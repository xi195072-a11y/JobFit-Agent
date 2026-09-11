import path from "node:path";

import { defineConfig, devices } from "@playwright/test";

const REPO_ROOT = path.resolve(__dirname, "..", "..");
const PYTHON =
  process.env.JOBFIT_PYTHON ??
  path.join(REPO_ROOT, "backend", ".venv", "Scripts", "python.exe");
const BACKEND_URL = process.env.JOBFIT_E2E_BACKEND_URL ?? "http://127.0.0.1:8000";
const FRONTEND_URL = process.env.JOBFIT_E2E_FRONTEND_URL ?? "http://127.0.0.1:3000";

/**
 * Phase 5 E2E（ADR-043）：
 * - 真实 backend 进程（tests/e2e/backend/app.py，注入 test-only DeterministicProvider）；
 * - 真实前端（Next.js dev server）；
 * - 无外部网站依赖、无 live credential 依赖。
 * 权威判定 = exit code + JUnit/HTML report（不以"浏览器打开了"为通过标准）。
 */
export default defineConfig({
  testDir: "./specs",
  timeout: 90_000,
  expect: { timeout: 20_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  forbidOnly: false,
  reporter: [
    ["list"],
    ["junit", { outputFile: "e2e-junit.xml" }],
    ["html", { outputFolder: "playwright-report", open: "never" }],
  ],
  use: {
    baseURL: FRONTEND_URL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      command: `"${PYTHON}" -m uvicorn app:app --app-dir tests/e2e/backend --host 127.0.0.1 --port 8000`,
      cwd: REPO_ROOT,
      url: `${BACKEND_URL}/health`,
      reuseExistingServer: true,
      timeout: 180_000,
      env: { PYTHONUNBUFFERED: "1" },
    },
    {
      command: "npm run dev -- --port 3000 --hostname 127.0.0.1",
      cwd: path.join(REPO_ROOT, "frontend"),
      url: FRONTEND_URL,
      reuseExistingServer: true,
      timeout: 240_000,
      env: { NEXT_PUBLIC_API_BASE_URL: BACKEND_URL },
    },
  ],
});

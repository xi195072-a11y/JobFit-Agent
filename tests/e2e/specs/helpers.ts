import fs from "node:fs";
import path from "node:path";

import { expect, type APIRequestContext, type Locator } from "@playwright/test";

export const BACKEND_URL = process.env.JOBFIT_E2E_BACKEND_URL ?? "http://127.0.0.1:8000";
const REPO_ROOT = path.resolve(__dirname, "..", "..", "..");
const FIXTURES = path.join(REPO_ROOT, "backend", "tests", "fixtures");

export type Extraction = "match" | "unknown" | "mismatch";
export type CritiqueMode = "valid" | "fabricated" | "cross_analysis" | "unknown_override";

/** 合成 fixture（无真实 PII），与 backend 集成测试使用同一批文件。 */
const FIXTURE_SETS: Record<Extraction, { resume: string; jd: string }> = {
  match: { resume: "resume_match.txt", jd: "jd_match.txt" },
  unknown: { resume: "resume_mismatch.txt", jd: "jd_unknown.txt" },
  mismatch: { resume: "resume_mismatch.txt", jd: "jd_mismatch.txt" },
};

export async function resetBackend(request: APIRequestContext): Promise<void> {
  const response = await request.post(`${BACKEND_URL}/__e2e__/reset`);
  expect(response.ok(), `reset failed: ${response.status()}`).toBeTruthy();
}

export async function setScenario(
  request: APIRequestContext,
  extraction: Extraction = "match",
  critique: CritiqueMode = "valid",
): Promise<void> {
  const response = await request.post(`${BACKEND_URL}/__e2e__/scenario`, {
    data: { extraction, critique },
  });
  expect(response.ok(), `scenario failed: ${response.status()}`).toBeTruthy();
}

async function uploadFixture(
  request: APIRequestContext,
  kind: "resume" | "jd",
  filename: string,
): Promise<string> {
  const response = await request.post(`${BACKEND_URL}/documents`, {
    multipart: {
      kind,
      file: { name: filename, mimeType: "text/plain", buffer: fs.readFileSync(path.join(FIXTURES, filename)) },
    },
  });
  expect([200, 201]).toContain(response.status());
  const body = (await response.json()) as { id: string };
  return body.id;
}

/** 经真实 HTTP 造数据：上传两份文档 -> 创建 analysis -> 运行确定性分析。 */
export async function seedAnalysis(
  request: APIRequestContext,
  options: { key: string; extraction?: Extraction; critique?: CritiqueMode },
): Promise<string> {
  const extraction = options.extraction ?? "match";
  await setScenario(request, extraction, options.critique ?? "valid");
  const fixture = FIXTURE_SETS[extraction];
  const resumeDocumentId = await uploadFixture(request, "resume", fixture.resume);
  const jdDocumentId = await uploadFixture(request, "jd", fixture.jd);

  const created = await request.post(`${BACKEND_URL}/analyses`, {
    data: {
      resume_document_id: resumeDocumentId,
      jd_document_id: jdDocumentId,
      idempotency_key: options.key,
    },
  });
  expect([200, 201]).toContain(created.status());
  const analysisId = ((await created.json()) as { id: string }).id;

  const run = await request.post(`${BACKEND_URL}/analyses/${analysisId}/run`, { data: {} });
  expect(run.status(), `run failed: ${await run.text()}`).toBe(200);
  return analysisId;
}

/** 触发 Phase 4 续接（critique -> report -> awaiting_review）。 */
export async function runPhase4(request: APIRequestContext, analysisId: string): Promise<void> {
  const response = await request.post(`${BACKEND_URL}/analyses/${analysisId}/critique`);
  expect(response.status(), `critique failed: ${await response.text()}`).toBe(200);
}

export async function getJson<T>(request: APIRequestContext, pathname: string): Promise<T> {
  const response = await request.get(`${BACKEND_URL}${pathname}`);
  expect(response.ok(), `GET ${pathname} failed: ${response.status()}`).toBeTruthy();
  return (await response.json()) as T;
}

/** 选中第一个真实 option（不依赖占位项）。 */
export async function selectFirstOption(locator: ReturnType<typeof expect> extends never ? never : any) {
  const value = await locator.locator("option").first().getAttribute("value");
  expect(value).toBeTruthy();
  await locator.selectOption(value as string);
}

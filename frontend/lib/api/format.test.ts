import { describe, expect, it } from "vitest";

import {
  analysisStatusLabel,
  citationStatusLabel,
  formatDate,
  formatDateTime,
  formatScore,
  formatSpan,
  isUnknownVerdict,
  sectionTitle,
  skillStatusLabel,
  verdictClass,
  verdictDescription,
  verdictLabel,
} from "./format";

describe("verdict 文案", () => {
  it("保留 MET / NOT_MET / UNKNOWN 三值原文", () => {
    expect(verdictLabel("MET")).toBe("MET");
    expect(verdictLabel("NOT_MET")).toBe("NOT_MET");
    expect(verdictLabel("UNKNOWN")).toBe("UNKNOWN");
    expect(verdictLabel("unknown")).toBe("UNKNOWN");
  });

  it("UNKNOWN 不等于失败", () => {
    expect(isUnknownVerdict("UNKNOWN")).toBe(true);
    expect(isUnknownVerdict("NOT_MET")).toBe(false);
    expect(verdictDescription("UNKNOWN")).not.toMatch(/fail|not[ _-]?met/i);
    expect(verdictDescription("UNKNOWN")).toContain("UNKNOWN");
  });

  it("UNKNOWN 使用独立的 CSS 类", () => {
    expect(verdictClass("UNKNOWN")).toContain("verdict-unknown");
    expect(verdictClass("UNKNOWN")).not.toBe(verdictClass("NOT_MET"));
    expect(verdictClass("MET")).toContain("verdict-met");
  });
});

describe("status 文案", () => {
  it("技能状态保留原文", () => {
    expect(skillStatusLabel("matched")).toBe("matched");
    expect(skillStatusLabel("partial")).toBe("partial");
    expect(skillStatusLabel("unknown")).toBe("unknown");
  });

  it("analysis 状态有可读文案", () => {
    expect(analysisStatusLabel("awaiting_review")).toContain("awaiting_review");
    expect(analysisStatusLabel("unknown_status")).toBe("unknown_status");
  });

  it("citation 校验文案区分 Validated / Rejected / Unavailable", () => {
    expect(citationStatusLabel("ok", "validated")).toBe("Validated");
    expect(citationStatusLabel("ok", "rejected")).toBe("Rejected");
    expect(citationStatusLabel("ok", "pending")).toBe("Pending");
    // unavailable 是独立状态，不得渲染成 Rejected（EXTERNAL CREDENTIAL BLOCKED）
    expect(citationStatusLabel("unavailable", "pending")).toContain("EXTERNAL CREDENTIAL BLOCKED");
  });
});

describe("分数与时间格式化", () => {
  it("分数格式化", () => {
    expect(formatScore(72.5)).toBe("72.5");
    expect(formatScore(80)).toBe("80");
    expect(formatScore(0)).toBe("0");
    expect(formatScore(null)).toBe("—");
  });

  it("时间按 UTC 固定格式", () => {
    expect(formatDateTime("2024-01-02T03:04:05Z")).toBe("2024-01-02 03:04 UTC");
    expect(formatDate("2024-01-02T03:04:05Z")).toBe("2024-01-02");
    expect(formatDateTime(null)).toBe("—");
  });
});

describe("section 标题与 evidence span", () => {
  it("section 标题映射", () => {
    expect(sectionTitle("hard_constraints")).toContain("硬性条件");
    expect(sectionTitle("unknowns")).toContain("unknowns");
    expect(sectionTitle("custom_section")).toBe("custom_section");
  });

  it("evidence span 文本", () => {
    expect(formatSpan(120, 180)).toBe("120-180");
    expect(formatSpan(0, 0)).toBe("0-0");
    expect(formatSpan(null, 180)).toBe("—");
  });
});

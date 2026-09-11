# JobFit Agent — 可解释决策链（Decision Trace）

> 关联：[docs/architecture.md](./architecture.md)（§4.6）、[docs/decisions.md](./decisions.md)（ADR-020）
> 目标：**最终报告里的每个关键决策，都可沿链展示 Requirement → Normalization → Rule → Evidence → Decision → Score contribution。**

> **实现状态：deterministic 部分已于 Phase 3 实现（ADR-033）。**
> 已落地：`matching/trace.py` 构建链并由 `matching/service.py` 在执行事务内幂等写入 `decision_traces`；
> 覆盖 `constraint` / `skill_match` / `score_component` 三类决策，每条约束与技能匹配都通过 `trace_id` 关联。
> **与设计稿的差异（更严格，ADR-033）**：evidence 环**不保存任何简历文本**（连脱敏 excerpt 也不存），
> 只保存 `source_chunk_id` + 文档级字符区间 + `span_sha256` + `tier`；JD 侧只保存单条 requirement 的
> `source_text` 与 anchor。原因：本项目脱敏器无法可靠识别人名，写入原文风险不可控（§24 允许该选项）。
> Phase 4 已实现 HITL review 生命周期（`reviews` + `audit_log`，ADR-038），但 **reviewer override → 新 trace 快照**（§3.6）
> 仍未实现；同样未实现的还有 `GET /analyses/{id}/trace` 之外的"揭示原文"受审计动作与前端 trace 渲染。
> 在实现前，README 与对外材料不得声称未落地部分已存在（ADR-021）。

---

## 1. 覆盖范围

写入 trace 的**确定性决策**：

| decision_type | decision_key | 产生节点 |
| --- | --- | --- |
| `constraint` | `req:{jd_requirement_id}` | validate_hard_constraints |
| `skill_match` | `skill:{jd_requirement_id}`（可关联 resume_skill_id）| match_skills |
| `score_component` | `section:{skills|experience|education|location|language}` | compute_base_score |

不写入 trace 体系：**LLM critique 是定性分析，不强行写入 deterministic decision trace**，但它必须通过 `evidence_ids` 做独立 citation validation（引用须命中证据池，ADR-007/ADR-020），并在 `critiques` 表单独留档（含 `citations_validated` 标志）。`UNKNOWN` 的弃权**本身**是确定性决策，必须写 trace（记录"为何无法判定"）。

---

## 2. 链结构（chain jsonb）

```jsonc
{
  "decision_key": "req:0f3a…",
  "decision_type": "constraint",

  "requirement": {                      // 第 1 环：来自 JD 的要求
    "req_type": "degree",
    "operator": ">=", "value": {"degree_level": 3, "majors": ["计算机","软件工程","人工智能"]},
    "source_text": "硕士及以上，计算机相关专业优先",
    "anchors": [{"doc":"jd","page":1,"char_start":12,"char_end":44}],
    "is_hard": true
  },

  "normalization": {                    // 第 2 环：简历侧原始值如何归一化
    "field": "resume_education[0].degree_raw",
    "raw": "工学硕士",
    "normalized": {"degree_level": 3},
    "method": "education_rules.degree_levels",
    "ruleset_version": "r:4a1e…"
  },

  "rule": {                             // 第 3 环：应用了哪条规则
    "rule_id": "edu.degree_at_least",
    "ruleset_version": "r:4a1e…",
    "params": {"min_level": 3, "majors": ["计算机","软件工程","人工智能"]}
  },

  "evidence": [                         // 第 4 环：证据（只允许 evidence_id；文本一律 trace-safe excerpt）
    {"evidence_id": "…", "source_chunk_id": "…",          // 原始 chunk（证据池内）
     "excerpt": "XX大学 软件工程 硕士（2020–2023）",       // PII 脱敏后的证据窗口
     "span": {"char_start": 320, "char_end": 355},          // chunk 内窗口偏移
     "excerpt_sha256": "…"}                                 // 脱敏 excerpt 指纹（可重生成校验）
  ],

  "decision": {                         // 第 5 环：结论
    "result": "MET",                    // MET | NOT_MET | UNKNOWN
    "basis": "deterministic",
    "reason": "degree_level 3 >= 3 且 major∈集合"
  },

  "score_contribution": {               // 第 6 环：计分影响（constraint 为 veto 时记 gate）
    "kind": "gate", "verdict": "pass",
    "weight": null, "points": null, "delta_total": 0
  },

  "versions": {"pipeline_version": "…", "extraction_schema_version": "…"} // 冗余便于单条审计
}
```

- **Skill match** 的 `score_contribution`：`{kind:"section", section:"skills", weight:0.35, max_points:…, points:…, delta_total:…}`；`claimed_only` 状态在 decision.reason 与 flags 中体现折扣系数（scoring.yaml `claimed_only_penalty`）。
- **score_component** trace 记录该 section 聚合：命中条目数、每项贡献明细（数组）、`delta_total`。
- **UNKNOWN 弃权**：`decision.result="UNKNOWN"`，`reason` 必须写清"判据不足的原因"（如 missing evidence / extraction degraded / rule 无法覆盖 operator），证据环可为空并显式标注 `missing`。
- **trace 内文本一律 PII-safe**：evidence 环只存 `excerpt`（不含姓名/电话/邮箱/URL 的脱敏窗口）；requirement / normalization 中出现的自由文本同样先经 PII 脱敏器处理；原始全文不写入 trace。策略与可验证性见 §6。

---

## 3. 不变式（测试强制，`test_trace.py`）

1. **覆盖性**：每个 constraint verdict、每个 skill_match_result 必有一条对应 trace；`hard_constraint_results.trace_id` / `skill_match_results.trace_id` 非空。
2. **存在性**：trace 内所有 `evidence_id` / `source_chunk_id` 均存在于 `document_chunks`；`rule.rule_id` 可回溯到 `ruleset_version` 对应配置；`versions` 与 analyses 行一致。
3. **excerpt 可验证性（PII-safe）**：`excerpt` 可由 `source_chunk_id` 对应 chunk 在相同 `span` 窗口上应用同一 PII 脱敏器**确定性重新生成**，重算 `excerpt_sha256` 与存储值一致；断言 excerpt 中不存在姓名/电话/邮箱/URL（见 §6）。
4. **可加性**：所有 `score_contribution.delta_total` 之和 = `score_snapshots.total`（容差 ≤ 1e-6）。
5. **确定性**：同版本快照下重放 trace 逐字段一致（配合 reproducibility.md 重放）。
6. **审计性**：human override 后 verdict 变化 → 原 trace 保留 + `hard_constraint_results.reviewer_override` 记录，生成新的 `decision: {basis:"human"}` 快照（新增 reviews 关联），不改历史。

---

## 4. 读取与呈现

- API：`GET /analyses/{id}/trace` → 按 decision_type 分组的 `DecisionTrace[]`（Pydantic，逐条含 chain）。**默认输出符合 PII-safe policy**：evidence 环只含已脱敏 `excerpt`，不含原始 chunk 全文/姓名/电话/邮箱；如需原始内容走受审计的"揭示"动作（§6）。
- 前端 `/analyses/[id]/trace`（Decision Trace / Evidence Trace）：
  - 每个需求一张卡片，六环纵向可折叠展示；
  - 证据环点击 → 以 `source_chunk_id + span` 定位 chunk，evidence-popover 高亮原文 anchor（后端做 PII 过滤后下发，或触发审计"揭示"）；
  - 计分环联动 score-breakdown（"这一项为总分贡献 +N"）；
  - `UNKNOWN`/`basis:"llm_extracted"`/`basis:"human"` 有专属徽标。
- 报告（markdown/PDF）引用 trace 链接；导出时附 trace_id 便于回查。

---

## 5. 生成时机与事务

trace 由 `matching/trace.py` 记录器在节点**产出决策的同一业务事务**内写入（与 verdict / skill / score 写入同一 UoW），保证"有决策必有 trace、无半截 trace"。该业务事务与 LangGraph checkpoint 分属**两个 durability domain**（不宣称同事务；跨域 crash consistency 由 idempotent node + CAS + claim fencing + reconciliation 保证，见 architecture.md §4.3 第 1 条与 ADR-017）。

---

## 6. PII 策略与 trace-safe excerpt（ADR-020 / ADR-013）

**问题**：trace 需要引用原文证据以支撑可解释性，但直接存原文会把姓名/电话/邮箱等 PII 复制进 trace、并随 Trace API 流出。

**决策**
1. **trace 不存敏感原文**。evidence 环只存 `excerpt` = 原始 chunk 的 **anchor 窗口**（char_start..char_end，必要时两侧扩展至固定上下文长度）经 **PII 脱敏器**处理后的确定性结果。脱敏器与日志层同一实现（`reports/redact.py`，移除姓名/电话/邮箱/URL，见 architecture.md §8.5）。
2. **excerpt 与原始 chunk 的关系（可验证性）**：
   - `source_chunk_id` 指向证据池内的原始 chunk（`document_chunks`，不可变；内容变更会改变其 `span_sha256`，与解析/抽取记录不一致即触发引用校验失败）；
   - `span` 记录 excerpt 在 chunk 内的窗口（char_start/char_end）——它等价于原始 anchor 的定位作用；
   - `excerpt_sha256` 是**脱敏后 excerpt 的指纹**；校验方式：取 `source_chunk_id` 对应 chunk 全文，在同一 `span` 上应用同一脱敏器重新生成，比对文本与 `excerpt_sha256` 一致 ⇒ 证明 excerpt 确定性地来源于该 chunk 且未被篡改（architecture.md §4.6/§8.5）。
3. **Trace API 默认输出即 PII-safe**（`GET /analyses/{id}/trace` 只下发已脱敏 excerpt）。查看脱敏前原文：以 `source_chunk_id + span` 读取 chunk 原文的高亮视图，属"揭示"动作——需要鉴权并写 `audit_log`。
4. requirement / normalization 等其它环若含自由文本，同样先脱敏后入库；数值/枚举/版本类字段不受影响。

**测试（test_trace.py）**：excerpt 重生成一致；excerpt 中无 PII 命中；Trace API 响应无敏感字段；揭示动作产生审计记录。

> 本文件与 ADR-020 同源；结构变更需先修订本文档。

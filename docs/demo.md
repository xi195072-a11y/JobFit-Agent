# JobFit Agent — 3–5 分钟面试演示脚本

> 目标：用**真实运行的系统**证明三件事 —— 确定性结论可解释、LLM 不越权、人工评审可审计。
> 全程使用合成数据（无真实 PII）。前置见 [README](../README.md) 的 Quick Start。

## 准备（演示前 1 分钟）

```powershell
docker compose -f backend/docker-compose.yml up -d      # 数据库
cd backend; .\.venv\Scripts\Activate.ps1
uvicorn jobfit.main:app --port 8000                     # 后端
cd ..\frontend; npm run dev                             # 前端 http://127.0.0.1:3000
```

打开浏览器访问 <http://127.0.0.1:3000>。

---

## Step 1 — Dashboard：先给结论（30s）

打开 `/`。展示最近分析的 **status / gate / score / 创建时间**。

> **展示的工程能力**：状态是数据库事实（`analyses.status`），gate 与 score 直接来自
> `score_snapshots`（前端不重算）。Dashboard 不出现姓名、电话、邮箱等 PII。

## Step 2 — 创建分析（20s）

进入 `/analyses/new`，从下拉框选择 **已有 Resume Profile** 与 **JD Profile**，点击创建。

> **展示的工程能力**：传的是 immutable artifact 的 id（`resume_profile_id` / `jd_profile_id`），
> 不是整份简历文本；profile 是版本化不可变产物，同一个 profile 可被多次分析复用。

## Step 3 — 运行确定性分析（20s）

进入 `/analyses/{id}`，点击 **运行确定性分析**。

> **展示的工程能力**：整条流水线（ingestion → parse → extract → constraints → skills →
> retrieval → score → trace）在**没有 LLM 凭证**时也能完整跑完；LLM 只在抽取层被调用，
> 且受 anchor grounding 约束。

## Step 4 — 硬性条件：三值语义（40s）

看 **硬性条件** 表格：`MET` / `NOT_MET` / `UNKNOWN` 三色区分。

指向一条 `UNKNOWN`：

> **展示的工程能力**：`UNKNOWN` 是一等公民（ADR-008）。**缺失证据 ≠ 不合格**——
> 系统不会把"没写"当成"不满足"。这是招聘场景里最容易被 LLM 搞错、后果最严重的一点。

## Step 5 — 技能匹配（20s）

看 **技能匹配** 表：`matched` / `partial` / `unknown` 都有**文字**状态（不只是颜色），
并给出 candidate、norm_used 与 evidence 数量。

> **展示的工程能力**：技能匹配有明确的证据优先级（structured > source chunk > retrieved > absence）；
> 仅出现在经历正文里的技能是 `partial`，不是 `matched`。

## Step 6 — 点击证据（30s）

看 **证据** 区：每条 evidence 显示 `source_chunk_id`、字符区间 `char_start-char_end`、
`span_sha256`、页码与"被谁引用"。

> **展示的工程能力**：PII-safe 的证据地基。前端**不读取简历原文**，只展示可校验的定位信息
> （chunk + 区间 + hash），任何一条结论都能回到具体字符区间。

## Step 7 — 决策链：为什么是 UNKNOWN？（40s）

回到硬条件表，对一条 `UNKNOWN` 点击 **"为何 UNKNOWN"**。

会出现 decision trace：`decision_key` → `rule` → `reason_code` → `result`，以及 evidence 环
（source_chunk_id / span / tier）。

> **展示的工程能力**：这就是"可解释"的**具体含义**——不是"AI 觉得如此"，而是
> **规则 + 证据 + 确定性的 reason code**。决策链 metadata-only，不落简历文本（防 PII 泄漏）。

## Step 8 — LLM Critique：明确不越权（40s）

滚动到 **LLM 解释层**。展示：
- 页面明确分区：**确定性分析** vs **LLM 解释层**
- 免责声明：`LLM critique does not override deterministic analysis.`
- **citation** 状态：`Validated` 或 `Rejected`

> **展示的工程能力**：LLM 的每条 factual claim 必须带 citation，且 citation 必须命中
> **当前 analysis** 的证据池；fabricated / 跨 analysis 引用会被 **citation validator** 拒绝，
> 页面上直接标红为 `Critique not trusted / validation failed`，不会伪装成可信证据。

## Step 9 — 报告（30s）

点击 **查看报告** 进入 `/reports/{id}`。

报告分区来自后端：Executive Summary / Hard Constraints / Skills / Evidence / Strengths /
Gaps / Risks / Unknowns / Score / Critique / Next Actions / Review Status，并显示 `stage`
（`draft → validated`）。

> **展示的工程能力**：报告是**确定性装配**（数字全部来自 DB）+ 已校验 critique 作为解释层；
> 前端不做任何叙述重组。stage 单向推进，`final` 之后不可再改。

## Step 10 — 人工评审：闸门与审计（40s）

进入 `/review/{id}`：
1. 状态显示 `awaiting_review`；
2. 点击 **通过（approve）** → 状态变 `finalized`，报告 stage 变 `final`；
3. （另一个分析）在理由框留空 → **拒绝按钮不可用**；填入理由后拒绝 → `rejected`；
4. 历史表展示 decision / comments / `from_state → to_state` / 时间。

> **展示的工程能力**：
> - **LLM 永远不会自动 finalized**，发布必须经人（ADR-009）；
> - 并发安全：状态转移是单条条件 UPDATE（CAS），两个 reviewer 同时 approve 只有一个生效，
>   另一个拿到 409，不会 double finalize；
> - 每次 action 落 `reviews` + `audit_log`，谁在何时把什么状态改成了什么，可审计。

---

## Step 11（加分项）— 当众"攻击"系统

如果时间允许，展示**系统如何拒绝被攻击**（对应 golden 集的 citation failure /
cross-analysis citation / prompt injection）：

```powershell
# 让 LLM 引用另一个 analysis 的 chunk（跨作用域）
curl -X POST http://127.0.0.1:8000/__e2e__/scenario -H "Content-Type: application/json" ^
  -d "{\"extraction\":\"match\",\"critique\":\"cross_analysis\"}"
curl -X POST http://127.0.0.1:8000/analyses/{id}/critique
```

刷新 `/analyses/{id}`：citation 显示 `Rejected`，并出现
`Critique not trusted / validation failed`；而**硬性条件结论一字未改**。

> **展示的工程能力**：安全不是"提示词祈祷"，而是**发布前的确定性校验 + 作用域隔离**。
> 这类场景已固化在 `tests/golden/` 的 11 个 case 中，可随时重放。

---

## 常见提问与应答要点

| 问题 | 回答要点 |
| --- | --- |
| 为什么不让 LLM 直接打分？ | 不可复现、不可审计、幻觉直接变成决策；且无法回答"为什么"。 |
| 怎么保证证据没被 LLM 编造？ | citation 必须命中当前 analysis 证据池 + `excerpt_sha256` 校验；fabricated 一律 rejected。 |
| 模型换了/规则改了，旧结论怎么办？ | 版本化（pipeline/ruleset/scoring/prompt）+ `config_snapshot` 随 analysis 快照；旧结果不漂移。 |
| 并发/崩溃会不会写坏数据？ | lease fencing 四条件断言 + 幂等唯一键 + CAS 状态转移；stale worker 写不进任何结果。 |
| 没有 LLM 凭证还能用吗？ | 能。确定性链路完整；critique 落 `unavailable` 并如实标注，绝不伪造。 |

# Interview Demo — 5 分钟演示脚本

> 目标：用**真实运行的系统**在 5 分钟内证明三件事 —— 确定性结论可解释、LLM 不越权、发布受人工闸门与审计约束。
> 全部基于当前已实现功能；脚本中不出现"以后会实现"。数据全部为合成 fixture（无真实 PII）。
>
> 更详细的逐步讲解见 [demo.md](./demo.md)；技术要点见 [interview-notes.md](./interview-notes.md)。

## 0. 一句话（10s）

> "这是一个简历 × JD 的匹配分析系统。**确定性引擎负责判断对错，LLM 只负责解释，人负责发布** —— 每条结论都能回溯到规则、证据和决策链。"

## 准备（演示前）

```powershell
docker compose up --build          # 或：本地 uvicorn + npm run dev
# backend: http://127.0.0.1:8000/docs   frontend: http://127.0.0.1:3000
```

---

### 1. 项目一句话 + 架构（30s）

展示 README 的架构图，指一句：

> "Resume/JD → Ingestion → Extraction → **Deterministic Analysis**（硬条件/技能/证据/评分）→ Decision Trace → LLM Critique → Citation Validation → Report → HITL → Final。**LLM is not the authoritative decision engine.**"

### 2. 选择 Resume / JD（20s）

`/analyses/new`：从下拉框选择已有 **Resume Profile** 与 **JD Profile** → 创建。

> 讲点：传的是 immutable artifact 的 id，不是整份文本；profile 版本化、可复用（ADR-023/026）。

### 3. Run analysis（20s）

`/analyses/{id}` 点击 **运行确定性分析**。

> 讲点：整条链路在**没有 LLM 凭证**时也能跑完；LLM 只在抽取阶段被调用，且受逐字 anchor grounding 约束。

### 4. Hard constraints（40s）

指向硬条件表：`MET` / `NOT_MET` / `UNKNOWN` 三色区分。指一条 `UNKNOWN`：

> 讲点：**UNKNOWN ≠ FALSE**（ADR-008/031）。缺失证据 ≠ 不合格 —— 这是招聘场景里后果最严重的一类错误；系统只有在**显式否定证据**存在时才允许 FALSE。

### 5. Skill matching（20s）

技能表：`matched` / `partial` / `unknown` 文字状态 + 证据。

> 讲点：证据优先级 structured > source chunk > retrieved > absence；只在经历正文出现的技能是 `partial`，不是 `matched`。

### 6. Evidence（30s）

证据区：`source_chunk_id` + 字符区间 + `span_sha256` + 页码 + 被谁引用。

> 讲点：前端**不读简历原文**，只展示可校验的定位信息；`excerpt_sha256` 可验证，杜绝"编造引用"。

### 7. Decision trace（40s）

点 "为何 UNKNOWN" → 展开 `decision_key` → `rule` → `reason_code` → evidence 环。

> 讲点：可解释性在这里是**数据**不是承诺；trace metadata-only（不落简历文本，防 PII 泄漏，ADR-033）。

### 8. Critique（40s）

LLM 区：明确分区 + 免责声明 `LLM critique does not override deterministic analysis.` + citation 状态。

> 讲点：每条 factual claim 必须带 citation，且必须命中**当前 analysis** 证据池；fabricated / 跨 analysis 会被 validator 拒绝并标为 `Rejected` / "Critique not trusted"。UNKNOWN 若被 LLM 断言为满足 → 直接 rejected。

### 9. Report（30s）

`/reports/{id}`：12 个分区全部来自后端 `report.sections`，stage 为 `validated`。

> 讲点：报告 = 确定性装配 + 已校验 critique 解释层；stage 单向 `draft → validated → final`，final 不可改。

### 10. HITL（30s）

`/review/{id}`：`awaiting_review` → **approve** → `finalized`（报告转 `final`）；再演示理由为空时拒绝按钮不可用。

> 讲点：**LLM 永远不会自动 finalized**（ADR-009）；状态转移是单条条件 UPDATE，两个 reviewer 并发 approve 只有一个生效，另一个 409；每次操作落 `reviews` + `audit_log`。

### 11. Evaluation（20s）

```powershell
cd backend; python -m jobfit.evaluation
```

> 讲点：11 个 golden case 全绿；六项指标 100%；指标由**代码**计算，**没有 LLM 自评**（ADR-042/§54）。

### 12. Architecture tradeoff（30s，收尾升华）

选一个讲深，例如：

> "**为什么不让 LLM 直接打分？** 因为它不可复现、不可审计，而且无法回答'为什么'。我把它拆成两层：确定性核心 + LLM 解释层。代价是规则引擎需要维护，收益是**每个结论都能被追问到底**。为了守住这条边界，我加了 citation validator、UNKNOWN 保留语义、以及一个 golden 集来防止回归。"

---

## 可能的追问（预备一行答案）

| 追问 | 一行答案 |
| --- | --- |
| 并发/崩溃会写坏数据吗？ | lease fencing 四条件断言 + 幂等唯一键 + CAS 状态转移，stale worker 写不进任何结果。 |
| 没有 LLM 凭证还能用吗？ | 能。确定性链路完整；critique 落 `unavailable` 并如实标注，绝不伪造。 |
| 怎么防 prompt injection？ | 文档内容作为受控 data 块 + 不执行文档内容 + citation validator 事后兜底 + 专门 golden case。 |
| 模型或规则改了旧结论会变吗？ | 不会。版本化 + `config_snapshot` 随 analysis 快照，旧行与旧绑定不变。 |
| 现在最大的限制？ | 无 live LLM 持续验证（EXTERNAL CREDENTIAL BLOCKED）；其余见 README「Known Limitations」。 |

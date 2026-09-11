# JobFit Agent

**Evidence-Grounded AI Career Intelligence Platform** —— 简历 × JD 的**可审计**匹配分析平台。

一句话：**确定性引擎负责判断对错，LLM 只负责解释，人负责发布。**
每一个结论（硬条件是否满足、技能是否匹配、总分、为什么是 UNKNOWN）都能回溯到规则、证据与决策链；LLM 的定性分析必须通过引用校验，且**永远不能覆盖**确定性结论。

---

## 1. 它解决什么问题

简历筛选的常见做法是"把两份文本丢给 LLM，让它打个分"。结果是：不可复现、不可审计、无法解释、无法追责，模型幻觉直接变成招聘决策。

JobFit Agent 把这件事拆成两层：

- **确定性层（authoritative）**：结构化的硬条件判定（三值 `MET` / `NOT_MET` / `UNKNOWN`）、技能匹配、证据检索、加权评分、决策链。纯规则与数据，可复现、可单测、可重放。
- **LLM 层（非权威）**：把非结构化文本抽成结构化事实；基于**已检索到的证据集合**生成结构化 critique（结构化输出 + 引用校验）。

**LLM 不是决策引擎。** 没有 LLM（或没有 API key），确定性部分照常产出结果，critique 降级为 `unavailable` 并如实标注。

## 2. Pipeline

```
Resume / JD (upload)
        │
        ▼
   Ingestion                校验 / sha256 去重 / 本地存储
        │
        ▼
   Parsing + Chunking       确定性滑窗，parser_version 参与身份
        │
        ▼
   Extraction (LLM)         Pydantic 严格 DTO + 逐字 anchor grounding → immutable profiles
        │
        ▼
   Deterministic Analysis
   ├── Hard Constraints      三值判定 + reason_code + gate（MET/NOT_MET/UNKNOWN）
   ├── Skill Matching        证据优先级：structured > source chunk > retrieved > absence
   ├── Evidence Retrieval    anchor gate + pgvector 余弦 + lexical fallback（作用域隔离）
   ├── Scoring               weighted + UNKNOWN 策略（exclude_and_renormalize）
   └── Decision Trace        每条结论 → 规则 → 证据 → source chunk
        │
        ▼
   LLM Critique              结构化输出（CritiqueSchema）+ Citation Validation
        │                    fabricated / 跨 analysis 引用 → rejected
        ▼
   Evidence-Grounded Report  确定性结果装配 + 已校验 critique 解释层（draft → validated）
        │
        ▼
   HITL Review               人工 approve / reject（状态机 + 并发安全 + 审计）
        │
        ▼
   Final                    报告 final（不可再改）
```

**关键边界：LLM ≠ authoritative decision engine。**

## 3. Deterministic vs LLM（责任划分）

| 能力 | 由谁决定 | 证据 |
| --- | --- | --- |
| 硬条件 verdict | 确定性规则 | `matching/constraints.py`，写 `hard_constraint_results` |
| 技能匹配 | 确定性规则 | `matching/skills.py` |
| 分数 / gate | 确定性配置 | `scoring.yaml` → `score_snapshots` |
| 决策链 | 确定性 | `decision_traces`（不含简历文本） |
| 结构化事实抽取 | LLM（受 anchor grounding 约束） | 每个字段必须有逐字引用，否则丢弃 |
| 定性 critique | LLM（受引用校验约束） | `critiques` 表 + `citations_validated` |
| 发布 | 人 | `reviews` + `audit_log` |

## 4. Evidence Grounding

- 抽取层的每个字段/条目都带 `evidence_quotes`，必须能在 chunk 中**逐字定位**，否则被丢弃并记 warning。
- critique 的每条 factual claim 必须携带 citation（`trace_id` / `source_chunk_id`）。
- **Citation Validator** 在发布前校验：citation 必须命中当前 analysis 的证据池；fabricated、跨 document、跨 analysis 引用一律 `rejected`；`excerpt_sha256` 必须可验证。
- **UNKNOWN 是一等公民**：缺失证据 ≠ 不合格。UNKNOWN 不会被写成 `NOT_MET`，也不会被 LLM 改写成"满足"。
- 前端展示 evidence 时只使用后端给出的定位信息（chunk id / 字符区间 / hash / 引用关系），**不渲染简历原文**。

## 5. Demo / 截图

- 产品界面（Next.js）：`/`（Dashboard）、`/jobs`（分析列表）、`/analyses/new`、`/analyses/[id]`、`/reports/[id]`、`/review/[id]`
- 3–5 分钟面试演示脚本：[docs/demo.md](./docs/demo.md)
- 页面截图（合成数据，无真实 PII）：[docs/screenshots/](./docs/screenshots/)（可用 `npm run screenshots` 重新生成）

## 6. Quick Start

### 支持版本（§14/§43）

| 组件 | 版本 | 说明 |
| --- | --- | --- |
| Python | **3.12**（`requires-python >= 3.11`） | backend 与 scripts |
| Node.js | **24**（Next.js 16 / React 19） | frontend 与 E2E |
| PostgreSQL | **16 + pgvector** | `pgvector/pgvector:pg16` |
| Docker / Compose | Docker 29+ / Compose v5+ | 全栈一键启动 |

### 方式 A：一条命令（推荐，已验证）

```powershell
git clone <repo>; cd ai
docker compose up --build
#   frontend  http://localhost:3000
#   backend   http://localhost:8000/docs   （OpenAPI：/openapi.json）
```

`backend` 容器启动时会先执行幂等 `alembic upgrade head`，再拉起 API；
`db` 使用 pgvector 镜像并有 healthcheck，`backend`/`frontend` 均带 healthcheck。

### 方式 B：本地开发（无需容器化 backend/frontend）

```powershell
# 1) 数据库（仅 db 服务）
docker compose up -d db

# 2) 后端
cd backend
python -m venv .venv; .\.venv\Scripts\activate
pip install -e ".[dev]"
alembic upgrade head
uvicorn jobfit.main:app --port 8000          # http://127.0.0.1:8000/docs

# 3) 前端
cd ..\frontend
npm ci
"NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000" | Set-Content .env.local
npm run dev                                   # http://127.0.0.1:3000
```

### 接口与密钥约定

- **API base path：`/`**（当前未启用 `/api/v1` 前缀；OpenAPI 文档见 `/docs` 与 `/openapi.json`）。
- **`DEEPSEEK_API_KEY` 为可选**：未配置时 critique 落 `unavailable`（EXTERNAL CREDENTIAL BLOCKED），
  确定性分析与报告照常可用；**不会伪造 live 结果**。
- 密钥只经环境变量/密钥管理注入：**不得**写入 `docker-compose.yml`、`Dockerfile`、前端变量或 CI 日志。
  前端仅暴露 `NEXT_PUBLIC_API_BASE_URL`（浏览器可见，绝不含密钥）；浏览器永远只访问 backend。
- 完整环境契约见 [.env.example](./.env.example)。


## 7. Test（权威判定）

一条命令启动后，四类测试分别执行（全部已在 CI 配置中固化，见 [.github/workflows/ci.yml](./.github/workflows/ci.yml)）：

```powershell
# 后端：unit + integration + golden（真实 PostgreSQL/pgvector）
cd backend
ruff check src tests alembic
mypy src/jobfit tests
pytest tests --junitxml=..\phase6-junit.xml     # JUnit 权威
alembic check                                   # 无 schema drift

# 前端
cd ..\frontend
npm run lint; npm run typecheck; npm run test:junit; npm run build

# E2E（真实 backend + 真实前端；自动拉起服务）
cd ..\tests\e2e
npx playwright test                             # 退出码 + JUnit/HTML report 权威

# 评测 / 冒烟
cd ..\backend; python -m jobfit.evaluation --out-json ../phase6-evaluation.json --out-md ../phase6-evaluation.md
cd ..; python scripts/release_smoke.py          # env/DB/API/frontend/golden
python scripts/smoke_api.py                     # 真实 HTTP 全链路（无凭证 => BLOCKED，不伪装 PASS）
```

测试策略要点：

- 集成测试打**真实 PostgreSQL + pgvector**；无 DB 时 **skip，不伪通过**。
- LLM 相关测试使用 test-only `DeterministicProvider`（`backend/tests/support.py`）；
  生产代码路径**不存在**任何 fake provider（有静态审计测试强制）。
- JUnit XML 是权威判定；后端 / 前端 / E2E 分别统计 `tests / failures / errors / skipped`。
- live DeepSeek **不属于默认 CI gate**（需要凭证时单独执行 smoke）。

## 8. Evaluation

声明式 golden 集 + 确定性指标（**不由 LLM 自评**）：

```powershell
cd backend
python -m jobfit.evaluation     # 运行 golden 并产出 phase6-evaluation.json / .md
```

- 结构：`tests/golden/{cases,expected,fixtures}`（见 [README](./backend/tests/golden/README.md)）
- 指标：`hard_constraint_exact_agreement`、`skill_match_agreement`、`unknown_preservation`、
  `citation_validity`、`report_invariant_validity`、`decision_trace_completeness`（+ `review_lifecycle`）
- 覆盖场景：perfect match / obvious mismatch / unknown / mixed constraints / skill partial /
  citation failure / cross-analysis citation / prompt injection / contradictory evidence /
  review rejection / review approval
- 当前结果：**11/11 passed**，六项指标 100%（见 [phase6-evaluation.md](./phase6-evaluation.md)）

## 9. Key Engineering Decisions

来自 [docs/decisions.md](./docs/decisions.md)（ADR-001 ~ ADR-043），只列工程事实：

- **Deterministic Core + LLM Critique**（ADR-001）：LLM 只做抽取与解释，不参与终审。
- **UNKNOWN ≠ FALSE**（ADR-008/ADR-031）：缺证据一律 UNKNOWN，只有显式否定证据才允许 FALSE。
- **Immutable artifacts + 版本化身份**（ADR-023/ADR-026/ADR-028）：parse/profile 不可变、唯一键 + `ON CONFLICT DO NOTHING`，重跑不覆盖历史。
- **Evidence Anchoring，禁止自由引用**（ADR-007）：逐字引用定位到 chunk，抽取结果可回溯。
- **Citation Validation**（ADR-036）：critique 引用必须命中当前证据池；fabricated / 跨 analysis → rejected。
- **幂等 + Fingerprint**（ADR-026/ADR-028/ADR-036）：同配置重跑复用同一 artifact / critique，不重复调 LLM。
- **Lease Fencing（四条件断言）**（ADR-017）：stale worker 无法写任何结果；0 行即停。
- **PostgreSQL + pgvector**（ADR-005/ADR-030）：向量检索真实使用 pgvector 距离，非模拟。
- **HITL 强制发布闸门 + 并发安全**（ADR-009/ADR-038）：状态机 + 单条条件 UPDATE（CAS），无 double finalize。
- **JUnit XML 权威测试 + 真实 DB 集成**（ADR-016/ADR-029）：无 DB 时 skip 而非假装通过。
- **Deterministic provider 仅用于测试/CI**（ADR-029/ADR-039）：live LLM 不进默认测试路径。
- **错误契约与请求关联**（ADR-041）：`error.{code,message,details,request_id,retryable}` + `X-Request-ID`。

## 10. Known Limitations（如实声明，ADR-021）

- **无 live LLM 验证**：本仓库开发环境未配置 `DEEPSEEK_API_KEY`，因此 live DeepSeek 结论为
  `EXTERNAL CREDENTIAL BLOCKED`，**不声称 PASS**。LLM 契约与引用校验通过 DeterministicProvider 全覆盖。
- **前端无上传 UI**：产品界面按 MVP 范围只做"选择已有 Profile"，文档上传/解析/抽取走 API。
- **Dashboard 每行额外请求 `/score`**：`GET /analyses` 不内联 gate/score，列表页按行读取（N 次并发请求）。
- **`make evaluate` 便捷命令**：未实现；评测入口是 `python -m jobfit.evaluation`。
- **reviewer override → 新 decision trace 快照**：未实现（review 本身已落 `reviews` + `audit_log`）。
- **Docker 全栈已实测可用**：`ai-backend` / `ai-frontend` 镜像构建成功，`docker compose up -d --build` 后
  `db` / `backend` / `frontend` 均 healthy、`/health` 与首页均可访问（详见 [docs/release.md](./docs/release.md)）。
  （本机曾因 Docker Hub 网络不可达阻塞，已通过配置 `registry-mirrors` 解决。）
- **CI 未在 GitHub runner 真实触发**：无远端仓库/runner。已完成 workflow 语法校验 + 4 个 job 的本地等价命令实跑；
  因此 README **不添加 CI badge**（不造假）。
- **文件格式**：仅 TXT / PDF（文本层）/ DOCX；扫描件、URL ingestion 明确不在 MVP（ADR-011/ADR-012）。
- **无鉴权 / 多租户**：当前为单租户 demo，未实现认证与租户隔离。

## 11. Roadmap

- Phase 6+：认证与多租户、`make evaluate` 便捷入口、reviewer override 的 trace 快照、
  语义 embedding（bge-m3）、批量排名与 counterfactual 对比、上传 UI、扫描件 OCR。

---

## 文档

- [docs/release.md](./docs/release.md) —— 发布说明、Release Readiness Matrix、实测数值与外部阻塞
- [docs/interview-notes.md](./docs/interview-notes.md) —— 面试技术要点（13 个为什么）
- [docs/interview-demo.md](./docs/interview-demo.md) —— 5 分钟演示脚本
- [docs/demo.md](./docs/demo.md) —— 3–5 分钟逐步演示（含"展示什么工程能力"）
- [docs/architecture.md](./docs/architecture.md) —— 系统架构与实现清单（§12.1）
- [docs/decisions.md](./docs/decisions.md) —— ADR 决策记录
- [docs/reproducibility.md](./docs/reproducibility.md) —— 版本化与可复现性
- [docs/decision-trace.md](./docs/decision-trace.md) —— 可解释决策链
- [backend/tests/golden/README.md](./backend/tests/golden/README.md) —— golden 评测集
- [CHANGELOG.md](./CHANGELOG.md) —— 阶段级变更记录

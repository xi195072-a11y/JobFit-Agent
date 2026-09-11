# Release Notes — JobFit Agent v0.1.0

> 本文件只记录**真实实现与真实测量**。未执行项明确标注（ADR-021：不把 planned 写成 implemented）。

## 1. 架构（一句话版）

**确定性引擎负责判断对错，LLM 只负责解释，人负责发布。**

```
Resume/JD → Ingestion → Parsing/Chunking → Extraction(LLM + anchor grounding) → Profiles(immutable)
   → Deterministic Analysis（Hard Constraints / Skill Matching / Evidence Retrieval / Score）
   → Decision Trace → LLM Critique → Citation Validation → Report → HITL Review → Final
```

- 完整设计：[architecture.md](./architecture.md)、ADR-001 ~ ADR-043：[decisions.md](./decisions.md)
- 可复现性：[reproducibility.md](./reproducibility.md)；决策链：[decision-trace.md](./decision-trace.md)

## 2. 功能（当前可用）

| 能力 | 状态 | 证据 |
| --- | --- | --- |
| 上传 / 解析 / 分块 / 结构化抽取（immutable artifacts） | 可用 | Phase 2 测试 + `test_upload_security.py` |
| 硬条件三值判定（MET / NOT_MET / UNKNOWN） | 可用 | `test_constraints.py`、golden `unknown` |
| 确定性技能匹配（含 partial） | 可用 | `test_skill_matching.py` |
| 证据检索（anchor gate + pgvector + lexical fallback） | 可用 | `test_phase3_retrieval.py` |
| 决策链（metadata-only，可解释 UNKNOWN） | 可用 | `test_trace*.py` |
| 加权评分（UNKNOWN 不入分母） | 可用 | `test_scoring.py` |
| LLM critique + Citation Validator | 可用（deterministic provider 验证） | `test_phase4_critique.py` |
| Evidence-grounded report（stage 单向） | 可用 | `test_report_builder.py`、`test_phase4_api.py` |
| HITL review（approve / reject / requeue + 审计） | 可用 | `test_phase4_concurrency.py`、E2E |
| Next.js 前端（6 路由，presentation-only） | 可用 | `npm run build`、E2E |
| Golden evaluation（11 cases + 指标） | 可用 | `phase6-evaluation.json` |
| CI（4 jobs，deterministic provider） | 已配置 | `.github/workflows/ci.yml` |
| Docker 全栈（db + backend + frontend） | 可用 | 镜像构建成功 + `docker compose up` 实测健康 |

## 3. 测试状态（机器可读 artifact）

| 套件 | tests | failures | errors | skipped | 来源 |
| --- | --- | --- | --- | --- | --- |
| Backend（unit + integration + golden） | **359** | 0 | 0 | 0 | `phase6-junit.xml` |
| Frontend（vitest） | **17** | 0 | — | 0 | `frontend/frontend-junit.xml` |
| E2E（Playwright，真实 backend + 前端） | **14** | 0 | 0 | 0 | `tests/e2e/e2e-junit.xml` |
| Golden evaluation | total **11** / passed 11 / failed 0 | | | | `phase6-evaluation.json` |

静态检查：`ruff` All checks passed（src+tests+alembic）；`mypy` no issues（150 files）；`tsc --noEmit` 0 error；`eslint` 0 problem。
数据库：`alembic current` = `0003_phase4_critique_report (head)`；`alembic check` = *No new upgrade operations detected*（无 schema drift）。

Golden 指标（代码计算，非模型自评）：`hard_constraint_exact_agreement` 9/9、`skill_match_agreement` 5/5、
`unknown_preservation` 5/5、`citation_validity` 11/11、`report_invariant_validity` 7/7、
`decision_trace_completeness` 7/7、`review_lifecycle` 2/2 —— 全部 100%。

## 4. Release Readiness Matrix（§4）

| 项目 | 状态 | Evidence |
| --- | --- | --- |
| backend install | PASS | `pip install -e ".[dev]"`（venv）后可 import / 跑测试；Docker 镜像内 `pip install .` 成功 |
| frontend install | PASS | `npm ci`（宿主机 + Docker builder 阶段均成功） |
| local startup | PASS | `docker compose up -d --build` → db/backend/frontend 均 healthy；宿主机 `uvicorn` + `next dev` 亦用于 E2E |
| environment configuration | PASS | `.env.example` 覆盖 POSTGRES_*/DATABASE_URL/TEST_DATABASE_URL/CORS/LLM(可选)/EMBEDDING/路径/retry budget；`test_phase6_release.py` 守护 |
| database startup | PASS | `jobfit-pg` healthy（pgvector:pg16），`jobfit` + `jobfit_test` 均存在 |
| migration | PASS | 容器 entrypoint 自动 `alembic upgrade head`；`alembic check` 无差异 |
| test command | PASS | 见 §3（JUnit 权威） |
| lint | PASS | ruff + eslint |
| typecheck | PASS | mypy + tsc |
| e2e | PASS | 14 tests（含 release smoke 与 a11y） |
| golden evaluation | PASS | 11/11，`python -m jobfit.evaluation` |
| Docker | PASS | `docker compose config` 有效；`ai-backend` / `ai-frontend` 镜像构建成功；全栈启动实测 healthy |
| CI | PASS（configuration） | `.github/workflows/ci.yml` YAML 解析通过，4 jobs；本地不具备 GitHub runner，未真实触发（见 §6） |
| README | PASS | 含架构图 / Quick Start / Key Engineering Decisions / Known Limitations |
| demo | PASS | `docs/demo.md` + `docs/interview-demo.md` |

## 5. 实测数值（可复算）

- **API smoke**（真实 HTTP，合成数据）
  - 容器化后端（无 LLM 凭证）：`13 passed / 0 failed / 6 blocked / 1 skipped` —— BLOCKED 均为
    `EXTERNAL CREDENTIAL BLOCKED`，**未伪造通过**。
  - deterministic-provider harness（严格模式）：`20 passed / 0 failed / 0 blocked`（含 critique → report → review approve → finalized）。
- **Release smoke**：`9 passed / 0 failed / 2 info`（env / DB / API / frontend / golden）。
- **性能基线**（`phase6-performance.json`，10 次迭代）
  - `/health`：p50 **15.9 ms**，p95 75.7 ms
  - `GET /analyses`：p50 **32.4 ms**，p95 50.4 ms
  - 确定性分析全链路（upload + create + run）：**290.7 ms**
  - critique + report：**154.1 ms**
  - 前端首页（容器化）：p50 **18.0 ms**，p95 31.1 ms
- **依赖审计**
  - `npm audit`：info 0 / low 0 / moderate 0 / high 0 / **critical 0**
  - `pip-audit`：应用运行时依赖 **0 条** 公告；仅本地 venv 的 `pip 24.0`（构建工具，非应用运行时）有升级公告
- **可访问性**：`@axe-core/playwright`，Dashboard / Analysis / Report / Review **critical = 0**（serious 级会打印跟踪，不阻断）

## 6. 外部阻塞与未执行项（如实声明）

1. **Live DeepSeek：EXTERNAL CREDENTIAL BLOCKED** —— 未配置 `DEEPSEEK_API_KEY`。
   因此未执行任何 live LLM 调用；`phase6-live-llm.json` 记录 `live_calls_executed: 0`。
   LLM 契约、citation 校验、报告与 HITL 全部由 deterministic provider 覆盖（golden + 8 个 phase4 集成测试 + E2E）。
   **不把 deterministic 结果写成 live PASS。**
2. **CI 未在 GitHub runner 上真实触发**：本机无 GitHub Actions runner / 未配置远端仓库。
   已完成：workflow YAML 解析校验、4 个 job 的本地等价命令全部实跑通过、无 secret 依赖、失败不吞。
3. **Docker 镜像构建（本机曾阻塞，已修复）**：本机 Docker 引擎原先无 registry 镜像源且直连 Docker Hub 失败；
   已配置 `registry-mirrors`（`daocloud` / `1ms.run` / `xuanyuan` / `rat.dev`）后构建成功。
   前端镜像额外配置 npm registry 可覆盖参数（默认 `npmmirror`，可 `--build-arg NPM_REGISTRY=...` 覆盖）。

## 7. 已知限制

- **无认证 / 多租户**：单租户 demo，未实现登录与租户隔离。
- **前端无上传 UI**：产品界面只做"选择已有 Profile"；文档上传/解析/抽取经 API（`scripts/smoke_api.py` 演示）。
- **`GET /analyses` 不内联 gate/score**：Dashboard 每行额外请求 `/score`（列表页 N 次并发请求）。
- **reviewer override → 新 decision trace 快照**：未实现（review 本身已落 `reviews` + `audit_log`）。
- **`make evaluate` 便捷入口**：未实现；评测入口为 `python -m jobfit.evaluation`。
- **文件格式**：仅 TXT / PDF（文本层）/ DOCX；扫描件、URL ingestion 不在 MVP（ADR-011/ADR-012）。
- **语义 embedding（bge-m3）**：未启用（MVP 为确定性 `hash-ngram-v1`，ADR-030）。

## 8. 数据与密钥边界

- **数据**：`docker compose down` **不会**删除数据卷（`backend_jobfit_pgdata` 被显式复用）；
  只有 `docker compose down -v` 才会删除数据。`jobfit_test` 可安全 reset（集成测试会在用例间 TRUNCATE）。
- **密钥**：`DEEPSEEK_API_KEY` 只经环境变量注入；`.env*` 已被 `.gitignore` 排除；
  前端仅暴露 `NEXT_PUBLIC_API_BASE_URL`（浏览器可见，绝不含密钥）；浏览器永远只访问 backend。

## 9. 复现本次发布

```powershell
# 一键启动（db + backend + frontend）
docker compose up --build

# 后端
cd backend; ruff check src tests alembic; mypy src/jobfit tests
pytest tests --junitxml=../phase6-junit.xml
alembic check

# 前端
cd ..\frontend; npm run lint; npm run typecheck; npm run test:junit; npm run build

# 评测 + E2E
cd ..\backend; python -m jobfit.evaluation --out-json ../phase6-evaluation.json --out-md ../phase6-evaluation.md
cd ..\tests\e2e; npx playwright test

# 冒烟
python scripts/release_smoke.py
python scripts/smoke_api.py            # 无凭证时为 BLOCKED，不伪装 PASS
```

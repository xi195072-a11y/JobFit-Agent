# Changelog

本文件记录**阶段级**技术能力（非逐 commit 罗列）。版本号遵循 `pyproject.toml`（当前 `0.1.0`）。

## [Unreleased] — live 验证暴露的缺口修复

**修复**

- **structured output repair retry**（ADR-001 / ADR-018 已规定"校验失败 → 带纠错提示重试 1 次"，但代码未实现）：新增 `backend/config/prompts/repair.j2`，抽取路径（resume / jd）在 schema 校验失败时按 `settings.max_repair_retries` 重试；**每次真实 provider 调用前仍先 `reserve_llm_attempt`**（重试同样消耗 attempt budget）。此前首次失败直接落 `failed:StructuredOutputError`（真实 DeepSeek 首次 `/run` 复现）。
- **测试环境污染**：`db_settings` 显式置空 `DEEPSEEK_API_KEY`，避免本机 `.env` 配置真实 key 时"无凭证 → 503 / critique unavailable"集成测试变成真实外呼；`backend/.env` 中重复的 key 已移除（容器由根 `.env` 经 compose 注入）。
- 清理 5 个测试文件行首 BOM（触发 `invalid-syntax`，同时打断 `ruff` 与 `mypy`）。

## [0.1.0] — Phase 6：Release Engineering / CI / 发布就绪

**新增**

- **CI/CD**：`.github/workflows/ci.yml`（push/PR）——backend（真实 pgvector service + `alembic upgrade head` + pytest JUnit + ruff + mypy）、frontend（lint/typecheck/vitest JUnit/build）、golden evaluation（11 cases）、E2E（Playwright，真实 backend + 前端）；**默认 deterministic provider，不依赖 `DEEPSEEK_API_KEY`**；test 失败即 workflow 失败；JUnit/evaluation/screenshots 作为 artifact 上传。
- **发布脚本**：`scripts/smoke_api.py`（真实 HTTP 全链路 smoke，无凭证时明确 BLOCKED 而不伪装 PASS）、`scripts/release_smoke.py`（env / DB / API / frontend / golden 五检查，退出码权威）。
- **环境契约**：`.env.example` 覆盖 POSTGRES_* / DATABASE_URL / TEST_DATABASE_URL / CORS / LLM(可选) / EMBEDDING / 路径 / retry budget。
- **版本单一来源**：`jobfit/core/version.py`（读取 package metadata），`/health` 暴露 `status` + `version`（不再暴露运行环境名）。
- **容器化**：`backend/Dockerfile`、`frontend/Dockerfile`（两阶段）、根 `docker-compose.yml`（db+backend+frontend）；**镜像已在本机构建成功**。
- **发布文档**：`docs/release.md`、`docs/interview-notes.md`、`docs/interview-demo.md`、`CHANGELOG.md`、PR 模板、`LICENSE`(MIT)、根 `.gitignore`。
- **可访问性**：E2E 引入 `@axe-core/playwright`，对 Dashboard / Analysis / Report / Review 做 critical 级 a11y 断言。
- **测试**：`test_phase6_release.py`（环境契约 / CI 契约 / 版本一致性 / 仓库卫生 静态门禁）、E2E release smoke + a11y。

**修复**

- Docker 镜像构建阻塞：本机 Docker 引擎无镜像源且直连 Docker Hub 失败 → 配置 `registry-mirrors`（daocloud / 1ms.run / xuanyuan / rat.dev）并重启引擎后，`backend` / `frontend` 镜像构建成功。

## [0.1.0] — Phase 5：Productization / Evaluation / E2E

- **前端**：Next.js（App Router）+ TypeScript，`/`、`/jobs`、`/analyses/new`、`/analyses/[id]`、`/reports/[id]`、`/review/[id]`；统一 `lib/api` 客户端；presentation-only（不重算分数/规则）；PII-safe；语义化 HTML + a11y 基线。
- **后端 API 打磨**：统一错误契约 `error.{code,message,details,request_id,retryable}`（保留 `detail` 兼容）、`X-Request-ID` 关联、CORS 显式白名单、`GET /analyses`（分页确定性顺序）、`GET /profiles`（PII-safe）、`GET /analyses/{id}/evidence`。
- **确定性评测**：`tests/golden/{cases,expected,fixtures}` 声明式 11 场景 + 受控指标；`python -m jobfit.evaluation` 产出 `phase5-evaluation.{json,md}`（代码计算，无模型自评）。
- **E2E**：Playwright + test-only backend harness，12 场景（含 citation 安全 / review 生命周期 / 非法转移）。
- **ADR-040 ~ ADR-043**：前端架构、错误契约与请求关联、评测 runner、E2E 策略。

## [0.1.0] — Phase 4：LLM Critique / Evidence-Grounded Report / HITL

- `CritiqueSchema` 结构化输出 + `CitationValidator`（作用域隔离、fabricated/跨 analysis 拒绝、UNKNOWN 越权拒绝、excerpt hash 校验）。
- critique 指纹幂等（同配置复用、不重复调 LLM）；无凭证落 `unavailable`（EXTERNAL CREDENTIAL BLOCKED）。
- 报告确定性装配 + `ReportValidator` 交叉校验；stage `draft → validated → final` 单向且 final 不可改。
- HITL 状态机（approve / reject / request_changes）+ 单条条件 UPDATE 并发安全 + `reviews`/`audit_log` 审计。
- ADR-036 ~ ADR-039；golden evaluation foundation。

## [0.1.0] — Phase 3：Deterministic Analysis Engine

- 硬条件三值判定（MET / NOT_MET / UNKNOWN）+ reason code + gate；**UNKNOWN 是一等公民**。
- 确定性技能匹配（证据优先级：structured > source chunk > retrieved > absence）。
- 证据检索：anchor gate + pgvector 余弦 + lexical fallback，document scope 隔离。
- 决策链（metadata-only，不落简历文本）、加权评分（UNKNOWN 不参与分母）。
- 结果 identity 唯一键 + lease fencing（四条件断言）+ requeue；ADR-030 ~ ADR-035。

## [0.1.0] — Phase 2：Document Intelligence Foundation

- 解析（TXT/PDF 文本层/DOCX）+ 确定性滑窗 chunking；parser/chunker 版本化。
- 结构化抽取（严格 DTO）+ 逐字 anchor grounding + 不可变 profile artifact（五元组唯一）。
- 幂等写入（唯一键 + `ON CONFLICT DO NOTHING`）；ADR-024 ~ ADR-029。

## [0.1.0] — Phase 1：基础设施与契约

- FastAPI app 工厂、错误体系、pydantic-settings 配置、SQLAlchemy 2.0 + Alembic baseline、上传校验（magic bytes / NUL / 路径穿越防护）、structlog PII 脱敏、LLM Provider Protocol（DeepSeek）、PG 任务队列 + lease fencing 原语；ADR-001 ~ ADR-023。

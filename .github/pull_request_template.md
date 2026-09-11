<!-- 简短 PR checklist（Phase 6 §15）。保持简洁，不做流程表演。 -->

## 变更说明

<!-- 为什么改（why），而不是逐行复述 what -->

## 影响面

- [ ] 影响确定性引擎（matching / scoring / constraints / trace）
- [ ] 影响 LLM 契约（critique schema / prompt / provider）
- [ ] 影响 API 契约（OpenAPI / 错误体 / 状态码）
- [ ] 影响数据库 schema（是否新增 Alembic migration？）
- [ ] 影响前端（页面 / API client）
- [ ] 仅文档 / 配置

## 自检（必须真实执行）

- [ ] `cd backend; ruff check src tests alembic`
- [ ] `cd backend; mypy src/jobfit tests`
- [ ] `cd backend; pytest tests --junitxml=../phase6-junit.xml`（**exit code 权威**）
- [ ] `cd backend; alembic check`（无 schema drift）
- [ ] `cd frontend; npm run lint && npm run typecheck && npm run test`
- [ ] 涉及前端/后端交互时：`cd tests/e2e; npx playwright test`
- [ ] 涉及 golden 数据集/指标时：`cd backend; python -m jobfit.evaluation`

## 红线（任一违反请先修复）

- [ ] 未提交任何真实密钥（`.env` / key / token）；`DEEPSEEK_API_KEY` 只经环境注入
- [ ] 未提交真实 PII（简历/手机号/邮箱）；fixtures 全部为合成数据
- [ ] 确定性结论仍由确定性代码产出；LLM 不得覆盖硬条件/分数（UNKNOWN 保持）
- [ ] 未引入 production fake provider / mock 数据 / TODO 占位实现
- [ ] 测试失败不会被吞掉（无 `continue-on-error` / 无 `except: pass`）
- [ ] 未把 "planned" 写成 "implemented"（ADR-021）

## 证据

<!-- 粘贴关键命令的真实输出摘要（tests/failures/errors/skipped）或报告链接 -->

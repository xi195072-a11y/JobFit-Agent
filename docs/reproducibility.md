# JobFit Agent — 版本化与可复现性（Reproducibility）

> 关联：[docs/architecture.md](./architecture.md)（§4.5/§5）、[docs/decisions.md](./decisions.md)（ADR-019、ADR-023）
> 目标：**同一输入在版本演进后，仍能还原"当时为什么得到这个结果"。**
> 前提：解析/抽取产物以**不可变、版本化 document artifact** 存储，analyses 显式绑定本次使用的 profile artifact（ADR-023）——本文件的版本模型建立在 artifact 不可变 + 显式绑定之上。

---

## 1. 目标与范围

- **可复现（deterministic）**：抽取缓存之后，硬条件判定、证据检索（keyword）、计分、trace 在同一版本快照下输出逐位一致。
- **可追溯（versioned）**：规则、权重、提示词、抽取 schema、模型全部版本化；每次分析记录当时使用的完整配置。
- **可重放（replayable）**：用**分析时快照**离线重放确定性阶段，不依赖当前代码配置。
- **不承诺**：LLM 输出不可逐 token 复现；可复现的是"决策路径"，而非 critique 文本。

---

## 2. 版本维度与来源

| 维度 | 字段 | 来源 | 变化时机 |
| --- | --- | --- | --- |
| pipeline_version | `analyses.pipeline_version` | env `PIPELINE_VERSION` 或 git commit（部署时注入） | 每次发布 |
| extraction_schema_version | `analyses.extraction_schema_version` | `ResumeProfile.__schema_version__` / `JDProfile.__schema_version__`（如 `resume.v3.jd.v2`）| schema 变更时 |
| prompt_version | `analyses.prompt_version` | config/prompts/*.j2 内容哈希（前缀 `h:`）| prompt 变更时 |
| ruleset_version | `analyses.ruleset_version` | 四份规则 YAML 声明 `version` 的组合哈希（前缀 `r:`）| 任一规则文件变更 |
| scoring_version | `analyses.scoring_version` | `scoring.yaml` 的 `version`（前缀 `s:`）| 权重变更时 |
| llm_model | `analyses.llm_model` | provider 实际模型名 | 每次分析 |
| embedding_model | `analyses.embedding_model` | embedding provider 模型名（V1 起）| 每次分析 |
| config_snapshot | `analyses.config_snapshot` | 解析并序列化后的 5 份 YAML 全量 JSON | 每次分析入队时固化 |
| parser_version | `parsed_documents.parser_version` | parser 代码/库版本 + 解析参数哈希（前缀 `p:`）| parser 变更时（新 parse artifact）|
| profile artifact 指纹 | `resume_profiles` / `jd_profiles` 的 `UNIQUE(document_id, pipeline_version, extraction_schema_version, prompt_version, llm_model)` | 同左五元组 | 任一维度变化 → 新 artifact（不可变，不 overwrite）|
| artifact 绑定 | `analyses.resume_profile_id` / `jd_profile_id` | extract 节点 guarded UPDATE（fencing）| 每次 analysis |

**版本组合规则：**
- `ruleset_version = hash(education_rules.version || location_rules.version || language_rules.version || skills.version)`。
- 版本哈希计算时**排除注释与空白**（只对语义内容做 canonical 序列化），避免无意义 diff 造成版本漂移。
- Python 源码与配置的边界：**源码变更不改变 ruleset/scoring 版本**；配置变更必须同时变更对应 YAML 的 `version`，否则 `loader` 校验失败（防"改了没升版本"）。
- **artifact 幂等复用**：同一 (document, version) 的 parse/profile artifact 只创建一次（唯一键冲突 → 复用已有行）；同一 fingerprint 绝不产生第二个 artifact；re-extract（fingerprint 变化）一律生成**新行**，不 overwrite 旧行（ADR-023）。
- **复用身份 vs 审计指纹（Phase 2 实现口径，ADR-026）**：profile 的**复用身份是五元组**（与 DB 唯一键一致），而 `_meta.fingerprint` 额外包含 `parsed_document_id`，是审计超集。因此当**仅 parse 版本变化**（五元组不变）时，DB 唯一键会判定为已存在并复用旧 profile —— **升级 parser / 解析库必须同时 bump `PIPELINE_VERSION`** 才会形成新 profile artifact。

---

## 2.1 实现现状（Phase 2 + Phase 3 落地情况）

| 维度 | 本阶段状态 | 说明 |
| --- | --- | --- |
| `parser_version` | **已实现** | `parsing/service.py: parse_version()` = parser 代码版本 + chunker 版本与参数 + pypdf/python-docx 版本（ADR-024）|
| `extraction_schema_version` | **已实现** | `ResumeProfile.SCHEMA_VERSION` / `JDProfile.SCHEMA_VERSION`（`ClassVar`，如 `resume.v1`）|
| `prompt_version` | **已实现** | `config/prompts.py` 内容哈希，单模板 `h:<16hex>`；抽取写入的是两模板的 `combined_version()`（ADR-027）|
| `llm_model` | **已实现** | 取 `analyses.llm_model`（入队快照），写入 artifact 五元组 |
| profile artifact 指纹 | **已实现** | `extraction/fingerprints.py`：canonical JSON + SHA-256，写入 `full_dump["_meta"]`（ADR-026）|
| artifact 绑定 | **已实现** | `analyses.resume_profile_id` / `jd_profile_id`，经 `fenced_bind_profile` 四条件 guarded UPDATE（ADR-017/ADR-023）|
| `pipeline_version` | **已实现（值来自部署注入）** | env `PIPELINE_VERSION`；不自动计算 git commit |
| `ruleset_version` / `scoring_version` | **已实现（Phase 3）** | `config/loader.py`：6 份 YAML（education/location/language/skills/constraint_rules/retrieval）版本组合哈希 `r:<8hex>`；`scoring.yaml` 版本 `s:<ver>` |
| `config_snapshot` | **已实现（Phase 3）** | 入队时固化；**执行期只读快照**：`RuleConfig.from_snapshot()` 在快照缺失时显式失败，禁止回退到磁盘当前配置 |
| `embedding_model` | **已实现（Phase 3，MVP 基线）** | `hash-ngram-v1`（ADR-030）；写入 `analyses.embedding_model` 与 `document_chunks.embedding_model`，向量检索严格同模型 |
| analysis 身份 | **已实现（Phase 3）** | `an:<sha256>` = f(profile/document pair, pipeline_version, extraction_schema_version, prompt_version, ruleset_version, scoring_version, embedding_model)；命中即复用（ADR-034）|
| 结果 identity | **已实现（Phase 3）** | 约束/技能 `(analysis_id, requirement_id, ruleset_version)`；trace `(analysis_id, decision_type, decision_key)`；score `(analysis_id, kind)` |
| 决策链可重放 | **已实现（Phase 3）** | trace 链只由输入 artifact + ruleset_version + 结果决定；测试断言两次独立 analysis 的链逐字段一致（ADR-033）|
| critique `prompt_version` | **已实现（Phase 4）** | `critique/prompt.py` 模板内容哈希 `h:<16hex>`，随 critique 行留档（ADR-027/ADR-036）|
| critique 指纹 / 幂等 | **已实现（Phase 4）** | `f(analysis_id, prompt_version, provider, model, config_snapshot)` → `critiques.fingerprint`；`UNIQUE(analysis_id, fingerprint)` 使同配置重跑复用同一行、不重复调用 LLM（ADR-036）|
| report 指纹 / 版本 | **已实现（Phase 4）** | `f(analysis_id, constraints, skill_matches, score, critique_fingerprint)`；`UNIQUE(analysis_id, version)`，stage `draft→validated→final` 单向且 final 不可改；`report.meta` 记录 builder 版本与 versions/models 快照（ADR-037）|
| review 审计 | **已实现（Phase 4）** | 每次 reviewer action 落一行 `reviews`（append-only，含 `from_state`/`to_state`）+ 一条 `audit_log`；并发由单条条件 UPDATE 原子裁决（ADR-038）|

> 尚未实现：`make evaluate` 便捷命令（后续阶段）。golden 集已于 Phase 4 落地于 `backend/tests/golden/`（§34 / ADR-039）。
> **Phase 5 起可复算的评测结果**：`python -m jobfit.evaluation` 会运行 golden 集并产出 `phase5-evaluation.json` /
> `phase5-evaluation.md`（含每个 case 通过与否、失败原因、以及六项确定性指标的 passed/total）。
> 指标全部由代码计算，**不含模型自评**（ADR-042/§54）。
> 未实现项不得在 README / 对外材料中声称已落地（ADR-021）；实现清单见 architecture.md §12.1。

---

## 3. 配置文件的形状（草案）

```yaml
# backend/config/scoring.yaml
version: "2.1"
sections:
  hard_constraint_gate: {kind: veto}          # 硬条件作为门禁而非分值（细则见 scoring 文档）
  skills:      {weight: 0.35}
  experience:  {weight: 0.25}
  education:   {weight: 0.15}
  location:    {weight: 0.15}
  language:    {weight: 0.10}
flags:
  claimed_only_penalty: 0.5                   # 仅自称证据的折扣系数
```

```yaml
# backend/config/education_rules.yaml
version: "1.3"
degree_levels: {博士: 4, 硕士: 3, 本科: 2, 大专: 1, 高中: 0, UNKNOWN: null}
rules:
  - id: edu.degree_at_least
    param: degree_level
  - id: edu.major_in
    param: majors
```

> `skills.yaml`（词表/同义词/category）、`location_rules.yaml`（别名/归一化）、`language_rules.yaml`（等级映射）结构类似：顶部 `version`，主体为纯数据。**无任何 Python 逻辑。**

---

## 4. 快照生命周期

1. **入队**：POST /analyses 时 `loader` 读取当前配置 → 计算各版本 → 把 `PipelineVersions` + `config_snapshot` 与 queued 行一并写入 `analyses`（同一业务事务内，与 LangGraph checkpoint 无关）。
2. **执行**：Worker 只使用 `analyses.config_snapshot`（而非启动时全局配置）驱动确定性阶段 → 保证"入队那一刻的规则"就是执行时的规则。
3. **执行（artifact 产出与绑定）**：parse 产出不可变 `parsed_documents`（`UNIQUE(document_id, parser_version)`，存在即复用）；extract 产出不可变 profile artifact（五元组唯一，存在即复用，re-extract = 新行不 overwrite）；随后以 guarded UPDATE（fencing）把 `analyses.resume_profile_id` / `jd_profile_id` 绑定到本次实际使用的 artifact（ADR-023）。
4. **演进**：新发布改了权重/模型 → 新分析入队时快照新版本、绑定新 fingerprint 的 artifact（如有）；旧分析行与旧绑定**不变**。
5. **报告**：report.meta.versions 展示快照与绑定的 artifact id，前端可查看。

---

## 5. 离线重放流程（Replay）

用途：审计、回归调试、回答"为什么当时 72 分"。

前置：目标 analysis 至少已进入 `validate_hard_constraints` 之后（绑定的 parse/profile artifact 已落库）。

```
1. 取 analyses 行 → config_snapshot（拒绝使用当前文件配置）
2. 以 analyses.resume_profile_id / jd_profile_id 读取本次绑定的不可变 artifact
   （parsed_documents.full text + resume_profiles/jd_profiles full_dump + document_chunks）
   —— 旧 analysis 永远读取旧 artifact，绝不引用"当前最新版本"
3. 用 snapshot 重建 RuleSet 与 ScoringConfig（版本哈希校验一致）
4. 重跑 validate_hard_constraints → retrieve_evidence(keyword) → match_skills
   → compute_base_score（全确定性，无 LLM 调用）
5. 断言：verdicts/evidence/trace 与库中记录完全一致；score_snapshots.total 一致
```

- 结果一致 → 环境自洽；不一致 → 定位为 bug/漂移（记录缺陷，不回改历史）。
- 重放是**只读**操作，不得写业务表（输出到临时 schema 或内存）。
- 若发现绑定的 artifact 缺失/被篡改（sha 校验失败），说明数据损坏，按保留期/审计流程处理，而非回填覆盖。

---

## 6. Schema 演进规则

- `ResumeProfile`/`JDProfile` 的任何字段/枚举/约束变化必须递增 `__schema_version__` 并新增 Alembic 迁移；`parser` 变更必须递增 `parser_version`（新 parse artifact）。
- **禁止 overwrite / 跨版本复用**：schema/版本变化时对新分析走"建新 artifact → 绑定新 `resume_profile_id`/`jd_profile_id` → 使用"，旧 analysis 保持旧绑定，旧 artifact 行不改写；`analyses` 的版本快照字段与所绑定 profile artifact 的 fingerprint 不一致时视为数据不一致，**不得**送入规则引擎（需重新抽取或显式降级 `UNKNOWN` + 人工）。
- profiles 的 JSONB `full_dump` 保留旧结构（dump 时记录 schema_version），便于旧版本代码语义读取。
- `parsed_documents` 记录 `content_sha256`，`document_chunks` 记录 `span_sha256`——任何篡改/半写都会在校验时暴露（结合 ADR-023 的不可变 + 唯一键约束）。

---

## 7. Embedding 版本约束（V1 起）

- `document_chunks.embedding` 记录 `embedding_model`；**禁止跨模型混用**（查询向量必须与库内向量同模型，否则重嵌入）。
- 更换 embedding 模型 = 数据迁移：全部重嵌入 + 版本标记 + hit@k 回归对比。

---

## 8. 测试与 CI

- `test_config_versions.py`：加载校验、版本哈希稳定性（同内容同哈希/注释不影响）、快照生成与还原 round-trip。
- `test_scoring.py`：同 snapshot 两次运行 total 一致；不同 snapshot 权重生效。
- artifact 测试（`test_parsing.py`/`test_extraction.py`/集成）：同 (document, parser_version) 幂等复用；同 fingerprint 不产生第二行；re-extract 建新行不 overwrite；analyses 绑定 id 与 fingerprint 一致性断言。
- golden 回归每次跑在**固定 snapshot** 上；`make evaluate` 时打印各维度版本与绑定的 artifact id，便于对比历史。

> 本文件与 ADR-019 / ADR-023 同源；任何偏离需先修订文档再改代码。

> **实现状态（Phase 2）**：本节列出的 `test_config_versions.py` / `test_scoring.py` 尚不存在（属 `matching/*` 阶段）。
> 本阶段**已实现**的相关测试为：`tests/unit/test_prompts.py`、`test_fingerprints.py`、`test_chunker.py`、`test_parser.py`
> 与 `tests/integration/test_phase2_artifacts.py`（幂等复用 / 唯一约束 / 同 fingerprint 不产生第二行 / prompt 版本变化建新行 / 绑定一致性）。

# JobFit Agent — 决策记录（Architecture Decision Records）

> 每条记录格式：Context（背景）→ Decision（决策）→ Consequences（后果/取舍）→ Alternatives（被否方案与理由）。
> 新决策一律追加编号；修订既有决策需新增一条并标记 `superseded by`。
> 关联文档：[docs/architecture.md](./architecture.md)

---

## ADR-001：Deterministic Core + LLM Critique（证据先行的双层架构）

**Status:** Accepted（MVP 生效）

**Context:** 直接让 LLM 输出"匹配分/是否合格"会产生不可复现、不可审计、易幻觉的结果，与"Evidence-Grounded"定位冲突。同时完全不用 LLM 又无法处理开放文本的语义。

**Decision:** 系统拆成两层：
- **确定性核心层**（纯代码）：解析、硬条件规则判定、技能匹配、证据检索、基础计分。输入相同则输出逐位相同。
- **LLM 层**：仅两个职责——(a) 把非结构化文本抽取为结构化事实（Pydantic + anchors）；(b) 基于证据集合生成 critique/建议（结构化输出 + 引用校验）。LLM **永远不写入**最终分数或终审二元结论。

**Consequences:**
- 优点：可复现、可单测、可解释；LLM 失效时确定性部分仍可发布（critique 标 `UNAVAILABLE`）；面试叙事清晰（工程可靠性优先）。
- 代价：规则引擎需持续维护词表/模式；开放语义的收益被限制在 critique 层。

**Alternatives:**
- 端到端 LLM 打分：否决，违反 NFR-2/可审计性。
- 完全规则系统：否决，无法处理 JD/简历的开放式表述。

---

## ADR-002：所有 LLM 交互强制 Pydantic Structured Output

**Status:** Accepted

**Context:** JSON 模式输出仍可能格式非法、字段越界、缺字段；未经 schema 校验的数据进入主表会污染下游。

**Decision:** 所有 LLM 调用的输出契约是 Pydantic v2 model。调用链统一为：`调用 → 解析 → model_validate → 失败重试(1 次，带纠错提示) → 仍失败则降级 UNAVAILABLE/UNKNOWN`。核心字段若为 `None`/缺省 → 必须显式落到 `UNKNOWN` 语义，禁止静默默认值。

**Consequences:**
- 抽取层输出的每个字段都有类型与枚举约束；critique 引用必须是 `evidence_id` 列表（供引用校验器二次确认）。
- 代价：schema 设计成本前置；prompt 必须与 schema 高度对齐。

---

## ADR-003：LLM Provider Abstraction（Protocol，默认 DeepSeek）

**Status:** Accepted

**Context:** MVP 用 DeepSeek API，但代码不得写死供应商；面试与技术债都不允许锁定。

**Decision:** 定义最小 `LLMProvider` Protocol：`complete_structured(prompt, schema, ...) -> Model` 与 `complete_text(...)`。DeepSeek 为第一个实现；配置经 `settings.llm.provider` 选择。不为"插件化"过度设计——只抽象真实使用到的两个方法。

**Consequences:**
- 优点：可 mock 测试、可换供应商、成本可控。
- 代价：抽象只覆盖当前需求；若未来需要流式/工具调用再扩展（届时走 ADR）。

---

## ADR-004：用 LangGraph 编排流水线（含 HITL interrupt）

**Status:** Accepted（MVP 生效）

**Context:** 需要：阶段可观测、失败可重试、进程崩溃可恢复（避免重复烧 LLM token）、发布前必须人工审批（挂起/恢复）、后续加节点不改调用方。

**Decision:** 用 LangGraph `StateGraph` 建模 1 对（resume, JD）分析；PostgreSQL checkpointer 落 checkpoint；`review` 节点用 interrupt 实现 HITL，审批接口以 `Command(resume=...)` 恢复图。每节点声明 RetryPolicy；确定性节点失败=致命（failed 终态），critique 节点失败=降级（可继续）。**图的执行从 FastAPI 进程迁出**：由独立 Worker 进程在持 lease 后驱动（执行机制部分被 ADR-017 superseded；图本身设计不变）。

**Consequences:**
- 优点：checkpoint/恢复/interrupt 是本项目 HITL 与防重复调用的关键，自研成本高；图拓扑即文档。
- 代价：新增一个重量依赖；其底层状态存储占用数据库表；团队成员需掌握 LangGraph 语义。

**Alternatives:**
- 手写状态机 + FastAPI BackgroundTasks：否决——恢复、重试、interrupt 需自行实现且难验证。
- Prefect/Temporal：否决——偏通用任务编排，图内 condition/interrupt 语义不如 LangGraph 贴合 agent 流程。

---

## ADR-005：PostgreSQL + pgvector + SQLAlchemy 2.0 + Alembic

**Status:** Accepted

**Context:** 需要事务一致性、JSONB 存抽取结果、关系行支撑匹配查询、V1 的 embedding 检索，且全部要能在 Docker Compose 单机跑。

**Decision:** 单库 PostgreSQL；抽取全文 JSONB + 可查询实体关系化；`document_chunks.embedding vector` 用 pgvector（V1 迁移时启用，HNSW 索引）。ORM SQLAlchemy 2.0（类型化），迁移 Alembic。

**Consequences:**
- 优点：向量与业务数据同事务，无需引入独立向量库；运维简单。
- 代价：pgvector 索引需调优；JSONB + 关系行双写需在仓储层保证一致性（UoW）。

**Alternatives:**
- 独立向量库（Milvus/Qdrant）：V1 再评估；MVP 无此复杂度收益。
- MongoDB：否决——需要强事务与关系查询。

---

## ADR-006：Embedding 可插拔，默认本地 BAAI/bge-m3（V1 启用）

**Status:** Accepted（接口先定义，MVP 不依赖实现）

**Context:** 关键词证据检索无法覆盖同义/语义改写；但 API embedding 引入供应商与成本，且需联网。

**Decision:** 定义 `EmbeddingProvider` Protocol（`embed(texts)->list[vector]`）；V1 默认本地 `bge-m3`（多语言、MIT 协议、可离线），运行于 docker-compose 内；MVP 阶段仅保留接口与关键词检索实现，**不因"看起来高级"提前引入向量调用**。合同规定：没有 embedding 结果时证据检索仍可用（keyword fallback），embedding 只是加分项。

**Consequences:**
- 优点：离线可跑、无供应商锁定、语义检索可控引入。
- 代价：本地模型占内存；需 embedding 质量回归（hit@k）。

---

## ADR-007：证据锚定（Evidence Anchoring）与"禁止自由引用"

**Status:** Accepted

**Context:** 反幻觉最有效的手段不是"告诉模型别编"，而是从机制上让引用无法凭空产生。

**Decision:**
1. 解析产物保留字符级/页级 anchor（`page, char_start, char_end` + 片段 sha256）。
2. 每个抽取字段携带 `evidence_ids` 指向 `document_chunks.id`。
3. critique/建议的引用字段只能是 `evidence_id[]`，后端 **citation validator** 校验全部命中本次证据集合，否则该条目降级标注并阻止进入终版。
4. 报告不渲染自由文本引用。

**Consequences:**
- 优点：报告里每个 claim 可"点开看原文高亮"；LLM 幻觉引用在机制层被封死。
- 代价：切块策略（chunk 边界与 anchor 映射）需要仔细设计与测试。

---

## ADR-008：`UNKNOWN` 是一等公民，而非空值/默认值

**Status:** Accepted

**Context:** 简历没有 JD 要求的信息（如无毕业年份）时，系统若给"不满足"就是误导性结论。

**Decision:** 所有匹配结果三值枚举 `MET / NOT_MET / UNKNOWN`。规则引擎判定"证据不足"必须返回 `UNKNOWN` 并把该项推进人工复核清单。UI、API、报告对 `UNKNOWN` 有专属呈现，禁止映射为"不匹配"。评分中 `UNKNOWN` 项既不得分也不扣分，但计入 `confidence_flags`（总分旁边显示"N 项待确认"）。

**Consequences:**
- 优点：诚实、可审计、引导人工聚焦。
- 代价：报告需要更强的表达设计；rule 需要显式覆盖"证据不足"分支。

---

## ADR-009：Human-in-the-loop 为强制发布闸门

**Status:** Accepted（MVP 生效）

**Context:** LLM 输出与自动判定在面试/求职场景可能造成误导；"未经审批不得发布"是对使用者负责的底线。

**Decision:** 报告只有 draft 与 final 两态；final 只能经 `review` 节点审批产生。审批支持：approve（发布）、request_changes（记录意见，可选触发 critique 重跑或人工编辑 draft）、reject（终态失败）。Reviewer 可对单项硬条件 override，全部写 `reviews` + `audit_log`。

**Consequences:**
- 优点：可审计闭环；把"AI 判定错误"的兜底放到人。
- 代价：端到端多一步人工；批量排名（V1）中每个单项仍各走各的审批。

---

## ADR-010：硬条件判定不依赖 LLM 终审

**Status:** Accepted

**Context:** 教育/年限/地点等若由 LLM 直接判"是否满足"，会引入不可复现与误判。

**Decision:** JD 的硬要求先由抽取层产出**结构化的 `(req_type, operator, value)`**；判定阶段由确定性规则对"简历结构化事实"执行比较（学历等级、年限 >=、地点归一化匹配等）。LLM 只出现在"抽取字段本身"（且字段带 anchors）。规则无法覆盖的（operator 语义不明/字段缺失）→ `UNKNOWN`。

**Consequences:**
- 优点：判定可复现、可测、易解释（报告展示"应用了哪条规则"）。
- 代价：规则引擎需要维护"学历等级映射、地点归一化、时间解析"等知识组件。

---

## ADR-011：PDF/DOCX/TXT 白名单；扫描件不进 MVP

**Status:** Accepted

**Context:** 恶意/损坏文件与 OCR 高成本。版式解析与扫描件会显著扩大攻击面与工作量。

**Decision:** MVP 支持 PDF（仅文本层）、DOCX、TXT；上传以 magic bytes 嗅探；解析设大小/解压比/时长上限；无文本层 PDF → 明确报错提示"请提供含文本层的 PDF 或 TXT"，不静默输出空结果。URL ingestion 一律推迟至 V2（见 ADR-012）。

**Consequences:**
- 优点：攻击面小、管线聚焦、实现快。
- 代价：扫描件用户暂不支持（文档中明确标注）。

---

## ADR-012：MVP/V1 不开放 URL Ingestion（规避 SSRF）

**Status:** Accepted

**Context:** 未来支持"贴 URL 抓取 JD"时，服务端抓取天然暴露 SSRF（内网/云元数据/DNS rebinding）。

**Decision:** 该能力定档 V2，且实现前置条件固定为：协议白名单、连接前 IP 校验（拒私网/环回/链路本地/元数据）、禁跳转越界、下载大小与超时上限、无凭证透传、独立抓取进程。条件未满足前不开放。

**Consequences:**
- 优点：第一阶段攻击面受控。
- 代价：需求侧需接受"JD 复制粘贴"。

---

## ADR-013：密钥与敏感信息治理（.env + 脱敏 + 加密 + 审计）

**Status:** Accepted

**Context:** API key 泄漏、日志打全文、PII 落明文都是面试项目里最容易被扣分的点。

**Decision:**
1. `.env.example` 入库、`.env` 忽略；所有密钥仅经 pydantic-settings 读取。
2. 日志层全局过滤：任何解析文本在 debug 日志前脱敏（手机号/邮箱/姓名正则 + 长度截断），默认 info 级不打印内容。
3. PII 字段（name/phone/email）可选列级 AES-GCM 加密（`PII_ENC_KEY`），库内索引用 sha256 值。
4. 报告/API 响应默认不含手机号、邮箱。
5. override/审批/查看敏感信息等动作写 `audit_log`。

**Consequences:**
- 优点：默认安全，可审计。
- 代价：加解密/脱敏逻辑需配套测试（`test_redact.py`）。

---

## ADR-014：可观测性默认零依赖（structlog），LangSmith 为可选开关

**Status:** Accepted

**Context:** 第一阶段不应为跑通而依赖 LangSmith（离线/面试演示环境可能不可用）；但又不能裸奔。

**Decision:** 内置 `structlog` + 结构化日志 + `job_runs` 进度/事件表 + `/health` 探针作为默认可观测性。LangSmith 通过环境变量开关启用（`LANGSMITH_ENABLED=true` 才初始化 tracer），默认关闭，不阻塞运行。

**Consequences:**
- 优点：开箱即用、无外部依赖；面试可现场"打开 LangSmith 演示 tracing"。
- 代价：LangSmith 集成保持薄层，后续再完善。

---

## ADR-015：Counterfactual 与批量排名建立在分数分解之上（不重跑整图）

**Status:** Accepted（V1 功能；MVP 预留计分内核）

**Context:** Counterfactual 若"把技能塞回简历重跑一遍"成本高且污染数据；批量排名若每个 pair 都跑满全链路则成本失控。

**Decision:**
- `score.py` 以**纯函数 + 分数分解**实现：`total = Σ w_i · section_i`，每 section 由可注入的"技能集合/经历属性"驱动 → Counterfactual = 在快照上改输入集合重算，得到 Δ 分；可选再调一次 critique（增量成本明确）。
- 批量排名 = 同一 resume 对 N 个 JD 并行跑图实例（图内仍是单 pair），聚合层排序，并标注每项硬条件命中数。

**Consequences:**
- 优点：Counterfactual 秒级响应、可测、不污染原分析；批量成本可控。
- 代价：需保证 score 纯函数严格无副作用（测试强制）。

---

## ADR-016：每 phase 必须有测试；golden dataset 驱动回归（指标语义见 ADR-022）

**Status:** Accepted

**Context:** "证据引用"系统最大的风险是抽取/检索/规则回归没人发现；且访谈看重质量工程意识。

**Decision:** 目录 `tests/unit|integration|golden`；golden fixtures 含 happy path 与对抗样例（恶意注入、损坏 PDF、超大文件、矛盾证据、缺证据、gold=UNKNOWN 样例）。指标清单与**语义**（default target / release gate / measured result / dataset baseline 四类区分，指标数值为 dataset 相关的默认 target 而非普适真理）见 architecture.md §9.3 与 ADR-022：未实测的数值不得作为 gate、不得写入 README；只有按 §9.3 升级为 gate 的指标才在 CI 中硬性执行。任何 phase 没有测试不得合入。

**Consequences:**
- 优点：质量可度量、可对面试官演示"我们怎么防幻觉"。
- 代价：fixture 标注工作前置，属预期成本。

---

## ADR-017：Job Runner & Crash Recovery（API/Worker 分离，PG 任务队列）

**Status:** Accepted（MVP 生效）；supersedes ADR-004 的执行机制部分

**Context:** v0.1 在 FastAPI 内用后台任务执行 LangGraph，无法完整实现 crash recovery（进程被杀后 queued/running 任务丢失或悬死）。但 MVP 又不应引入 Celery/Redis/Kafka 等中间件。

**Decision:**
1. **两个逻辑进程**：`api`（FastAPI，只负责上传校验/去重 + 写 `analyses(status='queued')` + 读报告）与 **dedicated worker**（独立进程，可独立重启，默认单副本、支持 N 副本）。API 不执行 graph、不 import workflow runtime。
2. **PostgreSQL 即任务队列**：`analyses` 表承载 job 状态（status + lease 字段 + 部分索引），无外部消息中间件。
3. **原子 claim（生成 claim_token）**：`UPDATE … SET status='running', claimed_by=…, claim_token=gen_random_uuid(), lease_expires_at=… WHERE id=(SELECT id … WHERE status='queued' … FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING …`；同一 analysis 至多一个 Worker 持有。每次 claim 生成唯一 `claim_token`（fencing token）。
4. **lease + heartbeat（四条件续租）**：running 行有 `lease_expires_at`，Worker 以 `UPDATE analyses SET last_heartbeat_at=clock_timestamp(), lease_expires_at=clock_timestamp()+… WHERE id=:aid AND claim_token=:tok AND status='running' AND lease_expires_at>clock_timestamp()` 周期续租（单条短事务，lease 一律用 `clock_timestamp()`/statement time，**不使用长事务中的 `now()`**，见第 11 条）——**拒绝已过期 lease**；**心跳 0-row ⇒ Worker 立即停止 graph execution**（并停止一切后续写入）。进程崩溃 → 心跳停 → lease 过期 → 该行视为 stale → `recover_stale_jobs()` 重排 `queued` → 再被认领（新 claim_token）。`requeue_count` 超限 → `failed`。recover 与心跳两条路径均为**单条原子 UPDATE**，由提交顺序唯一决定结果（不存在双方都认为有权执行的窗口；详见 architecture.md §3.3/§4.3）。
5. **Claim Fencing Token（四条件 lease fencing，防 stale worker 脏写）**：对持 lease 的 Worker 在 running 下的**一切 analysis-scoped 写入**——`hard_constraint_results` / `skill_match_results` / `decision_traces` / `score_snapshots` / `critiques` / `reports` / `job_runs` / `llm_attempts_used` 预算预占 / `current_phase` / 心跳续租 / `analyses.resume_profile_id`·`jd_profile_id` 绑定——都必须满足**四条件** `analysis_id + claim_token + status='running' + lease_expires_at > clock_timestamp()`。（API 侧非 Worker 写入如审批 `reviews` 不持 claim_token，改走 `WHERE status='awaiting_review'` 状态 CAS 与乐观锁，不属于本 fencing 范围。）**Document-scoped artifacts 不属于 fencing 写入**：`parsed_documents` / `document_chunks` / `resume_profiles` / `jd_profiles` 及其 profile 实体子行是**不可变、按 (document, version) 唯一**的 artifact，只 INSERT + 只读、禁止 overwrite（数据 ownership 与版本化模型见 ADR-023）。**analysis-scoped child INSERT 禁止"先 SELECT 校验、再 INSERT"（TOCTOU）**；只允许 parent-row lock（`FOR UPDATE` 父行 + 校验 + 同事务写）、guarded INSERT（`INSERT … SELECT … FROM analyses WHERE <四条件> RETURNING`）、guarded UPDATE（`WHERE analysis_id+claim_token+status+lease>clock_timestamp()`）三种原子语义；断言失败（0 行/无权）→ Worker 已失去独占权 → **立即中止执行并停止一切后续写入**。由此，job 被其他 Worker reclaim（新 token + 新 lease）后，stale worker 无法产生任何脏写。
6. **checkpoint 续跑而非重跑**：以 `analyses.graph_thread_id`（= analysis id）为 LangGraph thread，恢复后从最后 checkpoint 续跑。"已完成节点不重跑"由**业务表状态 + reconciliation** 判定，**不宣称跨域原子性**（见第 7 条）。
7. **LangGraph checkpoint 与 business state 是两个 durability domain**：
   - checkpoint 只承载**图运行态**，用于续跑，非权威业务记录；`analyses`/profiles/constraints/traces/score 等**业务表**为权威记录；
   - **不宣称两者共享同一原子事务**：只有跨表原子性被实际实现并通过测试后，才允许声称 transactional atomicity（本 ADR 默认不声称）。
8. **crash consistency 机制组合**：idempotent node（业务写入幂等/自然键约束，重放无重复副作用）+ 状态 CAS（`WHERE status=…` / claim 断言）+ claim fencing（第 5 条）+ reconciliation（认领恢复时以业务表为准、校验 claim_token/status 后再继续剩余节点）。**执行语义统一为：无任务丢失；已提交业务结果不产生重复副作用；未提交节点允许重跑——不声称 exactly-once execution。**
9. HITL 审批续跑同样走队列：审批写入 `reviews` + 将 analysis 置回 `queued`（`next_command` 携带 HumanDecision），Worker 领取后 `Command(resume=…)` 续跑。
10. 启动自检：Worker 启动先 `recover_stale_jobs()` 再进入 claim 循环；graceful shutdown 停止心跳并释放 lease。
11. **Lease 时间戳语义与短事务契约（v0.2.3）**：lease 有效性一律用 `clock_timestamp()`（statement time），**不依赖长事务中的 `now()`**；claim / recover / heartbeat / guarded write / reservation 全部为单条原子短事务。**禁止在持有业务 DB 事务期间执行 LLM / PDF parsing / 外部 HTTP**——节点模型为"短事务（写/校验/绑定）→ 释放 → 外部调用 → 下一短事务"，慢外部 I/O 不横跨业务事务。
12. **LLM attempt 的 call authorization window（v0.2.3）**：attempt reservation（guarded UPDATE 成功）只保证全局 budget 与"此刻持有有效 lease"，并授权紧接的一次 HTTP call；authorization window = reservation 成功至 lease 仍有效（`lease_expires_at > clock_timestamp()`）为止；HTTP 必须紧接 reservation 发起，任何新调用前必须重新 reservation（内含 lease 校验）→ **stale worker 在 lease 失效后无法启动新的 provider HTTP call**。**不声称可取消已发起的外部 HTTP request**；若调用期间 lease 过期，Worker 丢弃结果、不落库并停止（写入需四条件 fence），在途请求的费用/延迟已发生属可接受。

**Consequences:**
- 优点：无中间件依赖、单机可跑、崩溃恢复完整（**无任务丢失；已提交结果无重复副作用；未提交可重跑**，不声称 exactly-once）；四条件 fencing 断言从机制上杜绝 stale worker 脏写；面试可演示"kill worker → 任务自动恢复"。
- 代价：PG 需承担轮询压力（MVP 规模可忽略）；需要维护 lease/心跳/recover/fencing 断言逻辑与 `test_job_runner.py`（claim 竞态、心跳续租与**拒绝过期 lease/0-row 停止**、lease 过期 reclaim、**stale worker 写入被拒**、child 写入原子守卫（parent-row lock / guarded INSERT / guarded UPDATE）、崩溃续跑不产生重复副作用）等测试。

**Alternatives:**
- Celery + Redis/RabbitMQ：否决——新增外部故障点与运维负担，MVP 规模 PG 轮询足够；V2 若需要优先级/延迟队列再评估（届时新 ADR）。
- Kafka：否决——严重过度设计。
- FastAPI BackgroundTasks / 进程内线程：否决——v0.1 方案，崩溃即丢任务，不满足本 ADR 需求。

---

## ADR-018：统一 Retry Budget（禁止三层重试无限叠加）

**Status:** Accepted（MVP 生效）

**Context:** Graph 节点重试、Provider transport 重试、structured output repair 重试若各层独立且无限，会相互叠加产生不可控的 LLM 调用次数与账单/延迟；且"分层各自为政"难以测试。

**Decision:** 错误分类并归属单层处理（详见 architecture.md §4.4）：
1. transport/429/5xx → **provider retry**（`max_provider_retries`，指数退避+抖动）；
2. schema validation failure → **repair retry**（`max_repair_retries`，带纠错提示）；
3. 业务瞬时失败 → **node retry**（`max_node_retries`，只重试未提交节点）。

叠加规则：
- **Attempt Reservation（每 analysis 全局上限 `max_llm_attempts`）**：每次真实 provider HTTP 调用**之前**（transport 与 repair 的每一次都算）必须先完成原子预占——单条 guarded UPDATE（`SET llm_attempts_used=llm_attempts_used+1 … WHERE analysis_id+claim_token+status='running'+lease_expires_at>clock_timestamp() AND llm_attempts_used<:max`，独立**短事务**、先于 HTTP 提交、lease 判定用 `clock_timestamp()` 而非长事务 `now()`）；0 行 ⇒ 预算耗尽/失去 fencing ⇒ **不得发起 HTTP**，降级（extract→`UNKNOWN`、critique→`UNAVAILABLE`），analysis 置 `llm_budget_exceeded` 交人工复核。**reservation 不因 crash/节点失败回滚**（先于 HTTP 独立提交，`llm_attempts_used` 单调不减），因此**真实 provider call 数 ≤ `llm_attempts_used` ≤ `max_llm_attempts`**。每次预占写 `llm_attempt_log`（UNIQUE(analysis_id, attempt_no)）供审计"每次 call 必有对应 reservation"。reservation 成功即授权"紧接着的一次"调用（authorization window，ADR-017 第 12 条）；**已发起的 HTTP 无法取消**——lease 失效只撤销写权限，不撤销在途请求。
- 单次 LLM 任务调用数 ≤ `1 + max_provider_retries + max_repair_retries`，且整个 analysis ≤ `max_llm_attempts`；**上限取先到者**。
- 节点重试不重放已成功的 LLM 输出：其业务结果落库即视为完成（idempotent node + reconciliation 判定），**不依赖"输出与 checkpoint 同事务"**——业务表与 checkpoint 是两个 durability domain（ADR-017 第 7 条）。未提交节点重跑中的每次调用仍必须先成功预占 attempt（受 `max_llm_attempts` 约束，预留不因 crash 回滚）。

**Consequences:**
- 优点：成本与延迟有硬上限；行为可单测（`test_retry_budget.py`：reservation 先于 HTTP、不因 crash 回滚、真实调用数 ≤ `max_llm_attempts`、无乘法叠加/先到先停）；面试叙事清晰。
- 代价：预占计数器需在 DB 原子维护（guarded UPDATE + `llm_attempt_log`）；降级路径语义要写清楚（谁降级、报告怎么呈现）。

**Alternatives:**
- 三层各自无限重试：否决——不可控。
- 只靠全局计数器不做分类：否决——区分错误类型才能给出有效的重试参数。

---

## ADR-019：Pipeline & Rules Versioning（版本化规则配置 + 快照可复现）

**Status:** Accepted（MVP 生效）

**Context:** 规则/权重/提示词演进后，旧分析结果无法解释——"同一份简历上周 72 分这周 68 分"而无从考证。同时权重要求不硬编码进 Python 源码。

**Decision:**
1. **版本化 YAML 配置**置于 `backend/config/`：`scoring.yaml`（计分权重）、`skills.yaml`（词表/同义词/category）、`education_rules.yaml`（学历等级/专业规则）、`location_rules.yaml`（地点归一化）、`language_rules.yaml`（语言规则）；提示词模板在 `config/prompts/*.j2`。每个 YAML 声明自身 `version`；Python 源文件**不写权重与规则常量**（`score.py` 等保持纯函数，配置经注入）。
2. **每次分析固化快照**（API 入队时写入 `analyses` 行）：`pipeline_version`、`extraction_schema_version`、`prompt_version`、`ruleset_version`、`scoring_version`、`llm_model`、`embedding_model`，以及 **`config_snapshot`（解析后的规则/权重/词表全量 JSON）**。
3. 版本来源约定、变更规则与**离线重放流程**见 [docs/reproducibility.md](./reproducibility.md)：重放只使用 `config_snapshot` 与存储的 profiles/chunks，不依赖当前代码配置。extraction 结果以**不可变 profile artifact**（`resume_profiles`/`jd_profiles`）形式存储并由 `analyses.resume_profile_id`/`jd_profile_id` 绑定（数据 ownership 与 artifact 版本化见 ADR-023）。
4. Schema 演进：`ResumeProfile/JDProfile` 声明 `__schema_version__`；升级即重新抽取并标注旧数据，禁止静默跨版本复用。

**Consequences:**
- 优点：任何旧分析可还原"当时为什么得这个分"；权重变更可审计可回滚；演示"改一个权重 → 全部旧结果仍可解释"。
- 代价：配置加载/校验/快照与版本哈希需要基建与测试（`test_config_versions.py`）；配置变更要走 git 评审而非直接改生产。

**Alternatives:**
- 权重/规则硬编码常量：否决——不可复现、不可审计。
- 运行时读库表配置：否决——脱离 git 评审、无版本追溯，MVP 阶段 YAML + snapshot 更直接。

---

## ADR-020：Explainable Decision Trace（决策链一记录到底）

**Status:** Accepted（MVP 生效）

**Context:** 报告只给结论无法支撑"evidence-grounded"。需要让每个关键决策（硬条件 verdict、skill match、计分）都可沿链展示：Requirement→Normalization→Rule→Evidence→Decision→Score contribution。

**Decision:**
1. 确定性节点在**产生决策的同时**写 `decision_traces` 行：`decision_type(constraint|skill_match|score_component|other)` + `decision_key` + `chain jsonb`；`hard_constraint_results`/`skill_match_results` 持 `trace_id` 外键。
2. trace 内容逐字段规范化：原始文本+anchor → 归一化值及方法 → `rule_id`+`ruleset_version`+参数 → `evidence_ids`+摘录 → verdict(`basis`) → 权重/得分/对总分贡献。
3. 不变式（测试强制）：每个约束与 skill match 必有 trace；trace 内 evidence 全部存在；分项贡献之和 = 总分（容差内）。
4. API `GET /analyses/{id}/trace` 与前端 `/analyses/[id]/trace` 渲染完整链与原文高亮。结构与示例见 [docs/decision-trace.md](./decision-trace.md)。
5. **Decision Trace 只承载 deterministic / auditable 决策**：LLM critique 的"建议"是定性分析，**不强行写入 deterministic decision trace**；它必须通过 `evidence_ids` 做独立 citation validation（引用命中证据池，ADR-007），并在 `critiques` 表单独留档（含 `citations_validated` 标志）——可审计，但不属于 trace 体系。
6. **Trace 内文本 PII 策略**：trace 不存敏感原文；evidence 环存 **trace-safe excerpt**——由原始 chunk 的 anchor 窗口经 PII 脱敏器（与日志同一实现）确定性生成的脱敏摘录，携带 `source_chunk_id` + chunk 内偏移 + `excerpt_sha256`，可用 chunk 全文重新生成校验（excerpt ⊆ chunk，modulo 脱敏）。**Trace API 默认输出即 PII-safe**；查看脱敏前原文走受审计的"揭示"动作（ADR-013）。详见 [docs/decision-trace.md](./decision-trace.md)。

**Consequences:**
- 优点：可解释性从"报告有引用"提升到"每个判定可回放"；便于审计、调试规则回归与面试演示。
- 代价：规则引擎与计分函数需要额外产出 trace（输出结构变宽）；trace 校验测试成本前置。

**Alternatives:**
- 报告里拼文本式"解释"：否决——非结构化、不可校验、易与真实推理脱节。
- 只在 UI 层事后生成解释：否决——解释必须与决策同源产出，否则可能造假。

---

## ADR-021：MVP 安全能力范围如实声明（不声称未实现的能力）

**Status:** Accepted（MVP 生效）

**Context:** v0.1 文档出现"病毒/zip-bomb 检查"等表述，但 MVP 并未集成任何 antivirus engine——安全声明失实比没有该能力更危险（面试与生产同理）。

**Decision:**
1. MVP **已实现**的安全能力清单固定为：magic bytes 嗅探、文件类型白名单、上传大小上限、解压比上限、parser 超时、malformed document 处理、PII-safe logging、prompt injection defense。文档（architecture.md §8）只按此清单陈述。
2. **不声称存在病毒扫描/恶意内容深度扫描**；该项与 OCR、URL ingestion 一并列入 "Future Enhancements"（§8.11），真正接入 engine 后才更新声明与文档。
3. 去重/幂等/并发/事务等属于正确性设计而非安全能力吹嘘，仍按 §8.8–8.9 落实。

**Consequences:**
- 优点：安全声明可被实现与测试逐条印证；避免"看起来生产级"的虚假承诺（与"不加非必要依赖"红线一致）。
- 代价：安全章节措辞需随实现范围维护；评审与 README 不得越界宣传。

**Alternatives:**
- 引入开源 clamav 包装以"证明病毒扫描"：否决——增加依赖与运维面却仍不能保证检测有效性，属为展示而堆砌（违反核心工程原则 11）。
- 继续模糊表述：否决——失实声明。

---

## ADR-022：Evaluation Threshold 语义化（target / release gate / measured / baseline）

**Status:** Accepted（MVP 生效）

**Context:** 文档若把 `false UNKNOWN ≤ 20%` 这类数值写成普适门槛，会误导读者把它当作已达成事实；指标实际依赖具体 golden 集的 baseline，且只有实测值才代表真实表现。

**Decision:**
1. 所有评估数值严格区分四类语义（architecture.md §9.3）：
   - **dataset baseline**：当前 golden 集的先验（gold=UNKNOWN 占比、可判定率、字段分布），随数据集变化；数值脱离 baseline 无效。
   - **default target**：设计起点约定（如 `false UNKNOWN ≤ 20%`），**不是普适真理**；CI 不因 target 未达而失败。
   - **release gate**：指标被真实实现并在当前 golden 集上实测达标后，才在 CI 配置中显式升级为硬门槛。
   - **measured result**：`make evaluate` 的真实输出；**只有 measured 值可写入 README/对外材料**。
2. 结构性断言（引用校验、trace 完整性、计分确定性、retry budget、deterministic over-confidence=0 的规则语义）不依赖数据集即可成立，可长期在 CI 执行；数据集相关指标（F1、abstention 系列、false UNKNOWN、coverage）必须先报 baseline + measured，再谈 target/gate。
3. golden 集必须包含足够 gold=UNKNOWN 样本，否则 abstention 指标不可度量（此为前提条件而非假设）。
4. 对外材料（README、演示）出现任何指标数字时，必须同时给出：指标名、measured 值、所用 dataset/baseline、评估日期；禁止把 target 表述为已达成。

**Consequences:**
- 优点：指标诚实可信；CI 区分"结构性红线"与"数据集相关目标"，避免把设计目标当成绩；README 规则可被 code review 检查。
- 代价：每次发布需跑 `make evaluate` 并记录 baseline 快照（版本快照见 ADR-019）；指标口径维护成本上升。

**Alternatives:**
- 保留"指标红线"单一说法并固定数值：否决——脱离 baseline、冒充普适真理，违背"要求真实可验证"原则。

---

## ADR-023：Document Data Ownership & Immutable Versioned Artifacts

**Status:** Accepted（MVP 生效）

**Context:** 早期设计把 `document_chunks` / `resume_profiles` / `jd_profiles` 描述为 analysis-scoped child，并允许以 document_id 唯一——这会诱导"重抽取/重解析时 overwrite 旧行"或"reclaim 覆盖他人 artifact"，破坏共享复用与历史 analysis 的 reproducibility。

**Decision:**
1. **两级 ownership（详见 architecture.md §5.1）**：
   - **Document-scoped immutable artifacts**：`documents`、parse artifact（`parsed_documents` + `document_chunks`）、extraction artifact（`resume_profiles` / `jd_profiles` 及 profile 实体子行 `resume_education` / `resume_experiences` / `resume_skills` / `jd_requirements`）；
   - **Analysis-scoped artifacts**：`hard_constraint_results` / `skill_match_results` / `decision_traces` / `score_snapshots` / `critiques` / `reports` / `reviews` / `job_runs` + `analyses` 行上的 analysis 属性。
2. **parse artifact 版本化**：`parsed_documents` 以 `UNIQUE(document_id, parser_version)` 唯一（`parser_version` = parser 代码/库版本 + 解析参数）；chunks 以 `UNIQUE(parsed_document_id, chunk_index)` 唯一。同 document 被多个 analysis 复用；只按需创建（`ON CONFLICT DO NOTHING` → 选中已有行），**不可变、不 UPDATE/overwrite/删除在用的版本**——analysis reclaim 不会覆盖另一个 analysis 正在使用的 document artifact。
3. **extraction artifact 版本化（immutable versioned artifact model）**：`resume_profiles` / `jd_profiles` 不再以 document_id 唯一，改为五元组 `UNIQUE(document_id, pipeline_version, extraction_schema_version, prompt_version, llm_model)`（若未来确定性抽取依赖 skills/规则词表，需把相关配置哈希纳入指纹并修订本 ADR）。同一 document 可同时存在多个 extraction 版本；re-extract = 新行；**禁止 overwrite**。
4. **analyses 显式绑定**：`analyses.resume_profile_id` / `jd_profile_id`（nullable，extract 后以 guarded UPDATE/fencing 写入）记录本次实际使用的 artifact。旧 analysis 永远绑定旧 artifact → 版本演进不破坏旧结果的可复现性（ADR-019/reproducibility.md）。
5. **写访问语义差异**：document artifacts 不依赖 claim fencing（不可变 + 唯一键幂等，stale worker 的重复创建无害）；analysis-scoped 表与 analyses 绑定列依赖 fencing 四条件（ADR-017 第 5 条）。
6. **删除策略（ownership-safe deletion）**：删除 analysis **只删除其 analysis-scoped 行**，绝不 cascade 共享的 document-scoped artifacts；documents / artifacts 采用软删除 + 保留期 purge，被任一 analyses（或其约束/证据/trace/报告引用链）引用的 artifact 禁止硬删（§8.5/§5.1）。

**Consequences:**
- 优点：跨 analysis 安全共享解析/抽取产物；reclaim/re-run 不破坏他人数据；旧分析永久可重放；可安全演示"同文档多版本并存"。
- 代价：schema 与仓储需贯彻 immutable+唯一键+绑定模型；re-extract/升级需要"先建新 artifact → 绑定 → 再切换"流程；批量排名查询需经 `analyses.jd_profile_id` JOIN `jd_requirements`。

**Alternatives:**
- 保留"document 唯一 profile + 覆盖更新"：否决——破坏历史分析与共享安全。
- 把 chunks/profiles 当 analysis 私有并随 analysis 复制：否决——浪费且引入跨 analysis 一致性问题。

---

## ADR-024：Parser / Chunker 版本化与 parse artifact 身份

**Status:** Accepted（Phase 2 已实现）

**Context:** parse artifact（`parsed_documents` + `document_chunks`）会被多个 analysis 复用（ADR-023）。若 parser 代码、第三方解析库版本或 chunk 参数发生变化而 artifact 身份不变，下游证据定位与抽取输入就会在无人察觉的情况下改变——同一 analysis 重跑可能得到不同结果，破坏可复现性（ADR-019 / reproducibility.md）。

**Decision:**
1. `parser_version` 由 `backend/src/jobfit/parsing/service.py` 的 `parse_version()` **单点**生成，覆盖四类变化源：parser 代码版本（`PARSER_CODE_VERSION`，当前 `p2.0.0`）、chunker 版本与切分参数（`CHUNKER_VERSION` / `CHUNK_SIZE` / `CHUNK_OVERLAP`，当前 `c1.0.0` / `1200` / `200`）、第三方解析库版本（pypdf、python-docx）。
2. `parsed_documents` 以 `UNIQUE(document_id, parser_version)` 唯一；`document_chunks` 以 `UNIQUE(parsed_document_id, chunk_index)` 唯一。任一项变化 ⇒ **新 parse artifact（新行）**；旧 artifact 及其上绑定的 analysis 保持不变，禁止 UPDATE / overwrite。
3. 解析前**重新嗅探**（magic bytes）而非信任 `documents.kind`；不支持或不可解析 → `ParseFailure`（HTTP 422），绝不静默返回空文本（ADR-011）。PDF 无可提取文本层同样失败（扫描件不在 MVP）。
4. `parsed_documents.content_sha256`、`document_chunks.span_sha256` 记录文本指纹，篡改或半写可被校验发现（reproducibility.md §6）。

**Consequences:**
- 优点：解析行为的任何变化都可归因到明确版本；跨 analysis 共享安全；可演示"同文档多 parse 版本并存"。
- 代价：升级解析库或调整 chunk 参数会产生全新 artifact 与 chunk 行（存储增长）；且需在发布时显式 bump `PIPELINE_VERSION` 才会触发重新抽取（形成新 profile artifact，见 ADR-026 第 3 条）。

**Alternatives:**
- 以 `document_id` 唯一 + 覆盖更新：否决——破坏历史分析与共享安全（ADR-023）。
- 只用 parser 代码版本、忽略库版本与 chunk 参数：否决——升级 pypdf 后 chunk 结构可能改变而版本不变，属静默漂移。

---

## ADR-025：确定性滑窗 Chunking

**Status:** Accepted（Phase 2 已实现）

**Context:** chunk 是证据定位（ADR-007）与抽取 prompt 的基本单位。切分必须**确定性**（同输入同输出）且**可定位**（能给出字符区间），否则 grounding 与可复现性无法成立。

**Decision:**
1. `backend/src/jobfit/parsing/chunker.py` 采用固定窗口滑窗：`size=1200` 字符、`overlap=200` 字符、`step=size-overlap`；纯函数，无随机数、无时间依赖、无新增外部依赖。
2. 每个 chunk 记录 `chunk_index`（连续）、`content`、`char_start` / `char_end`（相对 parse artifact 全文的字符区间）、`page`（由 `PageSpan` 推导，可为 null）与 `span_sha256`。
3. 窗口内容**全为空白**时跳过该窗口，但 `chunk_index` 保持连续（索引即顺序，不因跳过而错位）。
4. chunk 参数是 parse artifact 身份的一部分（ADR-024 第 1 条）：改参数 = 新 parse artifact。
5. **不做** token 级切分、语义切分或段落重组：本阶段不引入 tokenizer / embedding 依赖，避免为非必要能力增加依赖面并引入不确定性。

**Consequences:**
- 优点：切分结果可逐位复现；`char_start/char_end` 使证据可回溯到原文区间；overlap 降低语义被窗口边界截断的概率。
- 代价：固定字符窗口与 token 预算不完全对齐（超长内容由抽取层的 `PROMPT_CHAR_BUDGET` 做确定性截断）；窗口边界仍可能切断句子（由 overlap 缓解）。

**Alternatives:**
- 按段落 / 标题切分：否决——长度方差大，易产生超预算 chunk，且"段落"判定依赖启发式，确定性弱。
- 按 token 切分：否决——需引入 tokenizer 依赖，且 token 边界与字符区间定位不一致。

---

## ADR-026：Extraction Artifact 身份与确定性 Fingerprint

**Status:** Accepted（Phase 2 已实现）

**Context:** 抽取产物（`resume_profiles` / `jd_profiles`）不可变（ADR-023），因此必须能**确定性地判定"是否已存在等价产物"**：既要不重复调用 LLM（成本与延迟），又不能在中途版本演进时错误复用旧结果。这需要一个不依赖随机性、可从输入完全重算的标识。

**Decision:**
1. **复用身份 = 五元组** `(document_id, pipeline_version, extraction_schema_version, prompt_version, llm_model)`，与 DB 唯一键 `uq_resume_profile_fingerprint` / `uq_jd_profile_fingerprint` 一一对应；命中即复用（不调用 LLM、不消耗 attempt），并向已有 artifact 补齐缺失子行（幂等修复历史中断）。
2. **fingerprint = SHA-256(canonical_json(payload))**：payload 为上述五元组 + `parsed_document_id` + `artifact` 区分键（`resume_profile` / `jd_profile`），见 `backend/src/jobfit/extraction/fingerprints.py`。`canonical_json` 固定 `sort_keys=True`、紧凑分隔符、`ensure_ascii=False`、`default=str`，保证同输入同哈希。fingerprint 写入 `full_dump["_meta"]` 随 artifact 永久留存，**绝不以随机 UUID / 时间戳充当身份**（UUID 仅作主键）。
3. **两者差异（如实记录）**：fingerprint 含 `parsed_document_id`，是复用身份的**审计超集**，用于诊断"该产物是否由不同 parse 版本产出"。当解析版本变化但五元组不变时，DB 唯一键会判定为已存在并**复用旧 artifact**。因此**升级 parser 或解析库必须同时 bump `PIPELINE_VERSION`**（部署时注入，ADR-019），否则不会触发重新抽取。这是显式约定，不是隐式行为。
4. `full_dump["_meta"]` 同时记录 `prompt_version`、`parser_version`、`schema_version`、`llm_model`、`attempt_no`，使单个 artifact 无需 JOIN 即可自证来源；读取时以 `_domain_dump()` 剥离 `_meta` 后再做领域 schema 校验（避免 `extra="forbid"` 误报）。

**Consequences:**
- 优点：复用判定与审计标识分离且各自确定性；artifact 自描述来源；"这次为什么没有调用模型"可精确回答（同一 fingerprint 命中）。
- 代价：两个标识并存需要文档解释（即本条）；解析版本升级若忘记 bump `pipeline_version` 会静默复用旧产物（目前以约定 + 文档约束，无运行时强制）。

**Alternatives:**
- 把 `parsed_document_id` 纳入 DB 唯一键：否决——需改动 Phase 1 baseline schema；当前由 `pipeline_version` 承担"整条流水线版本"职责（ADR-019），语义已足够。
- 用随机 UUID / 时间戳做身份：否决——不可复算，无法作为幂等键。

---

## ADR-027：Prompt 版本化（内容哈希，前缀 `h:`）

**Status:** Accepted（Phase 2 已实现）

**Context:** prompt 变化会改变抽取结果。`prompt_version` 若靠人工维护，极易出现"改了没升版本"的静默漂移（reproducibility.md §2 明确要求防此类漂移）。

**Decision:**
1. `backend/src/jobfit/config/prompts.py` 的 `PromptRegistry` 从 `backend/config/prompts/` 读取模板；`prompt_version = "h:" + sha256(文件名字节 ‖ 文件内容)[:16]`，**由内容决定，不手工维护**。
2. `combined_version()` 对两个模板（`extract_resume.j2`、`extract_jd.j2`）的版本串再取 SHA-256，作为本次抽取的 `prompt_version` 写入 artifact 五元组——任一模板变化 ⇒ 版本变化 ⇒ 新 artifact（ADR-026）。
3. 加载时校验模板必须含 `{{chunks}}` 占位符，缺失即 `ConfigurationError`（快速失败，而非运行期产出垃圾 prompt）。
4. 占位符替换使用**纯字符串替换**（`{{key}}`），**不引入 Jinja2 等模板引擎**：避免非必要依赖，并消除"文档内容被当模板求值"的注入面（architecture.md §8.1）。`.j2` 仅为命名约定。

**Consequences:**
- 优点：prompt 变更自动产生新版本与新 artifact，历史 prompt 组合可追溯；prompt 文本不硬编码在代码里。
- 代价：模板中的注释 / 空白变化也会改变版本（比语义哈希更敏感）；由 git 评审控制，属保守可接受策略。

**Alternatives:**
- 手工维护 `version:` 字段：否决——可能出现"改了没升版本"的静默漂移。
- 引入 Jinja2：否决——非必要依赖 + 模板注入面；本阶段只需字符串替换。

---

## ADR-028：幂等 Artifact 写入（唯一键 + `ON CONFLICT DO NOTHING`，禁止 SELECT-then-INSERT）

**Status:** Accepted（Phase 2 已实现）

**Context:** 同一 document 可能被多个 analysis（甚至并发）同时解析 / 抽取。若使用"先 SELECT 判存在、再 INSERT"的写法，两个 writer 会在检查与插入之间交错（TOCTOU），导致唯一键冲突异常或重复行（architecture.md §8.8 / §8.9）。

**Decision:**
1. 所有不可变 artifact 写入采用**单条 `INSERT ... ON CONFLICT DO NOTHING RETURNING id`**（`backend/src/jobfit/db/repositories/artifacts.py`）：返回 id ⇒ 本次创建；返回空 ⇒ 已被并发方创建，随即**选中已有行复用**。全程不做 SELECT-then-INSERT 的存在性预判。
2. `document_chunks` 为多值插入，`ON CONFLICT DO NOTHING` 的 rowcount 不可靠，故以**插入前后 `count_chunks` 差值**计算实际新建数（`chunks_created`），其余按 `(parsed_document_id, chunk_index)` 复用。
3. artifact 写入**不依赖 claim fencing**（不可变 + 唯一键天然幂等，stale worker 的重复创建无害）；只有 `analyses` 的绑定列与 analysis-scoped 表适用 fencing 四条件（ADR-017 第 5 条 / ADR-023 第 5 条）。
4. 并发正确性由**数据库唯一约束**保证，且测试直接以"绕过应用层手工重复插入必须抛 `IntegrityError`"的方式验证约束真实存在（而非只验证应用逻辑）。

**Consequences:**
- 优点：并发 / 重试下不重复、不覆盖、不抛异常；stale worker 无法破坏数据；复用路径明确（命中即 0 次 LLM 调用）。
- 代价：要求实现者理解"`ON CONFLICT` 空返回 = 已被他人创建"；chunks 新建计数需绕开不可靠的 rowcount。

**Alternatives:**
- SELECT-then-INSERT（含 `session.merge`）：否决——TOCTOU 竞态。
- 应用层加锁 / 串行化：否决——引入协调成本，DB 唯一键已足够。

---

## ADR-029：测试 Provider 策略（确定性 fake 用于编排测试 + 真实 PG 集成测试）

**Status:** Accepted（Phase 2 已实现）

**Context:** Phase 2 必须验证"reservation 先于 provider 调用""幂等复用不调用 LLM""grounding 可回溯"等**编排语义**，同时不得为了"测试通过"而弱化真实依赖（无 mock DB、无假 migration）。用真实 LLM 做测试既慢又不确定，且需要密钥。

**Decision:**
1. 编排测试使用 `backend/tests/support.py` 的 `DeterministicProvider`：实现完整 `LLMProvider` Protocol，按请求的 schema 返回固定 payload，并**记录每次调用**（`calls`），使"是否调用、调用几次"成为可断言事实——而非用 `unittest.mock` 打桩（mock 会让"是否真的没调用"变得不可靠）。
2. 集成测试连**真实 PostgreSQL + pgvector**；schema 由 `Base.metadata.create_all` 建立（生产仍走 Alembic）；数据库不可达时 `pytest.skip`，**不伪装通过**；每个 db 测试前 `TRUNCATE ... RESTART IDENTITY CASCADE` 隔离状态。
3. 生产代码路径不含任何 fake / 分支开关：`DeterministicProvider` 只存在于 `tests/`，经 `api/deps.py` 的 FastAPI 依赖覆盖注入。
4. 迁移正确性单独以 `alembic upgrade head → downgrade base → upgrade head` round-trip + `alembic check`（无差异）验证，不依赖测试套件。
5. 判定以 `pytest --junitxml` 的 XML 结果与**退出码**为权威（PowerShell 5.1 会丢失 native 输出的 pytest 摘要，仅凭控制台文本会误判）。

**Consequences:**
- 优点：编排语义可精确断言且测试快速稳定；真实依赖（PG / pgvector / migration）不被 mock 掩盖；"跳过"与"通过"不可能混淆。
- 代价：集成测试需要本地 PG（否则 skip，需在报告中如实标注）；需维护确定性 payload fixture。

**Alternatives:**
- 全量 mock provider + mock DB（SQLite / 内存）：否决——掩盖真实约束（唯一键、`clock_timestamp()`、pgvector），使幂等 / 并发断言失去意义。
- 用真实 DeepSeek 做集成测试：否决——不确定、需密钥，且无法断言"未调用"。

---

## ADR-030：证据检索策略（确定性 anchor gate + pgvector 排序）

**Status:** Accepted（Phase 3 已实现）

**Context:** 检索要为"硬条件/技能匹配"提供 supporting evidence。直觉做法是"取 top-k 相似 chunk"，但在**真实测量**后发现不可用：本仓库中文 fixture 上，相关查询的最高余弦相似度 0.20，不相关查询也能到 0.16；lexical 更差（不相关 0.70 > 相关 0.55，因中文单字/常用词重合）。若直接采用，**每个** requirement 都会"找到证据"，`absence → UNKNOWN` 的语义（ADR-008）会整体失效。

**Decision:**
1. **精度来自确定性 anchor gate，而不是相似度阈值**：检索命中必须**字面包含** requirement 的关键词（技能名等 distinctive term），由 `passes_anchor_gate()` 做形式归一后的子串判定；未设置 anchor 时不做 gate（调用方自担精度，如通用检索 API）。
2. **pgvector 只负责排序**：anchor 通过的候选按 `embedding <=> qvec` 余弦距离排序（`ORDER BY distance ASC, chunk_index ASC, chunk_id ASC`），保证确定性 tie-break。
3. **无 embedding 时退化 lexical**（ADR-006）：`method=auto` 仅在 scope 内 chunk **全部**具备同一 `embedding_model` 时走向量路径，否则用词面打分；两者都不会跨模型混用（architecture §7）。
4. **检索参数版本化**：`config/retrieval.yaml`（min_similarity / min_score / max_top_k / default_top_k）纳入 `RULES_FILES`，随 `config_snapshot` 快照——因为检索会影响 evidence 选择进而影响计分。
5. **embedding 实现**：MVP 用 `hash-ngram-v1`（feature hashing over 词/CJK 二元组，1024 维，L2 归一化，纯函数、无网络、无新增依赖）。它是**词面**表示，文档中不得声称具备语义理解能力；ADR-006 的本地 `bge-m3` 属 V1，在同一 Protocol 下替换（只改配置）。
6. **检索只服务技能类 requirement**：学历/年限/地点/语言/证书由结构化事实判定，不依赖检索——这正是 absence 不产生 FALSE 的前提。

**Consequences:**
- 优点：UNKNOWN 语义真实成立；检索结果确定性可复现；无需外部凭证/重型依赖即可运行；换模型只改配置。
- 代价：召回以字面重合为前提（未提及即无证据），对同义改写无召回——这是**有意的精度取舍**；要提升需引入真正的语义模型（V1）。

**Alternatives:**
- 纯 top-k 向量检索 + 阈值：否决——实测无法分离相关/不相关，会让 UNKNOWN 语义失效。
- 纯 lexical 检索：否决——中文常用词重合导致更高误召。
- 引入 sentence-transformers/torch：否决——重量级依赖且与"不堆砌依赖、无凭证可运行"约束冲突（记为 V1）。

---

## ADR-031：硬条件判定的权威性约定（何时允许 FALSE）

**Status:** Accepted（Phase 3 已实现）

**Context:** 三值语义（ADR-008）只有在明确"哪些事实足以否定"时才能落地；否则实现者容易把"没写"当成"不满足"。

**Decision:** 只有**权威事实被明确否定**才产生 `NOT_MET`，判据集中声明在 `matching/facts.py`：
1. `degree_level`：简历列出的最高学历是权威标量事实（人不会隐瞒更高学历）；已知等级不满足要求 => NOT_MET。
2. `experience_years`：由 dated 经历的**区间并集**（重叠不重复计入）得到年限下界；**仅当每段经历都有起止日期**时才允许据此判 NOT_MET，任一段缺日期或没有经历 => UNKNOWN。不使用"当前时间"（保证确定性）。
3. `location`：两侧都必须能归一到 `location_rules.yaml` 已声明的 canonical 城市才能比较；否则 UNKNOWN（绝不推断地点 OK/Not OK）。
4. `language`：等级必须已在 `language_rules.yaml` 声明；不做 CEFR/TOEFL 换算猜测。
5. `skills` / `certifications`：**不是穷尽列表**。缺失只能是 UNKNOWN，永不产生 NOT_MET/NO_MATCH。
6. 未建模的 requirement 类型（如工作签证）、或 `unsupported` 清单内类型 => UNKNOWN + `UNSUPPORTED_REQUIREMENT_TYPE`，并记录 architecture gap（绝不交给 LLM 猜）。
7. 算子解析也走显式配置：`constraint_rules.yaml` 为每个 req_type 声明 `default`/`allowed`；不属于 allowed（含无法解析）=> UNKNOWN + `UNSPECIFIED_OPERATOR`。

**Consequences:**
- 优点：每个 verdict 都能给出确定性 reason code 与依据；"缺失 ≠ 否定"在类型层面被实现约束住；可测试（每个 category 都有 TRUE/FALSE/UNKNOWN 三路用例）。
- 代价：部分现实 JD 会落到 UNKNOWN（例如未声明的签证要求），需要人工或后续阶段补模型。

**Alternatives:**
- 把简历字段一律当穷尽列表：否决——与 ADR-008 冲突，会把"没写"变成"不具备"。
- 用 LLM 兜底未建模类别：否决——§9 明确禁止 LLM 参与硬条件判定。

---

## ADR-032：确定性技能匹配（absence 不产生 NO_MATCH）

**Status:** Accepted（Phase 3 已实现）

**Context:** 技能匹配最容易出现两种错误：把 absence 当否定；或用模糊/语义相似度把 Python 匹配到 PyTorch。

**Decision:**
1. 归一化只做**形式归一 + 显式别名表**（`skills.yaml`）：Python/python3/Python 3 → canonical `python`；**未声明的近义关系一律不成立**（Python ↛ PyTorch）。
2. 证据优先级（§21）决定结论：structured（结构化技能 + grounded evidence）> source chunk > retrieved（检索命中）> absence（**不是** positive evidence）。
   - 结构化命中且有 grounding => `matched`（claimed_only => `claimed_only`，按 `claimed_only_penalty` 折扣）；
   - 结构化命中但无 grounding，或只在检索中命中 => `partial`；
   - 两者都没有 => `unknown`（reason `NO_EVIDENCE_UNKNOWN`）。
3. `SkillMatchStatus.MISSING`（NO_MATCH）**保留但当前不可由 absence 产生**：只有当存在显式否定证据时才允许写入，而现有 resume schema 无法表达"候选人没有某技能"的正面否定事实 —— 这是有意的 strictness，已在枚举 docstring 中说明。
4. 多行命中时按 `(claimed_only, skill_raw, id)` 确定性择一。

**Consequences:**
- 优点：不会出现"没写 = 不会"的误判；不会出现未批准的语义误配；结论可解释（reason code + evidence 路径）。
- 代价：技能覆盖率高的简历才能得到 `matched`；大量条目落 `partial`/`unknown`。

**Alternatives:**
- 语义相似度/embedding 近邻匹配：否决——不可解释且易误配（违反 §18/§19）。
- 把 absence 记为 NO_MATCH：否决——违反 ADR-008。

---

## ADR-033：Decision Trace 实现（metadata-only、可重生成、收敛）

**Status:** Accepted（Phase 3 已实现）

**Context:** decision-trace.md 设计稿要求 trace 保存 PII-safe excerpt，但本项目的脱敏器只保证去除电话/邮箱/URL/长密钥，**无法可靠识别人名**；把简历原文（即使脱敏）写入 trace 仍有 PII 泄漏风险。

**Decision:**
1. 采用设计稿 §6.3 允许的**更严格选项**：evidence 环只保存 `source_chunk_id` + 文档级字符区间 + `span_sha256` + `tier`，**不保存任何简历文本**；JD 侧只保存单条 requirement 的 `source_text`（业务文本）与 anchor。凭证：`test_trace.py` 断言电话/邮箱/姓名/简历原文片段/全文均不出现在 trace JSON 中。
2. 覆盖性：每条 `hard_constraint_results` 与 `skill_match_results` 都链接到 trace（`trace_id` 非空）；每个计分 section 各有一条 `score_component` trace。
3. 可追溯：链内含 `input_artifacts`（resume/jd profile id）、`requirement`（req_type/operator/value/anchors）、`normalization`、`rule`（rule_id + ruleset_version + params）、`evidence`、`decision`（result/basis/reason_code/explanation）、`score_contribution`、`versions`。
4. **可重生成**：链内容只由输入 artifact + ruleset_version + 结果决定，无时间/随机；测试断言两次独立 analysis 的链（去掉 analysis-local 字段后）**逐字段一致**。
5. **收敛**：`UNIQUE(analysis_id, decision_type, decision_key)` + `ON CONFLICT DO NOTHING`，重放不重复累积。
6. Trace 只承载 deterministic 决策；LLM critique 仍独立留档（ADR-020）。

**Consequences:**
- 优点：trace 天然 PII-safe（不含文本）；可回放、可对比、可作为审计证据。
- 代价：无法从 trace 直接读到原文摘录，需要按 `source_chunk_id + span` 二次读取（受审计的"揭示"动作，后续阶段）。

**Alternatives:**
- 存脱敏 excerpt：否决——人名无法可靠脱敏，风险不可控。
- 只在 API 层拼装解释：否决——解释必须与决策同源产出（ADR-020）。

---

## ADR-034：Analysis 身份 / 幂等 与生命周期终态

**Status:** Accepted（Phase 3 已实现）

**Context:** 需要回答"这次点击是复用已有分析还是新建"，并保证重跑/并发不产生重复或错配的结果。

**Decision:**
1. **显式绑定优先**：`POST /analyses` 可传 `resume_profile_id` / `jd_profile_id`（校验归属 document）；未传则保持 NULL，由执行期 extract 节点绑定并写回。执行期**只读取 `analyses` 上绑定的 artifact**，绑定缺失即 `ValidationFailed`（绝不"运行时猜最新 profile"）。
2. **analysis identity**：未显式给 `idempotency_key` 时按 `(resume_profile_id|resume_document_id, jd_profile_id|jd_document_id, pipeline_version, extraction_schema_version, prompt_version, ruleset_version, scoring_version, embedding_model)` 计算 `an:<sha256>`；命中已存在行则**复用并返回 200（`reused=true`）**；并发冲突由 `UNIQUE(idempotency_key)` 裁决（回滚后复用，无 TOCTOU）。
3. **结果 identity**：`hard_constraint_results` / `skill_match_results` 以 `(analysis_id, requirement_id, ruleset_version)` 幂等；`decision_traces` 以 `(analysis_id, decision_type, decision_key)`；`score_snapshots` 以 `(analysis_id, kind)`。写入前先做 fencing 断言（四条件 + `FOR UPDATE`），0 行即抛 `LeaseLost` 且不落任何结果。
4. **生命周期终态**：Phase 3 引入 `succeeded`（确定性引擎完成）。`awaiting_review` / `finalized` / `rejected` 仍属 HITL（ADR-009，Phase 4+）。重跑需显式 `POST /analyses/{id}/run {"requeue": true}`（`failed|succeeded → queued`），结果写入为幂等 upsert，因此**重跑不产生重复行**。

**Consequences:**
- 优点：同一身份重复点击不制造垃圾行；绑定可审计；并发/重跑/崩溃重试都收敛到同一结果集。
- 代价：`requeue` 是显式动作（不会自动重试成功过的分析）；identity 含版本维度，改配置后会产生新的 analysis 身份（符合 ADR-019 预期）。

**Alternatives:**
- 每次 POST 都新建 analysis：否决——产生无意义行与重复计算。
- 运行时按"最新 profile"自动挑选：否决——不可审计，且会让历史结果漂移。

---

## ADR-035：确定性计分规则与 UNKNOWN 策略（显式配置）

**Status:** Accepted（Phase 3 已实现）

**Context:** 需要给出可解释的总分，同时不能让"没写"被当作"不合格"（ADR-008），也不能在代码里偷偷发明业务权重。

**Decision:**（全部来自 `scoring.yaml`，`version` 参与 `scoring_version` 快照）
1. **分项权重**（skills .35 / experience .25 / education .15 / location .15 / language .10）与 `scale=100`；单项分值映射 `credits{matched:1.0, partial:0.5, missing:0.0}`，`claimed_only` 用 `flags.claimed_only_penalty`。
2. **硬条件门禁（veto）不参与加权**，单独输出 `gate ∈ {pass, blocked, unknown, not_applicable}`（`blocked` 当任一 hard 为 NOT_MET；`unknown` 当存在 UNKNOWN 且策略未要求阻断）。
3. **UNKNOWN 策略显式声明**：`unknown_policy.mode = exclude_and_renormalize` —— UNKNOWN 项不进分母；某 section 无可判定项时记为 `not_applicable` 并从加权总和中剔除，剩余 section 权重重新归一化（"没写"既不扣分也不加分）。`gate_unknown_blocks_recommendation=false` 明确 UNKNOWN 不阻断推荐。
4. 总分 = `scale * Σ(applied_weight × section_score) / Σ(applied_weight)`（无可用段时为 0 并记 `NO_DETERMINABLE_SECTIONS`）。
5. 门禁结论以 `GATE:<value>` 写入 `score_snapshots.flags`，使读侧可无损还原（表无 gate 列）。
6. 代码只实现配置声明的 mode；遇到未实现的 mode 直接抛错，不做静默降级。

**Consequences:**
- 优点：评分确定性、可复现、可解释；UNKNOWN 的行为由配置而非代码决定；权重变更走 git 评审并自动改变 `scoring_version`。
- 代价：`exclude_and_renormalize` 会让"信息更少"的简历在分项上不被扣分（有意为之，需在报告中同时呈现 gate 与 flags 才能正确解读）。

**Alternatives:**
- UNKNOWN 记 0 分：否决——等价于把缺失当不合格。
- 代码内固定 UNKNOWN 处理：否决——违反"由显式 ruleset 决定"（§27）。

---

## ADR-036：LLM Critique 契约、幂等与 Citation Validation

**Status:** Accepted（Phase 4 已实现）

**Context:** 需要让 LLM 产出有价值的定性 critique，同时保证它**不污染**确定性结论、可复现、可审计、可离线回归。

**Decision:**
1. critique 输出强制经 `CritiqueSchema`（Pydantic v2，`extra=forbid`）校验后才进入 domain layer；`SUPPORTED` claim 必须携带至少一个 citation（ADR-002/ADR-007）。
2. `critiques` 按 `(analysis_id, fingerprint)` 幂等（`ON CONFLICT DO NOTHING`）；`fingerprint = f(analysis_id, prompt_version, provider, model, config_snapshot)`（canonical JSON + SHA-256，ADR-026/ADR-028 同策略）。同配置重跑复用同一行，**不重复调用 LLM**。
3. `CitationValidator` 在发布前校验：citation 必须命中**当前 analysis** 证据池（绑定 resume profile 的 `parsed_document` 下 chunk）或当前 analysis 的 `decision_traces`；fabricated / 跨 document / 跨 analysis 引用一律 `rejected`；`excerpt_sha256` 必须可验证（§12）。
4. **UNKNOWN 保留**：确定性结果为 UNKNOWN 的 requirement，critique 不得以 `SUPPORTED` 断言确定结论（只能 `uncertain` / `inferential` / `contradicted`，并在 `unknown_acknowledgements` 中 acknowledge）。违反 => `validation_status=rejected` 且不进入报告正文。
5. provider 缺失（`ConfigurationError` / 无凭证）时 critique 落 `status=unavailable`（EXTERNAL CREDENTIAL BLOCKED），**绝不伪造 live 结果**（ADR-021）。
6. 硬条件判定中不出现 LLM（ADR-010 / ADR-031）；validator 只读，从不修改确定性结果表。

**Consequences:**
- 优点：黑盒 LLM 的产物被结构化 + 引用校验双重约束，citations 与 UNKNOWN 边界可单测；无凭证时可降级发布。
- 代价：citation 校验要求 LLM 精确回填 chunk/trace id，prompt 需内嵌受控 `<data>` 块；过严可能导致较多 `rejected`（由人工 review 兜底）。

**Alternatives:**
- 直接落 LLM 文本 / `json.loads` blind persistence：否决——违反 ADR-002，不可校验。
- 引用校验只在报告层做：否决——无法区分"缺失"与"捏造"，且 UNKNOWN 越权会漏检。

---

## ADR-037：Evidence-Grounded Report 装配、校验与不可变发布

**Status:** Accepted（Phase 4 已实现）

**Context:** 最终报告必须"以证据为地基"：既要有确定性结论，又要有人工可读的解释，且不可被 LLM 或重跑篡改。

**Decision:**
1. 报告由**确定性结果装配**（hard constraints / skill matches / score / traces 全部来自 DB）+ **已校验 critique** 作解释层；LLM 不生成整份报告（ADR-001）。
2. 分区固定：`summary / hard_constraints / skills / evidence / unknowns / score / critique / next_actions / review_status`；仅当 critique `citations_validated=True` 时追加 `strengths / gaps / risks / critique_unknowns`（rejected/unavailable 不得进入正文）。
3. `reports.stage` 单向 `draft → validated → final`；`UNIQUE(analysis_id, version)`，每次重新生成 = 新 version 行；`final` 不可再改（`published_at` 落库）。
4. `ReportValidator` 发布前与 DB 交叉校验：constraints/skills/score 逐条一致、UNKNOWN 完整保留、evidence 可解析且属于当前 analysis、critique 派生分区仅来自已校验 critique；失败 => `stage=draft` 留档、**不发布**。
5. `report_fingerprint = f(analysis_id, constraints, skill_matches, score, critique_fingerprint)`；`meta` 记录 builder 版本 / versions 快照 / models（ADR-019）。

**Consequences:**
- 优点：报告正文的任何事实都可回溯到 DB 或已校验 citation；重生成产生新版本而非覆盖，审计完整。
- 代价：分区结构固定，灵活性受限；validator 使报告构建成本略增（但纯读、无 LLM）。

**Alternatives:**
- 让 LLM 直接生成整份报告：否决——无法保证与确定性结论一致（哈希/逐条比对会失败）。
- 报告原地覆盖：否决——破坏可审计性与 final 不可变语义。

---

## ADR-038：HITL Review 生命周期与并发安全

**Status:** Accepted（Phase 4 已实现）

**Context:** 发布闸门要求人工审批（ADR-009）；多个 reviewer 并发操作时不得出现 double finalize 或状态漂移。

**Decision:**
1. 状态机：`awaiting_review --approve--> finalized`、`--reject--> rejected`、`--request_changes--> queued`；`finalized` / `rejected` 为终态，非法转移 => `422`。
2. 转移 = `analyses` 上**单条条件 UPDATE**（`WHERE status=:from_state`）原子裁决；并发下只有一个 reviewer 生效，败者 `409 Conflict`，**无 double finalize / lost update**（HITL 无 lease，用状态 CAS 而非 claim fencing）。
3. `approve` 前置：存在 `validated` 报告且 critique 非 `rejected`；通过则报告 `validated→final`（写 `published_at`）+ analysis `→finalized`。
4. `reject` 必须给出理由（`comments` 非空）；每次 action 落一行 `reviews`（append-only）+ 一条 `audit_log`（谁 / 何时 / `from→to` / 依据 comment）；不存 reviewer 凭据（ADR-013）。
5. **LLM 从不自动 finalized**：approve/reject 只能由 reviewer action 触发。

**Consequences:**
- 优点：并发与重放安全；审计链完整；状态语义与 DB 约束一致（`CHECK` 同时允许 Phase 3/4 终态）。
- 代价：`request_changes` 走"重排为 queued"的重跑语义（旧结果保留审计），而非原地编辑 draft。

**Alternatives:**
- 应用层 `SELECT ... then UPDATE`：否决——并发下有 TOCTOU，会产生 double finalize。
- 悲观锁（`SELECT FOR UPDATE` 长事务）：否决——HITL 动作由外部触发、事务跨度不受控。

---

## ADR-039：Golden Evaluation Set 与 live / deterministic provider 口径

**Status:** Accepted（Phase 4 已实现）

**Context:** 需要可重复的质量回归基线，同时避免把 LLM 的不确定性与外部成本引入默认测试。

**Decision:**
1. `tests/golden/` 使用**完全合成 / 匿名 fixture**（无任何真实 PII），覆盖 §34 的 11 类场景（perfect match / obvious mismatch / unknown / mixed constraints / skill partial match / citation failure / cross-analysis citation / prompt injection / contradictory evidence / review rejection / review approval）。
2. golden 断言**语义不变量**，不比对 LLM 自然语言逐字一致：①确定性契约（gate / verdict / skill status 与预期一致）；②citation 契约（fabricated / cross-analysis / 越权断言一律 `rejected`）；③决策一致性（critique 永不改写确定性结果、UNKNOWN 完整保留、报告分区与 review 终态正确）。
3. live DeepSeek 调用**不进普通 pytest 默认路径**（成本 / 不确定性 / 外部可用性）；默认只用 test-only `DeterministicProvider`（ADR-029）。
4. 无 `DEEPSEEK_API_KEY` 时 Phase 4 的 deterministic 实现与测试照常进行；live 结论单独标注 `EXTERNAL CREDENTIAL BLOCKED`，**绝不把 deterministic PASS 写成 DeepSeek PASS**（ADR-021）。

**Consequences:**
- 优点：基线可离线重复；不产生外部费用；"计划中"与"已落地"边界清晰。
- 代价：未真实调用 live 模型时，无法覆盖模型自身的输出分布漂移（需在有凭证的独立 eval 流程中补测）。

**Alternatives:**
- 把 live 调用放进默认 pytest：否决——成本、不确定性、外部可用性三重风险。
- 只比对 LLM 文本快照：否决——自然语言不稳定，会造成假失败（§34 明确禁止）。

---

## ADR-040：Frontend Architecture（Next.js App Router + 统一 API client，presentation-only）

**Status:** Accepted（Phase 5 已实现）

**Context:** Phase 5 要交付可演示、可截图、可用于面试 live demo 的产品界面，同时**不得**把确定性逻辑复制到前端、不得伪造后端结果。

**Decision:**
1. 技术栈：Next.js（App Router）+ TypeScript，不引入 UI framework / 状态管理库；样式用原生 CSS（`app/globals.css`），避免为视觉引入依赖。
2. **presentation-only**：前端**不计算分数、不重算约束/技能、不生成 narrative**；一切数值与文案来自 backend Pydantic response（score_snapshot、report sections、critique content）。
3. 统一 API client 位于 `frontend/lib/api/`：集中 base URL（`NEXT_PUBLIC_API_BASE_URL`）、错误归一（`code/message/details/request_id`）、HTTP 状态处理（404/409/422/500/503）；页面不得各自 `fetch`。
4. API base URL 一律经环境配置注入，**禁止**散落硬编码 `http://localhost:8000`。
5. PII 红线：前端不渲染 resume/JD 原文；日志与 `console.*` 不得输出 email/phone/整份文档（§41）。
6. 证据展示只用 backend 提供的 PII-safe 定位信息（chunk id / span / hash / 引用关系），不读取原始文档。

**Consequences:**
- 优点：前后端契约单一（OpenAPI），确定性语义不会被前端"二次实现"漂移；demo 与生产同一路径。
- 代价：某些展示需要新增只读 API（已按需增加 list/evidence endpoint），而非在前端拼装。

**Alternatives:**
- 在前端复刻 scoring / constraint 规则：否决——违反"LLM/前端都不是权威"；会产生与后端不一致的结论。
- 引入重型 UI 框架：否决——与"不加非必要依赖"红线冲突（architecture §12）。

---

## ADR-041：API Error Contract 与 Request Correlation

**Status:** Accepted（Phase 5 已实现）

**Context:** 前端需要稳定的机器可读错误语义（区分可重试与不可重试），且要能跨前后端追踪一次请求。

**Decision:**
1. 统一错误响应体：`{"error": {code, message, details, request_id, retryable}}`；**同时保留** `detail` 字段以兼容 Phase 1–4 客户端与回归测试。
2. `code` 为稳定枚举（NOT_FOUND / CONFLICT / LEASE_LOST / VALIDATION_FAILED / REQUEST_VALIDATION_FAILED / PARSE_FAILED / RESERVATION_FAILED / CONFIGURATION_ERROR / INTERNAL_ERROR）。
3. `retryable` 由代码映射（LEASE_LOST / CONFLICT 为 true），前端据此决定"重试"还是"提示用户"。
4. 请求关联：中间件读取/生成 `X-Request-ID`，写入 `request.state` + structlog contextvars + 响应头；错误体回填同一 id（§47）。
5. **绝不**把 Python traceback / 异常类型细节作为 message 返回给浏览器（unhandled => 固定 "internal server error"）。
6. CORS 使用显式 origin 白名单（`CORS_ALLOW_ORIGINS`），生产**禁止** `allow_origins=["*"]`；开发默认仅 localhost:3000。

**Consequences:**
- 优点：前端错误处理可枚举、可单测；一次请求跨日志/响应可追踪；`detail` 兼容使既有测试零改动。
- 代价：错误体短期同时存在 `detail` 与 `error` 两套字段（文档明确 `error` 为正式契约）。

**Alternatives:**
- 直接替换 `detail`：否决——会破坏 Phase 2–4 的既有 API 契约与测试。
- 返回 `str(exc)`：否决——可能泄露内部实现细节。

---

## ADR-042：Deterministic Golden Evaluation Runner

**Status:** Accepted（Phase 5 已实现）

**Context:** 需要可复制的质量回归基线，且**禁止**由 LLM 自评；评测必须能脱离 live credential 运行。

**Decision:**
1. golden 数据**声明式**：`tests/golden/cases/golden_cases.json`（input + 声明的 metrics）、`tests/golden/expected/golden_expected.json`（期望结论）、`fixtures/`（合成文档）；新增 case 不改 driver。
2. 指标受控且由**代码**计算：`hard_constraint_exact_agreement` / `skill_match_agreement` / `unknown_preservation` / `citation_validity` / `report_invariant_validity` / `decision_trace_completeness`（+ `review_lifecycle`）。新增指标需同时更新 case 声明与 `DEFAULT_METRIC_NAMES`，有测试守护。
3. 评测入口 `python -m jobfit.evaluation`：subprocess 运行 golden suite（pytest exit code 权威）→ 解析 JUnit XML → 读取测试写出的指标 sidecar → 渲染 `phase5-evaluation.json` / `phase5-evaluation.md`。
4. `jobfit.evaluation` **不 import tests/**、**不提供 production fake provider**（维持 ADR-029 边界），只做编排与渲染。
5. 不使用 live LLM；未配置凭证时不影响评测运行与结论。

**Consequences:**
- 优点：指标可复算、可审计、无外部依赖；报告可作为 CI artifact。
- 代价：golden case 需要真实 PostgreSQL（无 DB 时 skip，报告会显示 skipped 而非伪通过）。

**Alternatives:**
- 让 LLM 给报告打分：否决——不可复现且违反 §54。
- 只比对 LLM 文本快照：否决——自然语言不稳定，假失败率高（§34）。

---

## ADR-043：E2E Strategy（Playwright + 真实 backend，无外部依赖）

**Status:** Accepted（Phase 5 已实现）

**Context:** 需要证明"浏览器 → HTTP → FastAPI → service → PostgreSQL"整链路真实可用，且不依赖外网与 live credential。

**Decision:**
1. E2E 使用 Playwright，测试位于 `tests/e2e/`；**必须**经 HTTP 打真实 backend，不得 import backend Python 函数、不得用前端 mock 代替后端。
2. 数据：完全合成 fixtures；数据库使用 `jobfit_test`（与开发库隔离），测试间清理状态。
3. 默认使用 test-only `DeterministicProvider`（由 backend 测试夹具注入），**不依赖** live DeepSeek。
4. 权威判定以 **exit code + test report**（JUnit/Playwright report）为准，不以"浏览器打开了"为通过标准。
5. 覆盖：创建分析、状态、硬条件（含 UNKNOWN 呈现）、技能、证据、决策链、critique（含 citation 校验展示）、报告、review approve/reject，以及跨 analysis / fabricated citation 被拒。

**Consequences:**
- 优点：端到端可信；可在 CI 中稳定重放。
- 代价：需要 Node 工具链与后端进程编排；环境缺 Node 时该步骤显式标记为未执行（不伪通过）。

**Alternatives:**
- 用前端 mock 做"端到端"：否决——正好是 §56 明令禁止的 fake frontend。

---

## 决策索引

| ADR | 主题 | 状态 | 版本 |
| --- | --- | --- | --- |
| ADR-001 | 确定性核心 + LLM critique 双层架构 | Accepted | MVP |
| ADR-002 | LLM 输出强制 Pydantic structured output | Accepted | MVP |
| ADR-003 | LLM Provider Protocol（默认 DeepSeek）| Accepted | MVP |
| ADR-004 | LangGraph 编排 + HITL interrupt + checkpoint | Accepted | MVP |
| ADR-005 | PostgreSQL + pgvector + SQLAlchemy + Alembic | Accepted | MVP |
| ADR-006 | Embedding Protocol，默认 bge-m3 本地 | Accepted | V1 |
| ADR-007 | 证据锚定 + 禁止自由引用 | Accepted | MVP |
| ADR-008 | `UNKNOWN` 一等公民三值语义 | Accepted | MVP |
| ADR-009 | HITL 强制发布闸门 | Accepted | MVP |
| ADR-010 | 硬条件判定确定性规则化 | Accepted | MVP |
| ADR-011 | 文件格式白名单，扫描件不进 MVP | Accepted | MVP |
| ADR-012 | URL Ingestion 推迟 V2（SSRF）| Accepted | V2 |
| ADR-013 | 密钥治理 + PII 脱敏/加密/审计 | Accepted | MVP |
| ADR-014 | 可观测性零依赖默认，LangSmith 可选 | Accepted | MVP |
| ADR-015 | Counterfactual/批量基于分数分解 | Accepted | V1 |
| ADR-016 | 每 phase 测试 + golden 回归（指标语义见 ADR-022）| Accepted | MVP |
| ADR-017 | Job Runner & Crash Recovery（API/Worker 分离，PG 队列 + lease + claim_token fencing）| Accepted（部分 supersede ADR-004 执行机制）| MVP |
| ADR-018 | 统一 Retry Budget（max_llm_attempts，无叠加）| Accepted | MVP |
| ADR-019 | Pipeline & Rules Versioning（版本化 YAML + config_snapshot）| Accepted | MVP |
| ADR-020 | Explainable Decision Trace（仅 deterministic/auditable）| Accepted | MVP |
| ADR-021 | MVP 安全能力范围如实声明 | Accepted | MVP |
| ADR-022 | Evaluation Threshold 语义化（target/gate/measured/baseline）| Accepted | MVP |
| ADR-023 | Document Data Ownership & Immutable Versioned Artifacts（parse/profile 版本化 + 显式绑定）| Accepted | MVP |
| ADR-024 | Parser / Chunker 版本化与 parse artifact 身份 | Accepted（Phase 2 已实现）| MVP |
| ADR-025 | 确定性滑窗 Chunking（size/overlap 属 artifact 身份）| Accepted（Phase 2 已实现）| MVP |
| ADR-026 | Extraction Artifact 身份与确定性 Fingerprint | Accepted（Phase 2 已实现）| MVP |
| ADR-027 | Prompt 版本化（内容哈希 `h:`，不引入模板引擎）| Accepted（Phase 2 已实现）| MVP |
| ADR-028 | 幂等 Artifact 写入（唯一键 + `ON CONFLICT DO NOTHING`）| Accepted（Phase 2 已实现）| MVP |
| ADR-029 | 测试 Provider 策略（确定性 fake + 真实 PG 集成测试）| Accepted（Phase 2 已实现）| MVP |
| ADR-030 | 证据检索策略（确定性 anchor gate + pgvector 排序 + lexical fallback）| Accepted（Phase 3 已实现）| MVP |
| ADR-031 | 硬条件判定的权威性约定（何时允许 FALSE）| Accepted（Phase 3 已实现）| MVP |
| ADR-032 | 确定性技能匹配（absence 不产生 NO_MATCH）| Accepted（Phase 3 已实现）| MVP |
| ADR-033 | Decision Trace 实现（metadata-only / 可重生成 / 收敛）| Accepted（Phase 3 已实现）| MVP |
| ADR-034 | Analysis 身份 / 幂等 与生命周期终态（`succeeded`）| Accepted（Phase 3 已实现）| MVP |
| ADR-035 | 确定性计分规则与 UNKNOWN 策略（显式配置）| Accepted（Phase 3 已实现）| MVP |
| ADR-036 | LLM Critique 契约、幂等与 Citation Validation | Accepted（Phase 4 已实现）| MVP |
| ADR-037 | Evidence-Grounded Report 装配、校验与不可变发布 | Accepted（Phase 4 已实现）| MVP |
| ADR-038 | HITL Review 生命周期与并发安全 | Accepted（Phase 4 已实现）| MVP |
| ADR-039 | Golden Evaluation Set 与 live / deterministic provider 口径 | Accepted（Phase 4 已实现）| MVP |
| ADR-040 | Frontend Architecture（Next.js App Router + 统一 API client，presentation-only）| Accepted（Phase 5 已实现）| MVP |
| ADR-041 | API Error Contract 与 Request Correlation | Accepted（Phase 5 已实现）| MVP |
| ADR-042 | Deterministic Golden Evaluation Runner | Accepted（Phase 5 已实现）| MVP |
| ADR-043 | E2E Strategy（Playwright + 真实 backend，无外部依赖）| Accepted（Phase 5 已实现）| MVP |

> **实现状态口径**：标注"Phase 2/3/4/5 已实现"的 ADR，其描述的行为均有对应源码与测试；
> 未标注者仍属设计约定，尚未落地（详见 architecture.md §12.1 的实现现状清单）。

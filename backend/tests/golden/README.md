# Golden Evaluation Set

Phase 4 §34 建立的 golden foundation，在 Phase 5 §27–§30 整理为**声明式、可执行、可渲染报告**的结构。

## 目录结构

```
tests/golden/
  cases/golden_cases.json        # 每个 case 的 input（fixture + payload 名称）+ 声明的 metrics
  expected/golden_expected.json  # 每个 case 的 expected（deterministic / critique / report / review）
  fixtures/*.txt                 # 完全合成、无 PII 的输入文档
  test_golden_evaluation.py      # 通用 driver：加载声明 -> 跑 phase3/phase4/review -> 计算指标 -> 断言
  README.md
```

`cases` 与 `expected` 以 `name` 为键一一对应：`cases` 描述**输入与要考哪些指标**，
`expected` 描述**预期结论**。新增 case 只需同时改这两个文件，无需改 driver。

## case 字段

`cases/golden_cases.json` 每一项：

| 字段 | 含义 |
| --- | --- |
| `name` | case 唯一名（与 `expected` 的键一致） |
| `description` | 该 case 考察什么 |
| `metrics` | 该 case 断言哪些指标（受控集合，见下） |
| `resume_fixture` / `jd_fixture` | `fixtures/` 下的合成文档 |
| `resume_payload` / `jd_payload` | test-only `DeterministicProvider` 使用的抽取 payload 名 |

`expected/golden_expected.json` 每一项：

| 字段 | 含义 |
| --- | --- |
| `deterministic` | `gate` / `verdicts`（distinct 集合）/ `skills` / `constraint_count` |
| `critique` | `mode`（valid / fabricated / cross_analysis / unknown_override / contradicted）、`validation`、`citations_validated`、`derived_sections` |
| `report` | `stage`、`required_sections`、`forbidden_sections` |
| `review` | 可选：`decision` / `final_status` / `report_stage` |

## 指标语义（§29，全部由代码计算，非 LLM 自评）

| metric | 语义 |
| --- | --- |
| `hard_constraint_exact_agreement` | gate / verdict 集合 / 约束条数与预期逐项一致 |
| `skill_match_agreement` | 技能匹配状态与预期逐项一致 |
| `unknown_preservation` | UNKNOWN 完整保留（不写成 FALSE，也不被 LLM 改写） |
| `citation_validity` | citation 校验结论与预期一致（fabricated / 跨 analysis 一律 rejected） |
| `report_invariant_validity` | 报告 stage 与分区不变量成立（含 final 不可变） |
| `decision_trace_completeness` | 每条约束都有 `trace_id`，且 trace 链非空 |
| `review_lifecycle` | HITL 状态机转移、终态与审计留痕正确 |

## 覆盖的场景（§30）

`perfect_match` · `obvious_mismatch` · `unknown` · `mixed_constraints` · `skill_partial_match` ·
`citation_failure` · `cross_analysis_citation` · `prompt_injection` · `contradictory_evidence` ·
`review_approval` · `review_rejection`

## 运行方式

```powershell
# 只跑 golden（需要真实 PostgreSQL / pgvector；无 DB 时自动 skip）
python -m pytest backend/tests/golden

# 跑 golden 并渲染报告（phase5-evaluation.json / phase5-evaluation.md）
python -m jobfit.evaluation
```

`python -m jobfit.evaluation` 会：

1. 以 subprocess 运行 golden suite（pytest exit code 权威）；
2. 读取测试 session 写出的指标（`JOBFIT_GOLDEN_METRICS_PATH`）；
3. 渲染 `phase5-evaluation.json` 与 `phase5-evaluation.md`。

## 约束

- **不**调用 live LLM：默认使用 test-only `DeterministicProvider`（ADR-029）；
  live DeepSeek 调用不进普通 pytest 路径（ADR-039）。
- **不**比对 LLM 自然语言逐字一致，只断言语义不变量（§34）。
- fixtures 全部为合成/匿名数据，**禁止真实 PII**。
- 指标集合受控：新增指标需同时更新 case 声明与 `jobfit/evaluation/runner.py` 的
  `DEFAULT_METRIC_NAMES`；`tests/golden/test_golden_evaluation.py` 会校验两者一致。

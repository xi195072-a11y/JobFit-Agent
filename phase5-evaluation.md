# Phase 5 — Golden Evaluation Report

- schema: `phase5-evaluation.v1`
- generated_at (UTC): `2026-09-11T07:21:28.011828+00:00`
- golden dir: `D:\ai\backend\tests\golden`
- metrics source: `C:\Users\xiao'xi\AppData\Local\Temp\jobfit-eval-qaww5237\golden-metrics.json`
- ran pytest: `True`

## Totals

| 指标 | 值 |
| --- | --- |
| total cases | 11 |
| passed | 11 |
| failed | 0 |
| junit tests | 13 |
| junit failures | 0 |
| junit errors | 0 |
| junit skipped | 0 |
| overall | PASS |

## Metrics（由确定性代码计算，非 LLM 自评）

| metric | passed/total | rate | 语义 |
| --- | --- | --- | --- |
| `hard_constraint_exact_agreement` | 9/9 | 100.0% | gate / verdict 集合 / 约束条数与预期逐项一致 |
| `skill_match_agreement` | 5/5 | 100.0% | 技能匹配状态与预期逐项一致 |
| `unknown_preservation` | 5/5 | 100.0% | UNKNOWN 完整保留（不写成 FALSE，也不被 LLM 改写） |
| `citation_validity` | 11/11 | 100.0% | citation 校验结论与预期一致（fabricated / 跨 analysis 一律 rejected） |
| `report_invariant_validity` | 7/7 | 100.0% | 报告 stage 与分区不变量成立（含 final 不可变） |
| `decision_trace_completeness` | 7/7 | 100.0% | 每条约束都有 trace_id，且 trace 链非空 |
| `review_lifecycle` | 2/2 | 100.0% | HITL 状态机转移、终态与审计留痕正确 |

## Cases

| case | passed | junit | declared metrics | reasons |
| --- | --- | --- | --- | --- |
| `citation_failure` | yes | passed | hard_constraint_exact_agreement, citation_validity, decision_trace_completeness | — |
| `contradictory_evidence` | yes | passed | hard_constraint_exact_agreement, citation_validity, decision_trace_completeness | — |
| `cross_analysis_citation` | yes | passed | hard_constraint_exact_agreement, citation_validity | — |
| `mixed_constraints` | yes | passed | hard_constraint_exact_agreement, skill_match_agreement, unknown_preservation, citation_validity, report_invariant_validity, decision_trace_completeness | — |
| `obvious_mismatch` | yes | passed | hard_constraint_exact_agreement, skill_match_agreement, unknown_preservation, citation_validity, report_invariant_validity, decision_trace_completeness | — |
| `perfect_match` | yes | passed | hard_constraint_exact_agreement, skill_match_agreement, citation_validity, report_invariant_validity, decision_trace_completeness | — |
| `prompt_injection` | yes | passed | hard_constraint_exact_agreement, unknown_preservation, citation_validity | — |
| `review_approval` | yes | passed | citation_validity, report_invariant_validity, review_lifecycle | — |
| `review_rejection` | yes | passed | citation_validity, report_invariant_validity, review_lifecycle | — |
| `skill_partial_match` | yes | passed | hard_constraint_exact_agreement, skill_match_agreement, unknown_preservation, citation_validity, report_invariant_validity, decision_trace_completeness | — |
| `unknown` | yes | passed | hard_constraint_exact_agreement, skill_match_agreement, unknown_preservation, citation_validity, report_invariant_validity, decision_trace_completeness | — |

> 说明：本报告不包含模型质量自评；全部指标均由 `tests/golden/` 中的确定性断言计算。
> 未运行 live DeepSeek（无 `DEEPSEEK_API_KEY`）时，LLM 相关结论为 EXTERNAL CREDENTIAL BLOCKED，
> 绝不记为 PASS（ADR-021/ADR-039）。

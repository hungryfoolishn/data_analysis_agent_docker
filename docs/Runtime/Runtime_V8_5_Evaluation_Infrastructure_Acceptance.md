# Runtime V8.5 Evaluation Infrastructure Acceptance

## 1. Goal

Runtime V8 建立了 learning telemetry；V8.5 建立可信评价基础设施。V8.5 不修改 Skill，也不启用 GEPA，而是先回答：

- Agent 的分析结果是否正确？
- 失败发生在哪个阶段？
- 数字是否可追溯到 Evidence？
- Skill 在特定任务类型上是否可靠？
- 同一个 Skill 修改后能否和旧版本公平比较？

---

## 2. Implemented Contracts

### 2.1 Golden Dataset

位置：

```text
tests/evaluation/golden_cases/
tests/evaluation/data/sales_performance_2025_2026h1.csv
```

第一批包含 `50` 个 case，覆盖：

| 类型 | 数量 |
|---|---:|
| 单指标 | 10 |
| 多指标 | 6 |
| 同比 / 环比 | 8 |
| 分组比较 | 8 |
| 趋势 | 6 |
| 异常 | 6 |
| 综合分析 | 6 |

每个 case 包含：

- `question`
- `dataset`
- `dataset_sha256`
- `expected_metrics`
- `tolerance`
- `period` / `dimension` / `group` / `filters`
- `required_evidence`
- `required_sql`
- `required_finding`
- `required_report`
- `report_must_contain`
- `report_must_not_contain`

### 2.2 Golden Loader

位置：`langgraph_langchain/runtime/evaluation/golden_loader.py`

`GoldenCaseLoader` 支持：

- 加载单个或多个 JSON 文件。
- 校验 case ID 唯一。
- 解析数据集路径并限制在 evaluation root 内。
- 校验 dataset 是否存在。
- 校验 `dataset_sha256`。

### 2.3 Offline Evaluation Runner

位置：`langgraph_langchain/runtime/evaluation/runner.py`

核心接口：

```python
runner.run_case(case, candidate)
runner.run_dataset(cases, candidates, run_id=...)
```

`GoldenCandidateResult` 是候选运行的规范输入，包含：

- `status`
- `verification_passed`
- `metric_answers`
- `evidence_count`
- `verified_evidence_count`
- `finding_count`
- `sql_text`
- `report_text`
- `skill_id`
- `skill_name`
- `skill_version`
- `skill_hash`

### 2.4 Multi-dimensional Score

`EvaluationScore` 使用当前权重：

```text
total_score =
  0.10 * execution_score
+ 0.10 * verification_score
+ 0.30 * numeric_score
+ 0.15 * evidence_score
+ 0.15 * finding_score
+ 0.20 * report_score
```

`passed` 不是简单使用 score 阈值，而是要求：

- execution 成功
- verification 通过
- 全部 expected numeric checks 通过
- required Evidence 数量满足
- required Finding 存在
- required SQL 存在
- report 没有缺失 token，也没有 forbidden claim

---

## 3. Numeric Correctness

`compare_metric()` 使用确定性比较：

- 支持 absolute tolerance。
- 对“率 / ratio / rate / percent”类指标自动处理 `0.823` 与 `82.3%` 的尺度差异。
- 输出 `MetricComparison`，包含 expected、actual、absolute_error、tolerance、period、group、filters。
- 数值错误会在 `failures` 中以 `numeric:<metric>:...` 记录。

这意味着 Runtime 成功但 Golden 数字错误时，case 仍然失败。

---

## 4. Skill Version / Hash

Skill 身份现在贯穿：

```text
SkillMeta
→ SkillMatch
→ TaskExecutionRequest metadata
→ ExecutionResult
→ TaskEvaluation
→ SkillQuality
```

新增字段：

```text
skill_id
skill_name
skill_version
skill_hash
```

`SkillsLoader.skill_hash()` 使用 SKILL.md 文件内容的 SHA-256，避免同一名字下不同版本被混在一起统计。

---

## 5. Confidence-aware SkillQuality

`SkillQuality` 新增：

- `smoothed_success_rate = (successes + 1) / (attempts + 2)`
- `recent_success_rate`：最近 10 次成功率
- `confidence = attempts / (attempts + 5)`
- `task_type_success_rate`
- `executor_success_rate`
- 加权 `quality_score`

这避免 `2/2 = 100%` 的小样本结果压过 `94/100 = 94%` 的稳定结果。

`SkillRetriever` 可消费 learning memory：

- `reliable`：lexical score + 0.10
- `needs_review`：lexical score - 0.30
- `neutral`：不调整

当前阶段只影响排序，不自动改写 Skill。

---

## 5.5 Structured Failure Taxonomy

位置：`langgraph_langchain/runtime/learning.py`

`FailureCase` 保留 Runtime V8 的一级字段 `failure_kind`，同时新增：

- `stage`
- `category`
- `root_cause`
- `symptom`
- `taxonomy`

确定性映射示例：

| failure_kind | stage | category | root_cause | symptom |
|---|---|---|---|---|
| `verification` | `verification` | `numeric_consistency` | `calculation_mismatch` | `numeric_value_mismatch` |
| `evidence` | `evidence` | `evidence_binding` / `evidence_generation` | artifact or generation failure | `artifacts_without_verified_evidence` |
| `finding` | `finding` | `finding_provenance` | `missing_verified_evidence` | `finding_not_created` |
| `report` | `report` | `report_integrity` / `report_structure` | unsupported number or missing content | `report_rejected` |
| `execution` | `execution` | `tool_execution` | timeout / permission / missing data / tool error | execution symptom |
| `cancelled` | `workflow` | `workflow` | user/system cancellation | `run_cancelled` |

---

## 5.6 Cross-task Verification

位置：`langgraph_langchain/runtime/evaluation/cross_task.py`

`CrossTaskConsistencyVerifier.verify(evaluations)` 会比较：

```text
metric
+ period
+ dimension
+ group
+ filters
```

当两个以上 Task 对同一上下文给出超过 tolerance 的不同数值时，生成 `ConsistencyIssue`：

- `task_ids`
- `execution_ids`
- `values`
- `tolerance`
- `max_delta`
- deterministic `issue_id`
- human-readable message

`LearningSnapshot.consistency_issues` 由 `LearningMemoryStore.record_evaluations()` 自动刷新。

---

## 5.7 Claim-level Provenance

位置：`langgraph_langchain/runtime/report_claims.py`

`ReportClaim` 将报告要点映射到：

```text
ReportClaim
→ Finding
→ Evidence
→ Artifact
→ Execution
→ Skill(version/hash)
```

核心接口：

```python
extract_report_claims(report_text, findings, evidence)
trace_claim_lineage(claim, findings, evidence, artifacts, executions)
```

`ClaimLineage.complete` 只有在 Finding、Evidence、Artifact、Execution 四层都可追溯时为 true。

`finish_report` 现在会生成并注册 `report_claims.json`，包含：

- `claims`
- `lineages`

---

---

## 6. P0 Acceptance

| ID | Requirement | Status |
|---|---|---|
| V8.5-P0-001 | Golden Dataset ≥ 50 | PASS |
| V8.5-P0-002 | 每个 case 有 deterministic expected metrics | PASS |
| V8.5-P0-003 | Dataset hash 可验证 | PASS |
| V8.5-P0-004 | Offline Evaluation Runner | PASS |
| V8.5-P0-005 | Multi-dimensional score | PASS |
| V8.5-P0-006 | Numeric correctness 是核心分量 | PASS |
| V8.5-P0-007 | Skill version / hash 可追踪 | PASS |
| V8.5-P0-008 | Confidence-aware SkillQuality | PASS |
| V8.5-P0-009 | Learning feedback 影响 SkillRetriever | PASS |
| V8.5-P0-010 | 不自动改写 Skill | PASS |
| V8.5-P0-011 | Failure Taxonomy 保持 V8 兼容 | PASS |
| V8.5-P0-012 | Cross-task contradiction 可发现 | PASS |
| V8.5-P0-013 | Claim provenance 可追溯至 Execution | PASS |
| V8.5-P0-014 | Observation 不混淆不同 group/filter | PASS |
| V8.5-P0-015 | Cross-task comparison 使用 pairwise tolerance | PASS |
| V8.5-P0-016 | stable Skill ID 与 display name 分离 | PASS |
| V8.5-P0-017 | CI 运行 targeted + full pytest | PASS |
| V8.5-P0-014 | finish_report 生成 report_claims.json | PASS |

---

## 7. Tests

```text
tests/evaluation/test_runtime_v8_5_golden.py
```

覆盖：

1. Golden Dataset 数量、唯一 ID 和 dataset hash。
2. Numeric comparator 的 tolerance 与百分比尺度。
3. Runtime 成功但 Golden 数字错误时 case 失败。
4. EvaluationRun 按 task type / executor / skill 聚合。
5. Bayesian smoothing、recent success rate、confidence。
6. Skill version / hash 从 request metadata 进入 ExecutionResult。
7. Evidence group/filter 继承与 Cross-task pairwise tolerance。
8. 中文 paraphrase provenance 与 stable Skill ID。

另外：

```text
tests/evaluation/test_runtime_v8_5_advanced.py
```

覆盖：

1. Cross-task contradiction 检测。
2. 相同值或不同 period 不误报。
3. Structured Failure Taxonomy。
4. ReportClaim 与 Finding / Evidence 映射。
5. ClaimLineage 追溯 Artifact / Execution / Skill。
6. LearningMemoryStore 持久化 consistency issue。

Runtime E2E 额外验证 `finish_report` 生成并注册 `report_claims.json`。

---

## 7.1 V8.5.1 Correctness Patch

修复实施检查报告中的 7 项问题：

1. Cross-task `MetricObservation` 继承 Evidence 的 `group` 和 `filters`，East / West 不再被误判为同一上下文。
2. Cross-task comparison 改为 pairwise tolerance；某个宽松 tolerance 不能掩盖 strict observations 之间的矛盾。
3. 修复 `extract_report_claims()` 中 `item` fallback 可能引用上一轮循环变量的问题。
4. `trace_claim_lineage()` 以 `claim.evidence_ids` 为权威入口，再由 Finding evidence 补充。
5. 增加中文 paraphrase provenance 测试，支持“1.05 万”到 `10500`、部门、下滑、贡献等结构化匹配。
6. Runtime 区分 `skill_id`、`skill_name`、`skill_version`、`skill_hash`。
7. 增加 GitHub Actions workflow，并运行 targeted tests 与 full pytest。

对应测试：

```text
tests/evaluation/test_runtime_v8_5_advanced.py
tests/evaluation/test_runtime_v8_5_golden.py
tests/runtime_v2/test_runtime_e2e.py
```

覆盖：

- Evidence group/filter 继承。
- East / West 不误报。
- pairwise tolerance。
- Structured Failure Taxonomy。
- 中文改写 ReportClaim。
- Claim lineage 到 Artifact / Execution / Skill。
- stable Skill ID 与 display name 分离。

---

## 8. Non-Goals

V8.5 不做：

- Skill 文件自动改写
- GEPA / DSPy prompt evolution
- vector database
- multi-agent orchestration
- parallel DAG replanning
- PostgreSQL migration

这些属于 Runtime V9 及以后阶段。

---

## 9. Result

```text
Runtime V8.5 Evaluation Infrastructure + Trusted Learning Loop = READY
```

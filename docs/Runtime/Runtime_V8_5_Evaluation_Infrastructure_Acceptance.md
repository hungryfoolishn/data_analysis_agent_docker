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
Runtime V8.5 Evaluation Infrastructure = READY
```

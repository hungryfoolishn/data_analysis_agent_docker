# B4 / M4: 通用结构化 Planner

## 范围

B4 只实现确定性、结构化 Planner 与 allowlist 校验。Planner 不执行 Python、SQL
或图表；也不绑定宏观经济/财务语义。

## 新增模块

```text
langgraph_langchain/runtime/general_planner.py
```

## Plan schema

`GeneralAnalysisPlan` 包含：

- `plan_id`
- `user_goal`
- `input_asset_ids`
- `steps`

`GeneralPlanStep` 包含：

- `step_id`
- `operation`
- `input_refs`
- `parameters`
- `expected_output`
- `depends_on`
- `rationale`

## 操作 allowlist

- `inspect`
- `select_columns`
- `filter`
- `group_aggregate`
- `sort`
- `trend`
- `detect_anomalies`
- `summarize`

不支持任意 Python/SQL 操作。

## 确定性规划

`DeterministicAnalysisPlanner.plan()` 支持常见目标：

- group aggregation：`group by`、`按`、`每个`，以及 sum/mean/count/min/max。
- trend：时间列 + 数值列。
- anomaly detection：数值列 IQR。
- summarize。

目标不清楚时抛出 `PlannerClarificationError`，返回明确澄清问题，而不是猜测。

## 校验

`validate_general_plan()` 校验：

- 输入资产存在；
- step_id 唯一；
- operation 在 allowlist；
- input refs 有效；
- dependencies 有效且不是 forward reference；
- 参数类型和值合法；
- 引用列存在；
- expected output 与 operation 匹配。

循环依赖会在当前实现下以 forward/unknown dependency 拒绝。

## 失败语义

- 结构错误：`PlanValidationError`
- 需要澄清：`PlannerClarificationError`
- 不支持操作：`PlanValidationError`

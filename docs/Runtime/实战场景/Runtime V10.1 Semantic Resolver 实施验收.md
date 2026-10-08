# Runtime V10.1 Semantic Resolver 实施验收

## 结论

V10.1 已完成。本阶段新增独立 `FinancialSemanticResolver`，把用户目标解析为可执行语义要素：公司、期间、指标、数据需求、数据覆盖和澄清请求。Planner 可以在生成 DAG 前消费该结果。

本阶段不实现真实工具执行器，不修改 V9.3 MetricEngine 公式，不进入 V10.2 Runtime Integration，也不引入 LLM Planner、自动重规划或 Skill Evolution。

## 交付内容

### 1. 新增 Semantic Resolver

新增：

```text
langgraph_langchain/runtime/financial_agent/semantic.py
langgraph_langchain/runtime/financial_agent/semantic_models.py
```

核心组件：

| 组件 | 职责 |
| --- | --- |
| `CompanyRegistry` | 统一公司注册表，支持公司名、公司 ID、证券代码和英文别名 |
| `FinancialSemanticResolver.resolve()` | 输入 Task Understanding，输出 `SemanticResolution` |
| `PeriodCoverage` | 对比请求期间与数据集可用期间 |
| `MetricDefinitionResolution` | 输出指标名称、公式、单位、计算类型、口径和 source fields |
| `DataRequirement` | 输出当前期 / 上期报表字段需求 |
| `DataCoverageResult` | 输出公司 / 期间 / 指标覆盖与缺口 |
| `ClarificationRequest` | 输出结构化澄清诊断 |

### 2. 公司解析

公司注册表支持：

```text
EXACT_NAME
COMPANY_ID
STOCK_CODE
ALIAS
```

示例：

```text
贵州茅台 → 贵州茅台
Moutai   → 贵州茅台
600519   → 贵州茅台
```

证券代码使用数字边界匹配：

```text
600519   → 命中
1600519  → 不命中
```

解析结果包含：

```text
resolved companies
unresolved company terms
matches
diagnostics
```

### 3. 期间覆盖解析

Resolver 会把请求的年度范围与数据集中公司实际可用期间对齐。

输出：

```text
requested_start_year
requested_end_year
available_periods
selected_periods
missing_periods
status
```

状态包括：

```text
COMPLETE
PARTIAL
NO_MATCHING_PERIODS
NO_AVAILABLE_PERIODS
```

局部期间缺失不会静默扩大或缩小范围，而是保留：

```text
selected_periods
missing_periods
```

### 4. 指标与口径解析

Resolver 基于 V9.3 `financial_metric_registry` 解析：

```text
metric_id
name
category
formula
unit
calculation_type
balance_policy
source_fields
```

未知指标输出：

```text
SEMANTIC_UNKNOWN_METRIC:<metric_id>
```

指标 source field 同时做兼容归一化：

```text
balance_statement → balance_sheet
cash_statement    → cash_flow
```

### 5. 数据需求解析

根据指标类型生成当前期 / 上期数据需求：

| 指标类型 | 当前数据 | 上期数据 |
| --- | --- | --- |
| reported | ✅ | 不需要 |
| growth | ✅ | ✅ |
| ratio / ending_balance | ✅ | 不需要 |
| ratio / average_balance | ✅ | ✅ denominator |
| derived / free cash flow | ✅ | 不需要 |

例如：

```text
revenue_growth:
  CURRENT  income_statement.revenue
  PREVIOUS income_statement.revenue

roe:
  CURRENT  income_statement.net_profit
  CURRENT  balance_sheet.total_equity
  PREVIOUS balance_sheet.total_equity
```

### 6. 数据覆盖检查

每个公司 / 期间 / 指标生成：

```text
MetricCoverage.status
missing_fields
previous_period
```

状态包括：

```text
AVAILABLE
MISSING_CURRENT_DATA
MISSING_PREVIOUS_DATA
PERIOD_MISSING
```

覆盖结果分为：

```text
blockers
warnings
```

关键规则：

1. 局部字段缺失记录为 warning，不静默填零。
2. 上期缺失记录为 warning，后续执行时应返回 `UNAVAILABLE`。
3. 只有完全没有任何可用指标数据时才输出 `SEMANTIC_DATA_NOT_AVAILABLE`。
4. 部分指标缺失不会阻止其他可计算指标进入 V10.2 执行。
5. 所有缺口保留公司、期间、指标、报表类型、字段和原因。

### 7. Planner 集成与状态语义分离

`FinancialTaskPlanner` 新增：

```python
semantic_resolver: FinancialSemanticResolver | None
```

当 `SemanticResolution.ready_for_planning = True` 时，Planner 生成原有 DAG，并把完整语义解析结果写入：

```python
plan.metadata["semantic_resolution"]
plan.metadata["semantic_resolution_version"]
```

当语义解析阻塞时，Planner 返回：

```text
plan.status = BLOCKED
plan.planning_readiness = 原理解析结果
plan.planning_diagnostics = semantic diagnostics + blockers
plan.metadata.blocked_by = "semantic_resolution"
plan.metadata.blocked_reasons = semantic blockers
```

本次进一步明确两个概念分离：

| 字段 | 含义 |
| --- | --- |
| `planning_readiness` | 用户请求是否具备规划条件 |
| `plan.status` | 生成的 Plan 本身是否合法 / 可执行 / 阻塞 |

因此，如果用户请求 READY 但 Tool Registry 配置错误，结果是：

```text
plan.status = INVALID
plan.planning_readiness = READY
```

不再把输入就绪性和 Plan 合法性混为一谈。

### 8. 版本更新

工具注册表更新：

```text
company_resolver       v10.1.0
period_resolver        v10.1.0
data_coverage_checker  v10.1.0
```

`SemanticResolution` 包含：

```text
resolver_version = v10.1.0
```

## 测试验收

### Semantic Resolver 专项测试

```bash
PYTHONPATH=. pytest tests/financial_agent/test_semantic_resolver.py -q
```

结果：

```text
12 passed
```

覆盖：

1. 精确公司名、别名、证券代码。
2. 证券代码数字边界。
3. 未解析公司诊断与阻塞。
4. 可用期间选择与缺失期间报告。
5. 已注册指标与未知指标。
6. 当前期 / 上期数据需求。
7. 局部字段缺失作为 warning。
8. 首期上期缺失作为 warning。
9. 无任何可用数据时阻塞。
10. Planner 集成 Semantic Resolution。
11. 语义阻塞 Plan 的诊断透传。
12. `plan.status` 与 `planning_readiness` 独立。

### Financial + Financial Agent 联合测试

```bash
PYTHONPATH=. pytest tests/financial tests/financial_agent -q
```

结果：

```text
146 passed
```

### 全量回归

```bash
PYTHONPATH=. pytest -q
```

结果：

```text
824 passed, 26 warnings, 4 subtests passed
```

## V9.3 兼容性

未修改：

```text
FinancialMetricEngine 公式
Formula Golden
Verification Engine
Evidence / Finding / Report
Real Financial Data Loader
```

V9.3 指标 source field 中的历史命名由 V10.1 Resolver 做兼容归一化，不改变既有 MetricEngine 行为。

## 边界与不做事项

V10.1 明确不做：

```text
❌ 真实执行 Plan
❌ 真实工具调用绑定
❌ LLM Semantic Resolver
❌ 自动重规划
❌ Insight Engine
❌ 多 Agent 框架
❌ 实时行情
❌ 估值 / DCF / PE / PB
❌ 自动投资建议
❌ Skill Evolution / GEPA
```

## 剩余事项

进入 V10.2 前需要完成：

1. 把 Tool Registry 从能力声明升级为真实执行器绑定。
2. Planner 按 `selected_periods` 和可用指标驱动 V9.3 Workflow。
3. 局部缺失数据在执行结果中保留 `UNAVAILABLE` 与溯源。
4. Plan 执行状态从 `PENDING` 推进到真实 `RUNNING / SUCCEEDED / FAILED`。

## 验收结论

```text
V10.1 状态：FINAL
Semantic Resolver 测试：12 / 12 passed
Financial + Financial Agent：146 passed
Full regression：824 passed
```

# Runtime V10.2 Runtime Integration 实施验收

## 结论

V10.2 已完成从语义解析到可执行计划的接入。当前阶段把 Tool Registry 升级为可执行注册表，新增 ExecutionPlan 与 Runtime Executor，并通过既有 V9.3 `FinancialAnalysisWorkflow` 完成计算、独立验证、Evidence、Finding 和报告生成。

本阶段没有引入 LLM Planner、自动重规划、多 Agent、实时行情、估值或 Skill Evolution。

## 交付内容

### 1. 可执行 Tool Registry

新增：

```text
langgraph_langchain/runtime/financial_agent/execution_models.py
langgraph_langchain/runtime/financial_agent/tool_execution.py
```

核心能力：

```text
FinancialToolSpec        静态能力声明
ToolExecutor             Python 可调用执行器
ExecutableFinancialTool  Spec + Executor 绑定
FinancialToolExecutionRegistry  执行注册表
ToolExecutionRequest / Result   稳定请求与结果契约
```

注册表支持：

```text
from_specs()         根据静态工具目录绑定执行器
register()           注册新绑定
get()                读取绑定
execute()            执行工具并返回结果
list_tools()         稳定顺序列出绑定
```

执行器异常不会破坏 Runtime；会返回：

```text
status = FAILED
error  = "RuntimeError: ..."
```

### 2. ExecutionPlan

新增：

```text
ExecutionPlanStatus
ExecutionTask
ExecutionPlan
```

`ExecutionPlanBuilder` 将 `SemanticResolution` 展开为：

```text
company × period × metric
```

粒度任务。

每个 `ExecutionTask` 包含：

```text
task_id
tool_id
tool_version
company_id
company_name
period
metric_id
metric_name
dependencies
required_data
inputs
status
verification_status
status_reason
missing_fields
output_refs
failure_policy
metadata
```

关键语义：

1. 数据完整任务状态为 `PENDING`。
2. 缺少当前期字段的任务状态为 `UNAVAILABLE`。
3. 缺少上期字段的任务状态为 `UNAVAILABLE`。
4. 缺失期间生成显式 `UNAVAILABLE` 任务。
5. 不允许静默填 0，也不允许删除缺口。
6. `revenue_growth` 会依赖上一年同公司 `revenue` 任务。
7. `ExecutionPlan.validate()` 检查重复任务、未知依赖、自依赖和依赖循环。

### 3. Runtime Executor

新增：

```text
langgraph_langchain/runtime/financial_agent/runtime_integration.py
```

核心类：

```text
FinancialRuntimeExecutor
```

执行链路：

```text
ExecutionPlan
    ↓
FinancialQuery
    ↓
V9.3 FinancialAnalysisWorkflow
    ↓
MetricEngine
    ↓
VerificationEngine
    ↓
FinancialCalculation / Verification / Observation / Evidence
    ↓
RuntimeExecutionResult
```

Executor 会：

1. 校验 Plan 必须为 `PENDING`。
2. 根据 Plan 任务构造 `FinancialQuery`。
3. 调用既有 V9.3 Workflow。
4. 按 `company_id / period / metric_id` 索引 Calculation、Verification、Observation、Evidence。
5. 将 Workflow 结果回写到每个 ExecutionTask。
6. 生成 `RuntimeTaskResult`。
7. 汇总 `SUCCEEDED / PARTIAL / FAILED`。
8. 保留 V9.3 的完整 `FinancialAnalysisResult` 和 report markdown。

状态映射：

| Workflow 结果 | ExecutionTask 状态 |
| --- | --- |
| calculation calculated + verification passed + evidence 存在 | `SUCCEEDED` |
| calculation unavailable | `UNAVAILABLE` |
| calculation invalid | `FAILED` |
| verification failed | `FAILED` |
| workflow 未返回 calculation | `NOT_EXECUTABLE` |
| verification 未返回 | `FAILED` |
| verified 但缺少 Observation / Evidence | `FAILED` |

### 4. Semantic Coverage 修复

修复上期数据检查：

```text
data_service.statements(company_id, period, previous_period)
```

此前 Resolver 检查上期字段时没有传入 `previous_period`，导致增长率被误判为全部不可用。修复后，首期增长率仍正确标记为 `UNAVAILABLE`，后续期间可正常执行。

同时，V9.3 source field 历史命名在 V10.1 Resolver 中兼容归一化：

```text
balance_statement → balance_sheet
cash_statement    → cash_flow
```

## 测试结果

### Tool Registry

```bash
PYTHONPATH=. pytest tests/financial_agent/test_tool_execution.py -q
```

```text
8 passed
```

覆盖：

1. 绑定与执行。
2. 静态工具目录与执行器绑定。
3. 重复 tool ID。
4. 非可调用执行器。
5. 未知静态工具绑定。
6. 未知运行时工具。
7. 执行器异常转为 FAILED。
8. 稳定顺序列出工具。

### ExecutionPlan

```bash
PYTHONPATH=. pytest tests/financial_agent/test_execution_plan.py -q
```

```text
8 passed
```

覆盖：

1. 完整收入 / 净利润计划展开。
2. 增长率依赖上一年收入任务。
3. 首期增长率因缺少上期为 `UNAVAILABLE`。
4. 缺失期间生成 `UNAVAILABLE` 任务。
5. 缺失字段生成 `UNAVAILABLE` 任务。
6. 无数据时不产生 `PENDING` 任务。
7. Plan validation 检测重复 ID 和未知依赖。
8. Plan metadata 保留 SemanticResolution。

### Runtime Integration

```bash
PYTHONPATH=. pytest tests/financial_agent/test_runtime_integration.py -q
```

```text
3 passed
```

覆盖：

1. 完整 Plan 通过 V9.3 Workflow 执行。
2. Verification 通过后生成 Evidence。
3. 每个 Task 保留 Calculation / Verification / Evidence 引用。
4. 首期缺上期数据映射为 `UNAVAILABLE`。
5. 汇总状态为 `PARTIAL`。
6. 非 `PENDING` Plan 被拒绝执行。

### 联合回归

```bash
PYTHONPATH=. pytest tests/financial tests/financial_agent -q
```

```text
165 passed
```

### 全量回归

```bash
PYTHONPATH=. pytest -q
```

```text
843 passed, 26 warnings, 4 subtests passed
```

## V9.3 兼容性

本阶段没有修改：

```text
MetricEngine 公式
VerificationEngine 逻辑
Formula Golden
Evidence 模型
Finding 模型
Report 生成逻辑
```

V10.2 只调用既有 V9.3 Workflow，不重复实现财务公式。

## 边界与不做事项

```text
❌ LLM Planner
❌ 自动重规划
❌ 多 Agent 框架
❌ Skill Evolution / GEPA
❌ 实时行情
❌ 估值 / DCF / PE / PB
❌ 自动投资建议
```

## 剩余事项

1. Insight Engine 仍未实现，属于 V10.3。
2. Plan Evaluation / Recovery 仍未实现，属于 V10.3。
3. Tool Registry 目前绑定的是 Runtime 集成测试验证过的执行器；后续可在 V10.3 将全部 Task 类型进一步显式绑定。
4. Evidence-backed Insight 与失败分类恢复策略待 V10.3 处理。

## 验收结论

```text
V10.2 状态：FINAL
Tool Registry 测试：8 / 8 passed
ExecutionPlan 测试：8 / 8 passed
Runtime Integration 测试：3 / 3 passed
Financial + Financial Agent：165 passed
Full regression：843 passed
```

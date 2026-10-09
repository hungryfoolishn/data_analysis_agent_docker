# Runtime V10.2.1 Runtime Hardening 实施验收

## 结论

V10.2.1 已完成。本阶段是小版本质量加固，不新增财务指标，不引入 LLM Planner、自动重规划、多 Agent 或 Skill Evolution。重点解决 V10.2 Runtime Integration 中的执行闭环、状态可靠性和结果唯一性问题。

## 修复内容

### 1. Tool Registry 真正接入 Runtime

`FinancialRuntimeExecutor` 现在通过 `FinancialToolExecutionRegistry.execute()` 调用批处理 V9.3 Workflow，而不是绕过注册表直接调用 Workflow。

执行工具 ID：

```text
financial_workflow_runtime
```

批处理调用只执行一次，避免把每个指标任务都展开成一次完整 Workflow 调用。

测试通过注入 Spy Registry 验证：

```text
调用次数 == 1
payload 包含 company_names / metrics / start_year / end_year
result.metadata.workflow_tool_id == financial_workflow_runtime
```

### 2. Workflow 异常统一收尾

执行器先把 Plan 标记为：

```text
RUNNING
```

Workflow 通过 Tool Registry 执行。如果 Workflow 抛出异常，Tool Registry 返回 `FAILED`，Runtime Executor 会立即收尾：

```text
plan.status = FAILED
plan.finished_at = ISO-8601 UTC
result.status = FAILED
result.metadata.error = workflow tool failed
result.metadata.tool_error = 原始异常信息
```

不会再把 Plan 遗留在 `RUNNING` 状态。

### 3. 执行前重新校验 Plan

Runtime Executor 在执行前调用：

```text
ExecutionPlanBuilder.validate(plan)
```

校验：

```text
任务数量
task_id 唯一
company/period/metric 唯一
依赖存在
无自依赖
无依赖循环
```

如果 Plan 非法：

```text
plan.status = FAILED
plan.finished_at = UTC
raise RuntimeIntegrationError
```

非法 Plan 不会进入 Workflow 执行。

### 4. 结果索引拒绝重复记录

Workflow 返回的以下结果都按：

```text
company_id / period / metric_id
```

建立唯一索引：

```text
Calculation
Verification
Observation
Evidence
```

如果同一键出现重复记录，不再静默保留第一条，而是输出：

```text
Duplicate calculation result for ...
Duplicate verification result for ...
Duplicate observation result for ...
Duplicate evidence result for ...
```

并将 Plan 收尾为：

```text
FAILED
```

### 5. 全部不可用与执行失败语义分离

新增：

```python
RuntimeExecutionStatus.BLOCKED
```

状态汇总规则：

| 执行情况 | Runtime 状态 | ExecutionPlan 状态 |
| --- | --- | --- |
| 全部任务成功 | `SUCCEEDED` | `SUCCEEDED` |
| 至少一个成功，其余不可用 | `PARTIAL` | `PARTIAL` |
| 全部任务不可用 | `BLOCKED` | `BLOCKED` |
| 存在执行失败 | `FAILED` | `FAILED` |

因此，数据缺失不再等同于执行失败。

## 回归测试

新增或扩展以下 Hardening 测试：

1. `test_runtime_executes_tool_through_registry`
2. `test_runtime_marks_plan_failed_when_workflow_raises`
3. `test_runtime_handles_all_tasks_unavailable`
4. `test_runtime_rejects_duplicate_workflow_results`
5. `test_runtime_rejects_invalid_execution_plan`
6. `test_runtime_preserves_evidence_refs_after_registry_execution`

测试确认：

1. Registry 中的工具确实被调用。
2. Workflow 异常后 Plan 不遗留 `RUNNING`。
3. 全部不可用时状态为 `BLOCKED`，不是 `FAILED`。
4. 重复 Calculation 被拒绝。
5. 非法 Plan 被拒绝。
6. 成功任务保留 Calculation / Verification / Evidence 三类引用。

## 测试结果

### Runtime Integration

```bash
PYTHONPATH=. pytest tests/financial_agent/test_runtime_integration.py -q
```

```text
9 passed
```

### Financial Agent 全量

```bash
PYTHONPATH=. pytest tests/financial_agent -q
```

```text
97 passed
```

### Financial + Financial Agent 联合回归

```bash
PYTHONPATH=. pytest tests/financial tests/financial_agent -q
```

```text
171 passed
```

### 全量回归

```bash
PYTHONPATH=. pytest -q
```

```text
849 passed, 26 warnings, 4 subtests passed
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

V9.3 Workflow 继续作为批处理计算底座被调用。

## 边界与不做事项

```text
❌ 每个指标任务单独触发完整 Workflow
❌ LLM Planner
❌ 自动重规划
❌ 多 Agent 框架
❌ Skill Evolution / GEPA
❌ 实时行情
❌ 估值 / DCF / PE / PB
❌ 自动投资建议
```

## 剩余事项

依赖图目前仍由批处理 Workflow 执行后回溯映射，尚未升级为 Ready Queue / DAG Scheduler。真正的依赖感知调度、失败策略传播和重试规则放到下一阶段处理。

## 验收结论

```text
V10.2.1 状态：FINAL
Runtime Hardening 测试：全部通过
Financial + Financial Agent：171 passed
Full regression：849 passed
```

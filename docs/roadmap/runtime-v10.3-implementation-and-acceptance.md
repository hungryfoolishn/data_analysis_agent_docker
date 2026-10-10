# Runtime V10.3 实施与验收记录

## 结论

V10.3.0 已完成第一阶段核心能力：在 V10.2 批量工作流契约稳定后，新增独立的确定性 DAG Scheduler。当前调度器已通过假执行器验证，可证明任务依赖、就绪判断、状态迁移、失败策略和失败传播的正确性。调度器异常边界已补强，非法执行器返回值和调度器内部异常都会返回结构化失败结果，不会遗留 `RUNNING` 状态。

本阶段没有接入真实财务计算，也没有实现重试、幂等、持久化或并发调度。

## 阶段 0：V10.2 收尾

### 目标

- 修复工作流真实异常信息被通用错误覆盖的问题。
- 保证 `RuntimeExecutionResult.metadata["error"]`、任务级 `status_reason` 和任务级 `error` 都保留真实异常。
- 保持 `plan.status`、`finished_at`、任务状态和结果计数一致。
- 保留 `UNAVAILABLE` 语义，不把数据不可用误判为执行失败。

### 代码变更

- `langgraph_langchain/runtime/financial_agent/runtime_integration.py`
  - 工具执行失败分支现在把 `tool_result.error` 传入 `_failed_result()`。
- `tests/financial_agent/test_runtime_integration.py`
  - 断言真实异常 `RuntimeError: workflow exploded` 透传到主错误和任务级结果。
  - 计数一致性断言扩展为包含 `blocked_count`。

## 阶段 1：V10.3.0 DAG Scheduler 核心

### 目标

- 新增独立调度器模块，不把调度逻辑继续堆积到 `runtime_integration.py`。
- 实现单进程、确定性、顺序执行的 DAG 调度。
- 用假执行器验证依赖驱动执行，不接入真实财务公式。

### 非目标

- 不实现任务级财务执行适配器。
- 不实现任务级重试、幂等标识或执行尝试记录。
- 不实现持久化、断点恢复或分布式 Worker Lease。
- 不实现并发调度。
- 不改变 V9.3 财务公式、验证逻辑或证据模型。

## 代码变更

### 1. 新增调度器

新增：

```text
langgraph_langchain/runtime/financial_agent/scheduler.py
```

核心类：

```text
DeterministicDAGScheduler
```

职责：

- 校验计划必须是 `PENDING`。
- 复用 `ExecutionPlanBuilder.validate()` 检查重复 ID、未知依赖、自依赖和依赖循环。
- 建立 `task_id -> task` 与 `task_id -> downstream_tasks` 索引。
- 按计划顺序选择就绪任务，保证执行顺序确定。
- 执行任务并更新终态。
- 将依赖失败或不可用传播为下游 `BLOCKED`。
- 汇总 `RuntimeExecutionResult`。

### 2. 任务执行契约

新增：

```text
TaskExecutionOutcome
TaskExecutor
SchedulerValidationError
```

任务执行器只允许返回：

```text
SUCCEEDED
FAILED
UNAVAILABLE
```

非法状态会被转换为结构化 `FAILED`，不会让计划遗留 `RUNNING`。

### 3. 状态语义

| 状态 | 含义 |
| --- | --- |
| `PENDING` | 尚未进入调度 |
| `READY` | 依赖已满足，可执行 |
| `RUNNING` | 正在执行 |
| `SUCCEEDED` | 执行成功 |
| `FAILED` | 执行失败 |
| `UNAVAILABLE` | 数据不可用，非执行失败 |
| `BLOCKED` | 上游依赖失败或不可用，任务未执行 |

`BLOCKED` 与“当前尚未就绪”区分：`BLOCKED` 是终态，表示依赖已无法满足；`PENDING` 表示仍在等待调度。

### 4. 失败传播

- 上游 `FAILED` 会导致直接和间接下游任务进入 `BLOCKED`。
- 上游 `UNAVAILABLE` 同样会导致下游任务进入 `BLOCKED`。
- 执行器异常会转换为 `FAILED`，并保留 `异常类型: 异常信息`。
- 执行器返回 `None`、`dict` 等非 `TaskExecutionOutcome` 值时，会转换为结构化 `FAILED`。
- 非法执行结果状态会转换为结构化 `FAILED`。
- 调度器内部异常会返回结构化失败结果，设置 `plan.finished_at`，并将计划置为 `FAILED`，不会遗留 `RUNNING`。

### 5. 失败策略契约

| 策略 | 调度语义 |
| --- | --- |
| `CONTINUE_WITH_WARNING` | 默认策略；独立任务继续执行，失败任务的下游进入 `BLOCKED`。 |
| `FAIL_FAST` | 失败后停止启动尚未执行的任务，剩余任务进入 `BLOCKED`，计划进入 `FAILED`。 |
| `REPLAN_OR_FAIL` | V10.3.0 不支持重新规划；失败时显式返回不支持重新规划的结构化失败，并阻断剩余任务。 |

失败策略只作用于执行失败 `FAILED`；`UNAVAILABLE` 仍表示数据不可用，不会被当作执行失败处理。

### 6. 结果计数

`RuntimeExecutionResult` 新增：

```text
blocked_count
```

计数一致性约束为：

```text
succeeded_count
+ unavailable_count
+ failed_count
+ blocked_count
== len(task_results)
```

## 调度器测试

新增：

```text
tests/financial_agent/test_scheduler.py
```

覆盖：

1. 多层依赖按拓扑顺序执行。
2. 多个独立任务都会被调度。
3. 上游失败后直接和间接下游都不执行。
4. 上游不可用后下游进入 `BLOCKED`。
5. 无就绪任务时返回结构化 `BLOCKED` 结果。
6. 执行器异常转换为 `FAILED`。
7. 执行器返回 `None` 或 `dict` 时转换为结构化 `FAILED`。
8. 非法执行结果状态转换为结构化 `FAILED`。
9. 调度器内部异常返回结构化失败且不遗留 `RUNNING`。
10. `FAIL_FAST` 停止独立任务。
11. `CONTINUE_WITH_WARNING` 继续独立任务。
12. `REPLAN_OR_FAIL` 显式返回不支持重新规划。
13. 依赖循环、未知依赖、非法计划状态在执行前拒绝。
14. 拒绝执行时不修改计划或任务状态。
15. 重复依赖声明不会导致重复执行或重复计数。
16. 所有任务不可用时返回 `BLOCKED` 汇总。
17. 同一任务不会重复执行或重复计数。

## 验收命令与结果

### V10.2 定向回归

```bash
PYTHONPATH=. pytest tests/financial_agent/test_runtime_integration.py -q
PYTHONPATH=. pytest tests/financial_agent/test_tool_execution.py -q
PYTHONPATH=. pytest tests/financial_agent/test_execution_plan.py -q
```

结果：

```text
12 passed
8 passed
10 passed
```

### V10.3.0 调度器测试

```bash
PYTHONPATH=. pytest tests/financial_agent/test_scheduler.py -q
```

结果：

```text
22 passed
```

### 阶段 0 + 阶段 1 联合定向回归

```bash
PYTHONPATH=. pytest \
  tests/financial_agent/test_scheduler.py \
  tests/financial_agent/test_runtime_integration.py \
  tests/financial_agent/test_tool_execution.py \
  tests/financial_agent/test_execution_plan.py \
  -q
```

结果：

```text
52 passed
```

### 全量回归

```bash
PYTHONPATH=. pytest -q
```

结果（`docker-host` / Conda `analysis`）：

```text
876 passed, 11 warnings, 4 subtests passed
```

## 增长率依赖与输入契约

增长率任务显式声明以下必要输入：

```text
current_base_metric
previous_base_metric
```

`growth_input_contract` 记录：

- 基础指标 ID。
- 当期。
- 上一期。
- 必要输入。
- 输入模式：`task_output_or_direct_data_source`。

当执行计划中存在当期或上一期基础指标任务时，增长率任务会建立对应依赖；当基础指标任务不在当前计划中时，V10.2/V10.3 批量工作流可以按输入契约直接从数据服务读取基础值。缺少任务输出不等于缺少数据；真正的上一期数据缺失仍由计划构建阶段标记为 `UNAVAILABLE`。

## 兼容性

- V9.3 批量工作流仍保留。
- `FinancialRuntimeExecutor` 仍作为旧执行路径。
- 未修改财务公式、验证逻辑或证据生成逻辑。
- 新增 `blocked_count` 为向后兼容字段，旧结果默认为 `0`。
- 增长率依赖补强不会把“基础指标任务不在计划中”误判为数据不可用。

## 剩余事项

- V10.3.1：将基础指标、派生指标、验证和证据拆成任务级执行适配器。
- V10.3.2：实现执行尝试、错误分类、有限重试和幂等保护。
- V10.4：实现运行记录、状态事件、持久化和断点恢复。

## 验收结论

```text
V10.3.0 状态：阶段 1 完成并完成异常边界补强
调度器核心测试：22 / 22 passed
阶段 0 + 阶段 1 定向回归：52 passed
全量回归：876 passed
```

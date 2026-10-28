# Runtime V10.3 实施与验收记录

## 结论

V10.3.1 已完成 Task-level Financial Execution Adapter：`FinancialRuntimeExecutor` 默认通过 `DeterministicDAGScheduler` 逐任务执行真实财务计算，基础指标输出会传给增长率等派生任务，任务级验证和证据生成复用既有 V9.3 引擎。旧批量工作流保留为 `execution_mode="workflow"`，可用于兼容和端到端对照。

本阶段没有实现重试、幂等、持久化或并发调度。

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


## 阶段 2：V10.3.1 Task-level Financial Execution Adapter

### 目标

- 将真实财务执行入口默认切换到 `DeterministicDAGScheduler`。
- 为每个 `ExecutionTask` 执行真实基础指标或派生指标计算。
- 建立成功任务输出的类型化存储和依赖输出传递机制。
- 增长率任务优先消费当期和上一期基础指标任务输出。
- 当基础指标任务不在计划中时，按输入契约直接读取数据服务。
- 复用既有 `compute_metric`、独立验证引擎和证据模型。
- 保留 V9.3 批量工作流作为 `execution_mode="workflow"` 对照路径。

### 非目标

- 不实现任务级重试、幂等标识或执行尝试记录。
- 不实现持久化、断点恢复或跨进程恢复。
- 不实现并发调度或分布式 Worker Lease。
- 不实现真正的失败后重新规划。
- 不重写财务公式、验证公式或证据数据模型。

## V10.3.1 代码变更

### 1. 任务级执行适配器

新增：

```text
langgraph_langchain/runtime/financial_agent/task_executor.py
```

核心类型：

```text
TaskExecutionOutput
TaskOutputStore
TaskLevelFinancialExecutor
```

`TaskLevelFinancialExecutor` 职责：

- 解析公司与期间。
- 读取当前期和必要上一期财务报表。
- 调用既有 `compute_metric` 计算基础指标。
- 对增长率任务优先解析依赖任务输出。
- 当依赖任务输出不存在时，按契约直接读取数据服务。
- 调用 `FinancialVerificationEngine` 做独立公式验证。
- 验证通过后生成 `FinancialEvidence` 和 `FinancialObservation`。
- 将计算、验证、证据和观察结果封装为 `TaskExecutionOutput`。
- 数据缺失返回 `UNAVAILABLE`；未知指标、无效计算或验证失败返回 `FAILED`。

### 2. 依赖输出契约

`TaskExecutionOutcome` 新增：

```text
output
```

`DeterministicDAGScheduler` 新增：

```text
on_outcome
```

成功任务执行后，`TaskOutputStore` 会记录：

```text
TaskExecutionOutput
```

下游任务通过依赖任务 ID 取得输出，只能消费已成功任务的值。调度器不会在依赖失败或不可用时启动下游任务，因此下游不会读取缺失、失败或过期的上游输出。

增长率任务输入来源记录在任务元数据：

```text
input_sources.current
input_sources.previous
```

取值可能为：

```text
dependency_task:<task_id>
data_service
```

当期和上一期基础任务都在计划中时，增长率任务会同时消费两个依赖输出；任一基础任务不在计划中时，对应输入会直接读取数据服务。

### 3. 真实执行入口

`FinancialRuntimeExecutor` 新增：

```text
execution_mode
```

默认值：

```text
dag
```

兼容值：

```text
workflow
```

- `dag`：逐任务调度真实财务执行。
- `workflow`：保留 V9.3 批量工作流，一条调用完成整份查询后映射回任务。

DAG 模式会聚合任务级输出并返回：

- `FinancialAnalysisResult`
- `calculations`
- `verifications`
- `evidence`
- `observations`
- `data_sources`
- `summary`
- `report_markdown`

### 4. 失败语义

- 基础指标验证失败：
  - 当前任务为 `FAILED`
  - 直接和间接下游增长率为 `BLOCKED`
  - 计划为 `FAILED`
- 上一期数据缺失：
  - 增长率任务为 `UNAVAILABLE`
  - 不会被误报为执行失败
- 未知指标：
  - 任务为 `FAILED`
  - 不会被误报为 `UNAVAILABLE`
- 成功结果缺少类型化输出：
  - 输出存储拒绝记录
  - 调度器异常边界保证结构化失败结果

## V10.3.1 测试

新增：

```text
tests/financial_agent/test_task_executor.py
```

覆盖：

1. 真实基础指标 → 增长率任务的依赖输出传递。
2. 2023 年增长率同时消费 2023 和 2022 基础指标任务输出。
3. DAG 与旧批量工作流的计算结果、公式和独立验证结果一致。
4. 基础任务不在计划中时，增长率直接读取数据服务。
5. 上一期数据缺失时返回 `UNAVAILABLE`。
6. 未知指标返回 `FAILED`。
7. 上游验证失败时，下游增长率进入 `BLOCKED`，不生成伪造计算结果。
8. 成功结果缺少 `TaskExecutionOutput` 时输出存储拒绝记录。
9. 旧 V9.3 工作流测试显式使用 `execution_mode="workflow"` 保持兼容。

## V10.3.1 验收结果

### 任务级执行测试

```bash
PYTHONPATH=. pytest tests/financial_agent/test_task_executor.py -q
```

结果：

```text
6 passed
```

### V10.3.1 联合定向回归

```bash
PYTHONPATH=. pytest \
  tests/financial_agent/test_task_executor.py \
  tests/financial_agent/test_scheduler.py \
  tests/financial_agent/test_execution_plan.py \
  tests/financial_agent/test_runtime_integration.py \
  tests/financial_agent/test_tool_execution.py \
  -q
```

结果：

```text
58 passed
```

## V10.3.1 验收结论

```text
V10.3.1 状态：Task-level Financial Execution Adapter 完成
真实财务入口：默认切换到 DAG Scheduler
基础指标 → 派生指标：使用真实任务输出传递
直接读取路径：按 growth_input_contract 从数据服务读取
新旧链路对照：计算结果、公式和验证结果一致
任务级执行测试：6 / 6 passed
联合定向回归：58 passed
```
## 阶段 3：V10.3.2 财务正确性与报告兼容性收尾

### 目标

- 复核并验收平均余额类指标的上一期资产负债表输入。
- 增长率直接读取数据服务时，显式传播基础指标计算状态。
- 建立全指标 DAG 与旧批量工作流对照矩阵。
- 恢复 DAG 模式的比较、风险、异常和 Finding 分析层。
- 保证 DAG 报告与旧 V9.3 工作流报告使用同一分析和报告构建逻辑。

### 非目标

- 不新增任务重试、幂等、持久化或并发调度。
- 不修改财务公式定义。
- 不改变 `UNAVAILABLE`、`FAILED`、`BLOCKED` 的既有语义。

## V10.3.2 代码变更

### 1. 平均余额输入契约与黄金值验收

复核 `TaskLevelFinancialExecutor` 后确认：非增长率任务调用 `compute_metric()` 时已传入：

```text
previous_balance=previous_balance
```

本阶段补充显式测试，并使用 2022/2023 两期明显不同的资产负债表数据：

```text
2022 total_assets=400, 2023 total_assets=500
2022 total_equity=200, 2023 total_equity=300
2022 accounts_receivable=10, 2023 accounts_receivable=30
2022 inventory=20, 2023 inventory=40
```

黄金值测试覆盖：

```text
ROE
ROA
应收账款周转率
存货周转率
总资产周转率
```

测试断言当期分母、上一期分母、平均分母、手工计算的预期结果和验证结果。例如 ROE 使用：

```text
net_profit = 25
average(total_equity_2022, total_equity_2023) = 250
expected ROE = 25 / 250 * 100 = 10
```

缺少上一期资产负债表时，平均余额指标保持 `UNAVAILABLE`，原因为：

```text
average_balance_previous_period_missing
```

### 2. 增长率直接读取状态传播

增长率任务没有依赖任务输出时，不再只读取 `compute_metric(...).value`，而是显式检查基础指标计算结果状态：

- `CALCULATED`：基础值进入增长率公式。
- `UNAVAILABLE`：增长率返回 `UNAVAILABLE`，保留基础指标缺失原因。
- `INVALID`：增长率返回 `FAILED`，保留基础指标无效原因。

示例错误语义：

```text
current_base_metric_unavailable:reported_value_missing
current_base_metric_invalid:reported_value_not_finite
previous_base_metric_unavailable:reported_value_missing
previous_base_metric_invalid:reported_value_not_finite
growth_previous_period_zero
```

测试覆盖当前期字段缺失、当前期非有限值、上一期字段缺失、上一期非有限值和上一期基础值为零。

### 3. 全指标回归矩阵与核心黄金值

新增测试遍历 `financial_metric_registry.list_metrics()` 中的全部当前支持指标，并对比：

- DAG 任务级计算结果
- 旧 V9.3 批量工作流计算结果

对照字段包括：

```text
result
formula
status
inputs
missing_fields
unit
verification.passed
verification.actual_value
```

该矩阵覆盖基础指标、增长率、利润率、平均余额指标、偿债能力、现金流和派生现金流指标。

同时补充少量独立黄金值测试，直接断言：

```text
收入增长率
毛利率
净利率
自由现金流
ROE
ROA
应收账款周转率
存货周转率
总资产周转率
```

这些黄金值用于避免 DAG 与旧工作流同时复用同一计算引擎时出现同源错误。

### 4. 共享分析与报告层及证据链校验

`FinancialAnalysisWorkflow` 新增：

```python
build_analysis_result(...)
```

该方法统一构建：

```text
comparisons
risk_signals
anomalies
findings
summary
report_markdown
```

旧批量工作流和 DAG 聚合路径现在复用同一方法。因此 DAG 模式不再向分析层传空列表，报告保留：

- 同业比较
- 核心发现
- 风险信号
- Evidence-backed Finding
- 异常信号对应的分析结论

测试还校验每个 Finding 的 Evidence 引用：

- Evidence 必须存在。
- Evidence 必须标记为 `verified`。
- Evidence 必须关联 Calculation。
- Evidence 必须关联 Verification。
- Verification 必须通过。
- Verification 的 Calculation 引用必须与 Evidence 一致。

`RuntimeExecutionResult.metadata` 同步记录：

```text
finding_count
comparison_count
risk_signal_count
anomaly_count
```


## V10.3.2.1 季度与半年度期间兼容性

### 问题

DAG 与旧批量工作流在生成 `FinancialQuery` 时都曾使用：

```python
years = [int(task.period) for task in plan.tasks]
```

该写法只支持 `2023`，不能解析 `2023Q1`、`2023Q4`、`2023H1`、`2023H2`。

### 修复

统一新增：

```python
_years_from_tasks(plan.tasks)
```

内部使用项目既有 `PeriodNormalizer.parse(task.period).year` 提取财务年度，不再把完整期间字符串直接转换为整数。

该修复同时覆盖：

- DAG 聚合路径 `_build_task_analysis_result`
- 旧批量工作流路径 `_execute_plan_with_workflow`

### 测试

新增季度与半年度聚合测试，覆盖：

```text
2023Q1
2023Q4
2023H1
2023H2
```

测试使用同一份多期间数据分别执行 DAG 与旧工作流，并断言：

- 任务执行成功。
- `FinancialQuery.start_year == 2023`。
- `FinancialQuery.end_year == 2023`。
- 四个目标期间的观察值正确。
- 旧工作流可额外计算计划外的 2023Q3 数据，不影响任务映射。
- 季度和半年度任务不会因期间字符串转换失败而中断。


## V10.3.2.2 季度与半年度增长率比较口径

### 问题

`FinancialPeriod.previous()` 原本只表达相邻期间逻辑：

```text
2023Q1 -> 2022Q4
2023Q2 -> 2023Q1
2023H1 -> 2022H2
2023H2 -> 2023H1
```

这适合环比，但不能自动等同于财务分析中常用的同比。若把所有增长率都交给 `previous()` 决定，会造成口径混淆。

### 修复

新增显式比较口径：

```text
sequential       环比
year_over_year   同比
```

默认口径为：

```text
year_over_year
```

因此：

```text
2023Q1 同比 -> 2022Q1
2023Q2 同比 -> 2022Q2
2023H1 同比 -> 2022H1
2023H2 同比 -> 2022H2
2023Q2 环比 -> 2023Q1
2023H2 环比 -> 2023H1
```

`FinancialDataService.previous_period()` 仍默认返回相邻期间，供通用数据和平均余额逻辑使用。增长率相关路径必须显式传入比较口径。

### 契约位置

`ExecutionPlanBuilder` 新增参数：

```python
growth_comparison_type
```

计划元数据会写入：

```text
growth_comparison_type
```

增长率任务输入也会写入同一字段。旧批量工作流通过 `FinancialQuery.growth_comparison_type` 使用同一契约。

### 报告标识

同比计算会标记：

```text
（同比）
```

环比计算会标记：

```text
（环比）
```

标记出现在 Calculation、Evidence 和 Observation 的 formula 字段中，避免报告中混用口径。

### 测试

新增测试覆盖：

1. 2023Q1 对 2022Q1 的同比增长率，DAG 与 workflow 双路径一致。
2. 2023Q2 对 2023Q1 的环比增长率，DAG 与 workflow 双路径一致。
3. 2023H1 对 2022H1 的半年度同比增长率。
4. 同比基期缺失时，增长率任务返回 `UNAVAILABLE`。


## V10.3.3 财务口径、累计值转换与审计边界

### 目标

- 将增长率比较口径与期间解析进一步解耦。
- 明确累计值和单期值的转换契约。
- 增加独立黄金值测试，避免 DAG 与旧工作流同源错误互相掩盖。
- 对 TTM 和非法期间返回结构化不可用，而不是运行时崩溃。
- 增加重复报表的数据质量边界测试。

### 新增契约

`FinancialQuery` 和执行计划新增：

```text
growth_period_basis
```

取值：

```text
cumulative      累计值，默认
single_period   单期值
```

结合增长率比较口径：

```text
year_over_year + cumulative   累计同比
year_over_year + single_period  单期同比（自动从累计值推导）
sequential     + single_period  单季环比（自动从累计值推导）
```

### 单期转换规则

对于 Q2/Q3/Q4/H2 等非首期累计数据：

```text
single_period_current = current_cumulative - previous_cumulative
```

Q1/H1/FY 是年度或累计期的首期，直接使用当前累计值作为单期值。

EPS 不会被简单相减推导，因为不同期间的加权股本可能不同。因此：

```text
EPS growth + single_period + 非首期 -> UNAVAILABLE
```

### 溯源

单期转换涉及的所有原始报表 source_id 都会并入 Calculation.source_ids。例如 2023Q2 单季收入同比增长会同时保留：

```text
income_2023Q2
income_2023Q1
income_2022Q2
income_2022Q1
```

### TTM 边界

TTM / LTM 增长请求返回：

```text
status = UNAVAILABLE
reason = unsupported_growth_period:ttm
```

不会伪造数值，也不会在计划执行过程中产生未处理异常。

### 数据质量边界

同一公司、同一规范期间存在重复收入报表时，`FinancialDataService` 初始化直接失败，避免冲突版本静默进入计算。

## V10.3.2 测试

### 任务级财务正确性测试

```bash
PYTHONPATH=. pytest tests/financial_agent/test_task_executor.py -q
```

实际结果：

```text
27 passed in 2.15s
```

### V10.3.2 联合定向回归

```bash
PYTHONPATH=. pytest \
  tests/financial_agent/test_task_executor.py \
  tests/financial_agent/test_scheduler.py \
  tests/financial_agent/test_execution_plan.py \
  tests/financial_agent/test_runtime_integration.py \
  tests/financial_agent/test_tool_execution.py \
  -q
```

实际结果：

```text
79 passed in 2.74s
```

### V9.3 工作流兼容性回归

```bash
PYTHONPATH=. pytest \
  tests/financial/test_runtime_v9_semantics.py \
  tests/financial/test_runtime_v9_period_hardening.py \
  tests/financial/test_runtime_v9.py \
  tests/financial/test_runtime_v9_hardening.py \
  tests/financial/test_runtime_v9_golden.py \
  -q
```

实际结果：

```text
60 passed in 3.34s
```

### 全量回归

```bash
PYTHONPATH=. pytest -q
```

实际结果：

```text
903 passed, 11 warnings, 4 subtests passed in 223.16s (0:03:43)
```

## V10.3.2 验收结论

```text
V10.3.2 状态：财务正确性与报告兼容性收尾完成
平均余额输入：previous_balance 已传入并有全指标矩阵验收
增长率状态传播：CALCULATED / UNAVAILABLE / INVALID 显式区分
全指标对照：DAG 与旧工作流结果、公式、输入、验证一致
分析报告层：DAG 复用旧工作流共享构建逻辑
任务级测试：27 / 27 passed
联合定向回归：79 passed
V9.3 工作流回归：60 passed
全量回归：903 passed
```

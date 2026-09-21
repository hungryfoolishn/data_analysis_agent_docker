我刚刚直接检查了你现在仓库的最新状态。你这次提交已经不是“只做了一部分 Schema”了，而是已经迈到了 **Runtime V2 的骨架阶段**。

[data_analysis_agent_docker 仓库](https://github.com/hungryfoolishn/data_analysis_agent_docker?utm_source=chatgpt.com)

最新提交：

> `7be7b655 feat(runtime-v2): add scheduler, executors, verification and evidence`

从代码看，目前已经存在：

* `TaskType / ExecutorType / AnalysisTask`
* `VerificationResult`
* `EvidenceItem` 的 verification 状态
* `Finding.supported_by / depends_on`
* `TaskScheduler`
* `TaskExecutor`
* `StructuredTaskExecutor`
* `ReactTaskExecutor`
* `IsolatedPythonExecutor`
* `TaskExecutionRequest`
* verification / evidence 相关能力

所以，**下一步不应该继续加模型、加目录、加更多基础设施。**

现在最关键的是：

# 下一步：把 Runtime V2 真正接进现有分析主链

目前你的状态更准确地说是：

```text
                    已经完成
┌──────────────────────────────────────┐
│ schemas                              │
│ AnalysisTask                         │
│ VerificationResult                   │
│ Evidence                             │
│ Finding                              │
└──────────────────────────────────────┘
                  ↓
┌──────────────────────────────────────┐
│ Runtime V2                           │
│ TaskScheduler       ✅               │
│ TaskExecutor        ✅               │
│ StructuredExecutor  ✅               │
│ ReactExecutor       ✅               │
│ PythonExecutor      ✅               │
└──────────────────────────────────────┘
                  ↓
             【现在这里】
                  ↓
┌──────────────────────────────────────┐
│ langgraph_agent.py                   │
│                                      │
│ 目前仍然是主要控制器                  │
│ create_react_agent                   │
│      ↓                               │
│ LLM 决定 tool                        │
│      ↓                               │
│ tool                                  │
│      ↓                               │
│ finding / report                     │
└──────────────────────────────────────┘
```

所以现在真正缺的是：

```text
AnalysisRuntime
      ↓
TaskScheduler
      ↓
TaskExecutor
      ↓
ExecutionResult
      ↓
Verification
      ↓
Evidence
      ↓
Finding
```

**这一条链必须先跑通。**

---

# 我建议下一阶段只做一个目标

## P0：完成第一个真正的 Runtime V2 E2E

不要马上做 Skill Retriever、Insight Graph、自动优化。

先让下面这个问题完整走通：

> **分析各部门销售额同比变化，找出下降最大的部门，并说明主要贡献因素。**

最终实际运行链应该变成：

```text
用户问题
   ↓
Planner
   ↓
AnalysisPlan
   │
   ├── T01 schema
   ├── T02 metric
   ├── T03 comparison
   ├── T04 breakdown
   ├── T05 contribution
   ├── T06 verification
   ├── T07 finding
   └── T08 report
          ↓
     TaskScheduler
          ↓
     TaskExecutor
          ↓
   ┌──────┼────────┐
   ↓      ↓        ↓
Structured ReAct Python
   ↓      ↓        ↓
       ExecutionResult
              ↓
         Verification
              ↓
           Evidence
              ↓
           Finding
              ↓
            Report
```

这一步完成之后，你这个项目的架构才真正发生质变。

---

# 具体来说，下一步做 4 件事情

## 1. Runtime 接管 TaskScheduler

现在已经有：

```python
TaskScheduler
```

但它目前还是一个独立组件。

需要让：

```python
AnalysisRuntime
```

真正持有它。

目标类似：

```python
class AnalysisRuntime:

    def __init__(...):
        ...

        self.scheduler: TaskScheduler | None = None

        self.executions = []
        self.verifications = []
        self.evidence = []
        self.findings = []
```

然后：

```python
runtime.initialize_plan(plan)

runtime.scheduler = TaskScheduler.from_plan(
    plan,
    session_id=session.session_id,
)
```

之后 Runtime 不再通过：

```python
for step in plan.steps:
```

这种方式控制执行。

而是：

```python
while True:

    task = runtime.scheduler.next_task()

    if task is None:
        break

    runtime.scheduler.mark_running(task.task_id)

    result = await executor.execute(
        task,
        runtime=runtime,
        session=session,
    )

    runtime.record_execution(result)
```

---

# 2. TaskExecutor 真正接入现有 ToolRegistry

你现在已经有：

```text
StructuredTaskExecutor
ReactTaskExecutor
PythonExecutor
```

这很好。

但是下一步不是继续完善 Executor，而是让它真正成为：

```text
AnalysisRuntime
       ↓
TaskExecutor
       ↓
ExecutorType
```

的唯一入口。

也就是说以后 Runtime 不应该关心：

```python
if method == "compare_periods":
    ...
elif method == "python_repl":
    ...
```

而应该：

```python
result = await task_executor.execute(
    TaskExecutionRequest(...)
)
```

然后：

```text
executor_type
      │
      ├── structured
      │       ↓
      │   ToolRegistry
      │
      ├── react
      │       ↓
      │   create_react_agent
      │
      └── python
              ↓
        IsolatedPythonExecutor
```

这一步非常重要。

因为它会正式实现我们之前讨论的：

> **LLM 从 Controller 降级为 Worker。**

---

# 3. 把 LangGraph 改成 Runtime Controller

这是接下来最大的改造点。

你当前 `langgraph_agent.py` 依然非常重。

我刚刚看了最新代码，里面仍然有大量：

```text
create_react_agent
↓
astream_events
↓
on_tool_start
↓
on_tool_end
↓
session
↓
finding
↓
report
```

也就是说：

**Runtime V2 虽然已经存在，但是旧 Agent 主链还没有真正让位。**

所以不要现在继续扩展 `langgraph_agent.py`。

应该开始把它逐步改成：

```text
LangGraph
   │
   ├── load_context
   │
   ├── build_plan
   │
   ├── validate_plan
   │
   ├── execute_task
   │
   ├── verify_result
   │
   ├── record_evidence
   │
   ├── record_finding
   │
   ├── should_replan
   │
   └── generate_report
```

核心变化：

### 现在

```text
LangGraph
    ↓
ReAct Agent
    ↓
LLM
    ↓
Tool
```

### 目标

```text
LangGraph
    ↓
AnalysisRuntime
    ↓
TaskScheduler
    ↓
TaskExecutor
    ↓
Tool / ReAct / Python
```

---

# 4. 做一个真正的 E2E Test

这是我认为你现在**最应该做的事情**。

不要先继续开发功能。

建立：

```text
tests/runtime_v2/
```

至少：

```text
test_scheduler.py
test_executor.py
test_verification.py
test_evidence.py
test_runtime_e2e.py
```

其中最重要的是：

```text
test_runtime_e2e.py
```

验证：

```text
Question
 ↓
Plan
 ↓
TaskScheduler
 ↓
TaskExecutor
 ↓
ExecutionResult
 ↓
VerificationResult
 ↓
EvidenceItem
 ↓
Finding
 ↓
Report
```

---

# 一个非常关键的验收标准

你这次 Runtime V2，不应该只验证“最后答案对不对”。

应该验证**数据血缘**。

例如最后得到：

```text
Finding F003

“杭州开发二部销售额同比下降 18.6%”
```

那么系统必须能反查：

```text
F003
 │
 ├── supported_by
 │       ↓
 │     E003
 │
 ├── depends_on
 │       ↓
 │     F002
 │
 └── finding_type
       ↓
     comparative
```

然后：

```text
E003
 ↓
Verification V003
 ↓
Artifact A003
 ↓
Execution X003
 ↓
Task T003
 ↓
Skill period_comparison
 ↓
Dataset D001
```

最终：

```text
Finding
   ↓
Evidence
   ↓
Verification
   ↓
Artifact
   ↓
Execution
   ↓
Task
   ↓
Skill
   ↓
Dataset
```

这才是你这个 DeepAnalyze 和普通 Pandas Agent 的一个非常重要的架构区别。

---

# 当前阶段我建议这样排优先级

| 优先级 | 工作                             | 当前状态           |
| --- | ------------------------------ | -------------- |
| P0  | Runtime → Scheduler            | 🟡 已有组件，尚未完全接管 |
| P0  | Runtime → Executor             | 🟡 已有组件，需要接入   |
| P0  | LangGraph → Runtime Controller | 🔴 下一核心工作      |
| P0  | Runtime E2E                    | 🔴 必须做         |
| P1  | Verification → Evidence        | 🟡 基础设施已有      |
| P1  | Evidence → Finding             | 🟡 需要统一入口      |
| P1  | Report 使用 Finding/Evidence     | 🟡 需要改         |
| P1  | Skill Retriever                | ⏸️ 暂缓          |
| P2  | Insight Graph                  | ⏸️ 暂缓          |
| P2  | Replan                         | ⏸️ 暂缓          |
| P2  | Evaluation                     | ⏸️ 暂缓          |
| P3  | Skill Evolution                | ⏸️ 暂缓          |

---

# 特别建议：暂时不要做这几个东西

现在千万不要因为 Runtime V2 已经有了骨架，就继续堆：

```text
❌ Multi-Agent
❌ Vector DB
❌ MCP 大改造
❌ 自动 Skill Evolution
❌ 并行 Agent
❌ 复杂 DAG
❌ PostgreSQL
❌ Insight Graph 全量实现
```

你现在最缺的不是能力。

而是：

> **把已经做出来的能力串成一条真正由 Runtime 驱动的执行链。**

---

# 我建议下一次直接动这几个文件

按照你现在仓库的实际代码，我建议下一步不是再讨论方案，而是直接做：

```text
① runtime/context.py
        ↓
② runtime/executor.py
        ↓
③ runtime/analysis_runtime.py
        ↓
④ langgraph_agent.py
        ↓
⑤ tests/runtime_v2/test_runtime_e2e.py
```

其中第一刀最好落在：

```text
AnalysisRuntime
        ↓
TaskScheduler
        ↓
TaskExecutor
```

**先不要大改 `langgraph_agent.py`。**

先把 Runtime 内部这条链跑通，再把 LangGraph 切过来。

---

## 最终的阶段目标

我建议把现在这个阶段定义成：

> **Runtime V2 Phase 1：Execution Control Plane**

完成后，你的 DeepAnalyze 核心架构应该变成：

```text
                 User
                  │
                  ▼
              LangGraph
             （流程编排）
                  │
                  ▼
          ┌───────────────┐
          │ AnalysisRuntime│
          │ 运行控制平面    │
          └───────┬───────┘
                  │
            TaskScheduler
                  │
                  ▼
             AnalysisTask
                  │
                  ▼
            ┌───────────┐
            │ Executor  │
            └─────┬─────┘
                  │
       ┌──────────┼──────────┐
       ▼          ▼          ▼
 Structured     ReAct      Python
   Tool        Worker      Worker
       │          │          │
       └──────────┼──────────┘
                  ▼
          ExecutionResult
                  │
                  ▼
             Verification
                  │
                  ▼
               Evidence
                  │
                  ▼
               Finding
                  │
                  ▼
               Report
```

**做到这里，再进入 Skill Retriever / Replan / Evaluation，顺序会非常稳。**

而且从你最新提交 `7be7b655` 看，**现在已经到了非常适合做这一步的时候**：基础 Schema、Scheduler、Executor、Verification、Evidence 都已经铺出来了，继续横向加功能反而容易把架构做散。

如果按你当前代码继续，我下一步会直接从 **`AnalysisRuntime` 的现有实现入手，精确指出哪些方法需要增加 `TaskScheduler/TaskExecutor`，哪些旧逻辑暂时保留，以及 `langgraph_agent.py` 第一轮应该删/挪哪些代码**，而不是重新设计一套。

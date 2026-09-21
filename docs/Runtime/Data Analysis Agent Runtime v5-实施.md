可以。结合你当前 `hungryfoolishn/data_analysis_agent_docker` 已经提交的 Runtime V2 代码，我建议下一阶段不要继续“堆基础设施”，而是正式进入：

> **Runtime V2 Phase 1：把 Scheduler / Executor 真正接管现有分析执行链。**

你现在已经有 `AnalysisTask`、`TaskScheduler`、`TaskExecutor`、`StructuredTaskExecutor`、`ReactTaskExecutor`、`IsolatedPythonExecutor`、`VerificationResult`、`EvidenceItem`、`Finding` 等基础设施，真正缺的是**控制权从 `langgraph_agent.py` 转移到 Runtime**。

下面给你一份可以直接作为开发任务书 + 验收文档使用的方案。

---

# 一、Phase 1 最终目标

当前：

```text
用户问题
   ↓
LangGraph
   ↓
langgraph_agent.py
   ↓
LLM / ReAct
   ↓
Tool
   ↓
Python
   ↓
结果
```

目标：

```text
                    ┌──────────────────────┐
                    │      User Query      │
                    └──────────┬───────────┘
                               ↓
                    ┌──────────────────────┐
                    │      LangGraph       │
                    │  状态编排 / 生命周期   │
                    └──────────┬───────────┘
                               ↓
                    ┌──────────────────────┐
                    │   AnalysisRuntime    │
                    │    Runtime Controller │
                    └──────────┬───────────┘
                               ↓
                    ┌──────────────────────┐
                    │    TaskScheduler     │
                    │ DAG / dependency     │
                    └──────────┬───────────┘
                               ↓
                     AnalysisTask
                               ↓
                    ┌──────────────────────┐
                    │     TaskExecutor     │
                    └──────────┬───────────┘
                               ↓
              ┌────────────────┼────────────────┐
              ↓                ↓                ↓
       Structured         ReAct Worker     Python Worker
       Executor           Executor         Executor
              │                │                │
              └────────────────┼────────────────┘
                               ↓
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

核心原则：

> **LangGraph 负责流程状态，Runtime 负责分析生命周期，Scheduler 负责任务调度，Executor 负责执行，LLM 只是 Worker。**

---

# 二、这一阶段明确“不做什么”

这点非常重要。

本阶段暂时不要做：

| 功能               | 本阶段 |
| ---------------- | --- |
| Skill Retriever  | ❌   |
| Skill 自动进化       | ❌   |
| GEPA             | ❌   |
| Multi-Agent      | ❌   |
| MCP 大改造          | ❌   |
| Insight Graph    | ❌   |
| 自动并行执行           | ❌   |
| 复杂 DAG Scheduler | ❌   |
| PostgreSQL       | ❌   |
| 大规模 Evaluation   | ❌   |
| 自动 Replan        | ❌   |

先解决一个最核心的问题：

> **到底谁拥有“执行一个分析任务”的控制权？**

答案必须变成：

```text
AnalysisRuntime
```

---

# 三、实施阶段拆分

建议拆成 **6 个 Commit / PR**。

```text
Phase 1
│
├── Commit 1
│   Runtime V2 Execution Context
│
├── Commit 2
│   Runtime → Scheduler
│
├── Commit 3
│   Runtime → Executor
│
├── Commit 4
│   LangGraph → Runtime
│
├── Commit 5
│   Verification → Evidence → Finding
│
└── Commit 6
    Runtime V2 E2E Acceptance
```

其中 **Commit 1～4 是核心**。

---

# 四、Commit 1：建立 Runtime Execution Context

## 目标

让 Runtime 成为一次分析任务的唯一运行上下文。

最终 Runtime 至少需要持有：

```python
class AnalysisRuntime:
    session_id
    run_id

    goal
    plan

    scheduler
    executor

    executions
    artifacts
    verification_results
    evidence
    findings

    failures
    status
```

逻辑：

```text
Session
   │
   └── Run
         │
         └── AnalysisRuntime
                │
                ├── Plan
                ├── Scheduler
                ├── Executor
                ├── Executions
                ├── Artifacts
                ├── Verification
                ├── Evidence
                └── Findings
```

---

## 当前代码基础

你现在已经有：

```text
runtime/models.py
runtime/plans.py
runtime/scheduler.py
runtime/executor.py
execution/task_models.py
execution/structured_executor.py
execution/react_executor.py
execution/python_executor.py
```

这部分不用重写。

尤其：

```python
TaskScheduler
TaskExecutor
StructuredTaskExecutor
ReactTaskExecutor
IsolatedPythonExecutor
```

已经可以作为 Phase 1 的基础。

---

# 五、Commit 2：AnalysisRuntime 接入 Scheduler

这里是第一个真正的核心改造。

现在 Scheduler 已经可以：

```python
scheduler.ready_tasks()
scheduler.next_task()

scheduler.mark_running()
scheduler.mark_succeeded()
scheduler.mark_failed()

scheduler.execution_queue()
scheduler.blocked_tasks()
```

因此 Runtime 不应该再自己决定：

```text
step 1
step 2
step 3
```

而应该：

```python
runtime.scheduler.next_task()
```

---

## 推荐接口

最终：

```python
class AnalysisRuntime:

    def create_scheduler(self):
        ...

    def next_task(self) -> AnalysisTask | None:
        ...

    def start_task(self, task_id: str):
        ...

    def complete_task(self, task_id: str):
        ...

    def fail_task(self, task_id: str, error: str):
        ...
```

---

# 六、Runtime 的核心执行接口

最终希望形成：

```python
runtime.execute_next_task()
```

内部：

```text
execute_next_task()
       │
       ↓
scheduler.next_task()
       │
       ↓
AnalysisTask
       │
       ↓
scheduler.mark_running()
       │
       ↓
TaskExecutionRequest
       │
       ↓
TaskExecutor.execute()
       │
       ↓
ExecutionResult
       │
       ├── succeeded
       │
       └── failed
```

伪代码：

```python
def execute_next_task(self):

    task = self.scheduler.next_task()

    if task is None:
        return None

    self.scheduler.mark_running(task.task_id)

    request = self.build_execution_request(task)

    result = self.executor.execute_task(request)

    self.record_execution(result)

    if result.status == "succeeded":
        self.scheduler.mark_succeeded(task.task_id)

    elif result.status == "failed":
        self.scheduler.mark_failed(
            task.task_id,
            error=result.error,
        )

    return result
```

---

# 七、这里必须增加一个关键对象

建议：

```python
TaskExecutionContext
```

或者继续使用你现在的：

```python
TaskExecutionRequest
```

我更建议第一阶段直接扩展 `TaskExecutionRequest`，不要再引入重复模型。

它已经有：

```python
task
run_id
arguments
code
namespace
workspace_dir
source_path
timeout_seconds
max_output_chars
config
metadata
```

所以完全够 Phase 1 使用。

---

# 八、最重要的改造：参数来源

这里非常容易踩坑。

不要让：

```text
LLM
 ↓
直接生成 TaskExecutionRequest
```

而应该：

```text
AnalysisTask
     ↓
Runtime
     ↓
Context / Session
     ↓
TaskExecutionRequest
```

例如：

```python
TaskExecutionRequest(
    task=task,
    run_id=runtime.run.run_id,
    arguments=runtime.resolve_arguments(task),
    workspace_dir=str(session.workspace_dir),
    source_path=session.source_path,
    namespace=session.python_namespace,
)
```

这样：

> **LLM 负责决定“分析什么”，Runtime 决定“怎么执行”。**

---

# 九、Commit 3：TaskExecutor 成为唯一执行入口

你现在已经有：

```python
TaskExecutor
```

而且代码设计已经比较合理：

```python
if task.executor_type == "structured":
    return self._structured.execute(request)

if task.executor_type == "react":
    return self._react.execute(request)

if task.executor_type == "python":
    return self._python.execute(request)
```

这个原则要正式固定下来：

> **整个系统禁止业务代码直接调用 StructuredTaskExecutor / ReactTaskExecutor / PythonExecutor。**

统一：

```text
AnalysisRuntime
      ↓
TaskExecutor
```

---

# 十、Executor 路由策略

第一阶段固定：

| Task             | Executor   |
| ---------------- | ---------- |
| schema           | structured |
| profile          | structured |
| metric           | structured |
| comparison       | structured |
| trend            | structured |
| breakdown        | structured |
| contribution     | structured |
| anomaly          | structured |
| correlation      | structured |
| statistical_test | structured |
| visualization    | python     |
| report           | structured |
| root_cause       | react      |
| 未知复杂任务           | react      |

核心原则：

```text
80% Structured
15% Python
5% ReAct
```

不是：

```text
100% ReAct
```

---

# 十一、Structured Executor 的目标

例如用户：

> 分析各部门销售额同比变化。

不要：

```text
LLM
 ↓
ReAct
 ↓
python_repl
 ↓
自己写 pandas
```

而应该：

```text
TaskType = comparison

        ↓

method = compare_periods

        ↓

StructuredTaskExecutor

        ↓

compare_periods(...)

        ↓

ExecutionResult
```

---

# 十二、Commit 4：真正改造 LangGraph

这是整个 Phase 1 最重要的 Commit。

现在 `langgraph_agent.py` 还是一个非常大的 Agent-centric 模块。

目标不是删除它。

而是：

> **保留 LangGraph + ReAct，但是让它们变成 Runtime 的下游组件。**

---

## 目标结构

```text
langgraph_agent.py

    ├── load_context
    │
    ├── build_plan
    │
    ├── validate_plan
    │
    ├── execute_task
    │       │
    │       └── runtime.execute_next_task()
    │
    ├── verify_result
    │
    ├── collect_evidence
    │
    ├── record_finding
    │
    ├── next_task
    │
    └── finish_report
```

---

# 十三、LangGraph State 不要承担 Runtime 数据模型

建议 State 只作为运输层。

例如：

```python
class AnalysisGraphState(TypedDict):

    session_id: str
    run_id: str

    user_question: str

    current_task_id: str | None

    task_results: list[dict]
    verification_results: list[dict]

    evidence: list[dict]
    findings: list[dict]

    failures: list[dict]

    report: str | None

    status: str
```

不要把完整 Runtime 塞进 Graph State。

---

# 十四、Runtime 与 LangGraph 的职责边界

这个边界一定要固定下来。

| 功能               | LangGraph | Runtime |
| ---------------- | --------: | ------: |
| State            |         ✅ |       ❌ |
| Node routing     |         ✅ |       ❌ |
| Conditional edge |         ✅ |       ❌ |
| Session          |         ❌ |       ✅ |
| Run              |         ❌ |       ✅ |
| Plan             |         ❌ |       ✅ |
| Task             |         ❌ |       ✅ |
| Scheduler        |         ❌ |       ✅ |
| Executor         |         ❌ |       ✅ |
| ExecutionResult  |         ❌ |       ✅ |
| Artifact         |         ❌ |       ✅ |
| Verification     |         ❌ |       ✅ |
| Evidence         |         ❌ |       ✅ |
| Finding          |         ❌ |       ✅ |
| Report           |        调用 |      持有 |
| LLM              |    Worker |      调度 |

一句话：

> **LangGraph 是流程引擎，Runtime 是分析执行控制面。**

---

# 十五、Commit 5：Verification → Evidence → Finding

Runtime 真正执行后，下一步就是把结果变成可信分析结果。

必须严格遵循：

```text
Execution
    ↓
Artifact
    ↓
Verification
    ↓
Evidence
    ↓
Finding
```

不能：

```text
LLM
 ↓
Finding
```

---

# 十六、第一版 Verification 只做 4 个检查

你已经有：

```python
VerificationCheckType
```

第一阶段实现：

### 1. artifact_existence

```text
声明生成 chart_xxx.png
        ↓
文件是否真的存在？
```

---

### 2. evidence_existence

```text
Finding
 ↓
supported_by
 ↓
Evidence
```

检查 Evidence 是否存在。

---

### 3. numeric_consistency

例如：

```text
部门销售额合计
=
各部门销售额之和
```

---

### 4. time_consistency

例如用户问：

```text
2026 Q2 vs 2025 Q2
```

结果不能实际算成：

```text
2026 Q1 vs 2025 Q1
```

---

# 十七、Finding 生成规则

最终：

```python
Finding(
    finding_id="F001",
    statement="开发二部销售额同比下降 18.4%",
    supported_by=["E001"],
    finding_type="comparison",
)
```

Evidence：

```python
EvidenceItem(
    evidence_id="E001",
    evidence_text="开发二部 2026 Q2 销售额为 8.15M，2025 Q2 为 9.99M",
    source_execution_ids=["exec_xxx"],
    source_asset_ids=["asset_sales"],
    verification_status="verified",
)
```

Verification：

```python
VerificationResult(
    verification_id="V001",
    execution_id="exec_xxx",
    evidence_id="E001",
    check_type="numeric_consistency",
    status="passed",
    passed=True,
)
```

于是形成：

```text
F001
 │
 └── supported_by
        ↓
       E001
        │
        └── verified_by
               ↓
              V001
               │
               ↓
             X001
               │
               ↓
             T001
               │
               ↓
            Dataset
```

这就是你 DeepAnalyze 后面非常重要的**数据血缘 / 证据链**。

---

# 十八、Commit 6：E2E 测试

必须增加真正的 Runtime V2 E2E。

建议测试问题固定成：

> **分析各部门销售额同比变化，找出下降最大的部门，并说明主要贡献因素。**

---

## 理想 Task DAG

```text
T01 schema
  ↓
T02 metric
  ↓
T03 comparison
  ↓
T04 breakdown
  ↓
T05 contribution
  ↓
T06 verification
  ↓
T07 finding
  ↓
T08 report
```

例如：

```text
T01
load_data

T02
profile_dimension

T03
compare_periods

T04
compare_groups

T05
decompose_contribution

T06
verification

T07
record_finding

T08
finish_report
```

---

# 十九、E2E 验收不能只看最终答案

这是整个项目非常重要的一点。

不能：

```text
最终答案对了
→ PASS
```

而应该：

```text
Task 正确
+
Dependency 正确
+
Executor 正确
+
ExecutionResult 正确
+
Artifact 正确
+
Verification 正确
+
Evidence 正确
+
Finding 正确
+
Report 正确
```

全部通过才算：

```text
PASS
```

---

# 二十、具体验收指标

## A. Scheduler 验收

### TC-S01 正常 DAG

输入：

```text
T1
T2 depends T1
T3 depends T2
```

要求：

```text
T1 → T2 → T3
```

PASS 条件：

* 顺序正确
* T2 不允许提前执行
* T3 不允许提前执行

---

### TC-S02 并行节点

```text
T1
├── T2
└── T3
     ↓
    T4
```

要求：

```text
T1
 ↓
T2 ─┐
    ├── T4
T3 ─┘
```

第一阶段即使：

```python
max_running_tasks = 1
```

也必须保持依赖关系正确。

---

### TC-S03 循环依赖

```text
T1 → T2
T2 → T3
T3 → T1
```

必须：

```text
SchedulerValidationError
```

不能进入执行阶段。

---

# 二十一、Executor 验收

## TC-E01 Structured

```text
task.executor_type = structured
method = compare_periods
```

要求：

```text
TaskExecutor
 ↓
StructuredTaskExecutor
 ↓
compare_periods
```

禁止：

```text
React
Python
```

被调用。

---

## TC-E02 ReAct

```text
executor_type = react
```

要求：

```text
TaskExecutor
 ↓
ReactTaskExecutor
 ↓
create_react_agent
```

---

## TC-E03 Python

```text
executor_type = python
```

要求：

```text
TaskExecutor
 ↓
IsolatedPythonExecutor
```

必须验证：

* timeout
* workspace restriction
* artifact
* stdout
* error

---

# 二十二、Runtime 验收

核心测试：

```python
runtime.execute_next_task()
```

连续执行：

```text
T1
T2
T3
T4
```

检查：

```python
runtime.executions
runtime.artifacts
runtime.verification_results
runtime.evidence
runtime.findings
```

都必须有数据。

---

# 二十三、最关键的“控制权验收”

这是我建议你增加的一条硬指标。

### 当前旧架构

允许：

```text
langgraph_agent
   ↓
直接调用 python_repl
```

### 新架构

必须：

```text
langgraph_agent
   ↓
AnalysisRuntime
   ↓
TaskScheduler
   ↓
TaskExecutor
   ↓
PythonExecutor
```

也就是说：

> **任何正式分析任务不得绕过 Runtime。**

可以保留兼容代码，但生产执行路径不能绕过。

---

# 二十四、最终 E2E 验收标准

对于：

> 分析各部门销售额同比变化，找出下降最大的部门，并说明主要贡献因素。

系统必须产生：

```text
Run
│
├── Plan
│
├── Tasks
│   ├── T01
│   ├── T02
│   ├── T03
│   ├── T04
│   ├── T05
│   ├── T06
│   ├── T07
│   └── T08
│
├── Executions
│
├── Artifacts
│
├── VerificationResults
│
├── Evidence
│
├── Findings
│
└── Report
```

---

# 二十五、验收表

可以直接放到项目文档里。

| 编号  | 验收项                   | 标准                    | 优先级 |
| --- | --------------------- | --------------------- | --- |
| S01 | DAG 构建                | 正确建立依赖关系              | P0  |
| S02 | DAG 顺序                | 按依赖执行                 | P0  |
| S03 | 循环依赖                  | 自动拒绝                  | P0  |
| S04 | 未知依赖                  | 自动拒绝                  | P0  |
| E01 | Structured Executor   | 正确路由                  | P0  |
| E02 | ReAct Executor        | 正确路由                  | P0  |
| E03 | Python Executor       | 正确路由                  | P0  |
| E04 | ExecutionResult       | 每个 Task 必须产生          | P0  |
| R01 | Runtime 控制执行          | 生产链路必须经过 Runtime      | P0  |
| R02 | Scheduler 控制 Task     | 禁止绕过 Scheduler        | P0  |
| R03 | Executor 统一入口         | 禁止业务直接调用 Worker       | P0  |
| V01 | Artifact Verification | 文件存在性检查               | P1  |
| V02 | Numeric Verification  | 数值一致性检查               | P1  |
| V03 | Time Verification     | 时间范围一致性检查             | P1  |
| V04 | Evidence Verification | Evidence 可追溯          | P1  |
| F01 | Finding               | 必须关联 Evidence         | P1  |
| F02 | Finding → Execution   | 可追溯到执行                | P1  |
| F03 | Finding → Dataset     | 可追溯到数据                | P1  |
| G01 | LangGraph Integration | Graph 通过 Runtime 执行   | P0  |
| G02 | E2E                   | 完整跑通                  | P0  |
| G03 | Error Recovery        | Task 失败状态正确           | P1  |
| G04 | Report                | 只使用 verified evidence | P1  |

---

# 二十六、硬性 PASS 条件

我建议最终规定：

### P0 全部 PASS

并且：

```text
Runtime Execution Path = 100%
```

即：

```text
所有正式 Task
    ↓
TaskScheduler
    ↓
TaskExecutor
```

不得存在：

```text
LangGraph
   ↓
直接 Python
```

或者：

```text
LangGraph
   ↓
直接 Tool
```

这样的生产旁路。

---

# 二十七、最终日志应该长这样

建议 Runtime 日志最终形成：

```text
[RUN] run_xxx started

[PLAN] plan_xxx version=1

[TASK] T01 pending
[TASK] T01 running
[EXEC] T01 executor=structured method=load_data
[EXEC] T01 succeeded duration=132ms

[TASK] T02 running
[EXEC] T02 executor=structured method=profile_dimension
[EXEC] T02 succeeded duration=84ms

[TASK] T03 running
[EXEC] T03 executor=structured method=compare_periods
[EXEC] T03 succeeded duration=93ms

[TASK] T04 running
[EXEC] T04 executor=structured method=compare_groups
[EXEC] T04 succeeded duration=101ms

[TASK] T05 running
[EXEC] T05 executor=structured method=decompose_contribution
[EXEC] T05 succeeded duration=112ms

[VERIFY] V001 numeric_consistency passed
[VERIFY] V002 time_consistency passed

[EVIDENCE] E001 verified
[EVIDENCE] E002 verified

[FINDING] F001 supported_by=E001
[FINDING] F002 supported_by=E002

[REPORT] generated

[RUN] run_xxx completed
```

这会比现在单纯看 Agent 日志强很多。

---

# 二十八、最终目录建议

Phase 1 完成以后，Runtime 部分建议稳定成：

```text
langgraph_langchain/
│
├── runtime/
│   ├── models.py
│   ├── plans.py
│   ├── scheduler.py
│   ├── executor.py
│   ├── analysis_runtime.py       # 核心
│   ├── verification.py           # 下一阶段
│   └── evidence.py               # 下一阶段
│
├── execution/
│   ├── task_models.py
│   ├── structured_executor.py
│   ├── react_executor.py
│   └── python_executor.py
│
├── tools/
│   └── ...
│
├── skills/
│   └── ...
│
├── langgraph_agent.py
│
└── schemas.py
```

其中：

```text
analysis_runtime.py
```

将成为真正的核心。

---

# 二十九、完成后的系统架构

最终这一阶段完成后，你的 DeepAnalyze 会从：

```text
LLM Agent
+
Tools
+
Python
+
LangGraph
```

升级成：

```text
                    DeepAnalyze
                         │
              ┌──────────┴──────────┐
              │                     │
          LangGraph             Runtime
          Workflow              Control Plane
                                    │
                    ┌───────────────┼───────────────┐
                    │               │               │
                  Plan          Scheduler        Executor
                                    │               │
                                    │       ┌───────┼───────┐
                                    │       │       │       │
                                    │   Structured ReAct  Python
                                    │
                                    ↓
                               Execution
                                    │
                                    ↓
                              Verification
                                    │
                                    ↓
                                Evidence
                                    │
                                    ↓
                                Finding
                                    │
                                    ↓
                                Report
```

这一步非常关键，因为后面你想做的：

```text
Skill Retriever
       ↓
Skill Selection
       ↓
Skill Execution
       ↓
Skill Evaluation
       ↓
Skill Evolution
```

都必须建立在这个 Runtime 控制面之上。

---

# 三十、建议你实际开发顺序

不要一次改完。

按下面顺序最稳：

```text
Day 1
│
├─ AnalysisRuntime
│
├─ TaskScheduler 接入
│
└─ 单元测试

Day 2
│
├─ TaskExecutor 接入
│
├─ Structured Executor
├─ React Executor
└─ Python Executor

Day 3
│
├─ LangGraph execute_task
├─ Runtime Controller
└─ 兼容旧链路

Day 4
│
├─ Verification
├─ Evidence
└─ Finding

Day 5
│
├─ E2E
├─ 回归测试
└─ 验收
```

---

# 三十一、最终开发完成定义（Definition of Done）

这一阶段只有同时满足下面条件，才建议标记：

> **Runtime V2 Phase 1 DONE**

```text
□ AnalysisRuntime 可以创建并管理一次 Run

□ AnalysisRuntime 持有 Scheduler

□ AnalysisRuntime 持有 TaskExecutor

□ Scheduler 可以从 AnalysisPlan 创建 AnalysisTask

□ Scheduler 正确处理 dependency

□ Scheduler 能阻止非法 DAG

□ TaskExecutor 成为统一执行入口

□ StructuredTaskExecutor 可执行结构化分析

□ ReactTaskExecutor 可执行复杂任务

□ PythonExecutor 可执行 Python 任务

□ 每个 Task 都产生 ExecutionResult

□ ExecutionResult 可关联 Artifact

□ VerificationResult 可以关联 Execution

□ Evidence 可以关联 Verification

□ Finding 可以关联 Evidence

□ LangGraph 正式执行路径经过 Runtime

□ 旧 Agent 不能绕过 Runtime 执行正式 Task

□ E2E 分析任务成功跑通

□ E2E 可以还原完整 provenance chain

□ 失败 Task 能正确记录

□ Report 不依赖 LLM 凭空生成事实

□ P0 测试全部通过
```

---

## 我建议你下一步直接做这一件事

**不要再停留在方案层。**

下一步可以直接针对你当前仓库做：

```text
Commit 1
AnalysisRuntime + TaskScheduler 集成
        ↓
Commit 2
AnalysisRuntime + TaskExecutor 集成
        ↓
Commit 3
langgraph_agent.py 接管
        ↓
Commit 4
Runtime V2 E2E 测试
```

而且我建议**下一轮直接按你当前仓库实际代码，逐个读取 `AnalysisRuntime`、`langgraph_agent.py` 中现有 Runtime 初始化/执行入口和现有 Plan 执行代码，然后给你出“文件级改造清单 + 方法级修改点 + 完整代码骨架 + 测试用例”**，这样就可以直接进入编码，而不是再做一版抽象架构。

我检查了你当前仓库的最新代码。结论比较明确：

> **上一轮方案已经实施了大约 70%～80% 的“Runtime V2 基础设施”，但还没有完成上一轮要求的“Runtime 真正接管生产执行链”。**
>
> 所以下一轮**不应该继续增加 Scheduler / Executor / Verification / Skill Retriever 基础设施**，而应该进入：
>
> # Runtime V2 Phase 2：生产执行链接管 + E2E 闭环

我以仓库当前最新提交 `7be7b655` 为基线检查；该提交已经包含 Runtime V2 的 Scheduler、Executor、Runner、Graph、Verification、Evidence、SkillRetriever 以及对应测试/验收文档。[data_analysis_agent_docker 当前仓库](https://github.com/hungryfoolishn/data_analysis_agent_docker?utm_source=chatgpt.com)

---

# 一、先给你最终判断

我把上一轮方案逐项对照现在代码：

| 上一轮要求                                | 当前代码                   | 判断          |
| ------------------------------------ | ---------------------- | ----------- |
| `AnalysisTask`                       | `schemas.py` 已有        | ✅           |
| `ExecutorType`                       | 已有                     | ✅           |
| `VerificationResult`                 | 已有                     | ✅           |
| Evidence 验证状态                        | 已有                     | ✅           |
| Finding 支持 Evidence                  | 已有                     | ✅           |
| `TaskScheduler`                      | `runtime/scheduler.py` | ✅           |
| DAG / dependency                     | 已实现                    | ✅           |
| 循环依赖检查                               | 已实现                    | ✅           |
| `TaskExecutor`                       | `runtime/executor.py`  | ✅           |
| Structured Executor                  | 已实现                    | ✅           |
| ReAct Executor                       | 已实现                    | ✅           |
| Python Executor                      | 已实现                    | ✅           |
| `TaskRunner`                         | 已实现                    | ✅           |
| Runtime LangGraph Controller         | `runtime/graph.py`     | ✅           |
| Verification                         | 已实现                    | ✅           |
| EvidenceCollector                    | 已实现                    | ✅           |
| SkillRetriever                       | 已实现                    | ✅           |
| LangGraph 接入 Runtime                 | 已接入                    | ⚠️ **部分完成** |
| Runtime 成为生产主控制流                     | **没有**                 | ❌           |
| Runtime 同时调度 Structured/ReAct/Python | **没有**                 | ❌           |
| Execution → Runtime 持久化              | **不完整**                | ❌           |
| Verification → Runtime 持久化           | **不完整**                | ❌           |
| Evidence → Finding                   | **没有形成完整闭环**           | ❌           |
| Runtime → Report                     | **依然依赖旧路径**            | ⚠️          |
| 真正 E2E                               | 当前测试主要是组件级             | ❌           |
| Legacy ReAct 彻底降级为 Executor          | **没有**                 | ❌           |

所以：

```text
Runtime V2 基础设施       ████████████████████ 100%
Runtime V2 控制器         ██████████████████░░  90%
生产链路接管              ████████████░░░░░░░░  60%
Evidence/Finding闭环      ██████████░░░░░░░░░░  50%
真实E2E                   ██████░░░░░░░░░░░░░░  30%
```

**现在最重要的不是再设计，而是“接管”。**

---

# 二、我检查到的几个关键事实

## 1. `AnalysisRuntime` 已经存在

现在是：

```text
langgraph_langchain/runtime/context.py
```

而不是之前方案里假设的：

```text
runtime/analysis_runtime.py
```

当前 `AnalysisRuntime` 已经负责：

```text
Session
 ↓
Run
 ↓
Plan
 ↓
RuntimePlanStep
 ↓
Execution
 ↓
Artifact
 ↓
Finding
```

并且已经有：

```python
start_step()
complete_step()
record_execution()
register_artifact()
record_finding()
propose_plan()
set_run_status()
pause_plan()
revise_plan()
confirm_plan()
resume_plan()
```

所以：

> **AnalysisRuntime 本身不需要重写。**

---

# 三、Scheduler 已经基本完成

当前：

```text
runtime/scheduler.py
```

已经具备：

```text
AnalysisPlan
    ↓
analysis_tasks_from_plan()
    ↓
AnalysisTask
    ↓
TaskScheduler
```

而且已经实现：

```text
dependency
topological order
cycle detection
unknown dependency
duplicate task
running limit
ready_tasks()
blocked_tasks()
next_task()
mark_running()
mark_succeeded()
mark_failed()
```

这部分上一轮要求已经基本落地。

### 结论

**不要再改 Scheduler。**

除非后续真实 E2E 暴露问题。

---

# 四、Executor 层也已经完成

当前：

```text
runtime/executor.py
```

已经有：

```python
TaskExecutor.execute()
TaskExecutor.execute_task()
```

并且可以路由：

```text
structured
react
python
```

同时已经有：

```text
StructuredTaskExecutor
ReactTaskExecutor
PythonTaskExecutor
```

甚至已经有：

```text
tests/test_runtime_executor_commit3.py
```

测试 Structured / ReAct / Python 三种执行器。

所以这部分：

> **上一轮已经实施。**

---

# 五、TaskRunner 也已经出现了

这是一个比较重要的进展。

当前：

```text
runtime/runner.py
```

已经形成：

```text
Scheduler
   ↓
TaskRunner
   ↓
TaskExecutor
   ↓
ExecutionResult
   ↓
Verification
   ↓
Evidence
```

而且：

```python
execute_next()
run()
```

已经存在。

实际上你现在已经拥有了一个：

> **Runtime Execution Loop**

这是非常关键的基础。

---

# 六、Runtime Graph 也已经实现

当前：

```text
runtime/graph.py
```

已经实现：

```text
START
  ↓
execute_task
  ↓
execute_task
  ↓
execute_task
  ↓
...
  ↓
END
```

Graph 本身不再负责调度，而是：

```text
Graph
  ↓
TaskRunner
  ↓
Scheduler
```

这个方向是正确的。

---

# 七、但是这里存在一个非常关键的问题

当前 `build_runtime_v2_controller()`：

```python
executor = TaskExecutor(
    structured=StructuredTaskExecutor(
        tool_resolver=tool_resolver
    ),
)
```

注意：

# 这里只配置了 Structured Executor。

没有：

```python
react=ReactTaskExecutor(...)
```

也没有：

```python
python=PythonTaskExecutor(...)
```

因此现在 Runtime V2 Controller 实际上是：

```text
Runtime
  ↓
Scheduler
  ↓
TaskRunner
  ↓
TaskExecutor
  ↓
Structured
```

而不是：

```text
Runtime
       ↓
Scheduler
       ↓
TaskExecutor
   ┌───┼────┐
   ↓   ↓    ↓
Struct React Python
```

这是下一轮必须修复的 **P0**。

---

# 八、第二个更严重的问题：Runtime V2 目前还是“旁路”

现在 `langgraph_agent.py` 里有：

```python
if _RUNTIME_V2_ENABLED:
    async for chunk, artifacts in _run_runtime_v2_graph_stream(...):
        ...
```

Runtime V2 跑失败以后：

```text
Runtime V2
    ↓
failed / incomplete
    ↓
Legacy ReAct
```

也就是说：

```text
                 ┌→ Runtime V2
                 │
Question → Agent ┤
                 │
                 └→ Legacy ReAct
```

这还是**双轨系统**。

而且配置：

```python
RUNTIME_V2_ENABLED
```

默认是关闭的。

所以生产默认路径实际上还是：

```text
Question
 ↓
create_react_agent()
 ↓
LLM
 ↓
Tools
```

而不是：

```text
Question
 ↓
Runtime
 ↓
Scheduler
 ↓
Executor
```

---

# 九、因此上一轮最核心目标实际上还没有完成

上一轮最重要的一句话是：

> **LLM 从 Controller 降级为 Worker。**

目前实际上还是：

```text
LLM = Controller
Runtime = Optional Controller
```

目标应该变成：

```text
Runtime = Controller
LLM = Worker
```

所以我建议：

# 下一轮不要再叫“Runtime V2 基础设施建设”。

直接定义：

# Runtime V2 Phase 2：Runtime Control Plane Takeover

---

# 十、下一轮的最终目标

目标架构：

```text
                         User
                           │
                           ↓
                     LangGraph
                           │
                           ↓
                   AnalysisRuntime
                           │
                           ↓
                     AnalysisPlan
                           │
                           ↓
                    TaskScheduler
                           │
                           ↓
                     TaskRunner
                           │
                           ↓
                    TaskExecutor
                 ┌─────────┼─────────┐
                 ↓         ↓         ↓
             Structured   ReAct    Python
                 │         │         │
                 └─────────┼─────────┘
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

---

# 十一、Phase 2 具体实施计划

我建议拆成 **5 个 Commit**。

```text
Commit 9
Runtime统一执行上下文

Commit 10
三类 Executor 正式接入 Runtime

Commit 11
Execution → Artifact → Verification → Evidence 持久化闭环

Commit 12
Finding → Report 闭环

Commit 13
真实 E2E + Legacy 降级
```

---

# 十二、Commit 9：统一 Runtime Execution Context

## 目标

解决现在：

```text
AnalysisRuntime
```

和：

```text
RuntimeV2Controller
```

各自维护状态的问题。

当前实际上存在：

```text
AnalysisRuntime
       │
       ├── run
       ├── steps
       ├── executions
       ├── artifacts
       └── findings

RuntimeV2Controller
       │
       ├── scheduler
       └── runner
```

这两边需要统一。

---

## 改造目标

让：

```text
AnalysisRuntime
```

拥有：

```python
runtime.scheduler
runtime.runner
runtime.executor
```

或者至少：

```python
runtime.create_execution_controller()
```

最终：

```python
runtime.execute_next_task()
```

成为正式入口。

---

# 十三、建议新增方法

在：

```text
runtime/context.py
```

增加：

```python
def build_controller(self, ...):
    ...

def execute_next_task(self, ...):
    ...

def execute_all(self, ...):
    ...

def record_execution_result(self, result):
    ...

def record_verification_results(self, results):
    ...

def record_evidence(self, evidence):
    ...
```

这样：

```text
langgraph_agent.py
```

不再直接：

```python
build_runtime_v2_controller(...)
```

而是：

```python
runtime.execute_next_task()
```

---

# 十四、Commit 10：三类 Executor 真正接入

这是 P0。

修改：

```text
runtime/graph.py
```

目前：

```python
TaskExecutor(
    structured=...
)
```

变成：

```python
TaskExecutor(
    structured=structured_executor,
    react=react_executor,
    python=python_executor,
)
```

---

# 十五、Executor 初始化应该来自 Session

因为：

```text
Structured
```

需要：

```text
Session Tool Registry
```

而：

```text
ReAct
```

需要：

```text
LLM + tools + prompt
```

Python：

```text
workspace
source_path
namespace
timeout
artifact recorder
```

所以建议：

```text
Session
  │
  ├── Tool Registry
  ├── ReAct Agent
  ├── Python Executor
  │
  └── AnalysisRuntime
             │
             ↓
         TaskExecutor
```

---

# 十六、最终路由

必须实现：

```text
task.executor_type
        │
        ├── structured → StructuredTaskExecutor
        │
        ├── react      → ReactTaskExecutor
        │
        └── python     → PythonTaskExecutor
```

验收：

```text
structured task
    → StructuredTaskExecutor

react task
    → ReactTaskExecutor

python task
    → PythonTaskExecutor
```

不能出现：

```text
Runtime
 ↓
全部 Structured
```

---

# 十七、Commit 11：Execution → Artifact → Verification → Evidence

这是下一轮最关键的业务闭环。

目前 TaskRunner 已经做了：

```text
ExecutionResult
 ↓
verify
 ↓
Evidence
```

但是还没有完全写回：

```text
AnalysisRuntime
```

---

## 当前问题

`TaskRunner`：

```python
result.verification_results = verifications
result.evidence = evidence
```

但是：

> **result 有了，不代表 Runtime 的 authoritative state 有了。**

必须最终：

```text
AnalysisRuntime.executions
AnalysisRuntime.artifacts
AnalysisRuntime.verification_results
AnalysisRuntime.evidence
```

都得到持久化。

---

# 十八、建议 Runtime 增加两个集合

当前 Runtime 已有：

```python
self.executions
self.artifacts
self.findings
```

建议增加：

```python
self.verification_results
self.evidence
```

例如：

```python
self.verification_results: list[VerificationResult] = []
self.evidence: list[EvidenceItem] = []
```

然后：

```python
runtime.record_execution_result(result)

runtime.record_verifications(
    result.verification_results
)

runtime.record_evidence(
    result.evidence
)
```

统一：

```text
Execution
 ↓
Runtime
 ↓
Verification
 ↓
Runtime
 ↓
Evidence
 ↓
Runtime
```

---

# 十九、Evidence 必须成为 Runtime 一级对象

最终：

```text
AnalysisRuntime
├── Run
├── Plan
├── Tasks
├── Executions
├── Artifacts
├── VerificationResults
├── Evidence
├── Findings
└── Report
```

而不是：

```text
AnalysisRuntime
├── Executions
├── Artifacts
└── Findings

ExecutionResult
└── Evidence
```

---

# 二十、Commit 12：Finding 闭环

现在已有：

```text
EvidenceCollector
```

但还缺：

```text
Evidence
 ↓
Finding
```

自动化闭环。

目标：

```text
Execution
 ↓
Verification
 ↓
Evidence
 ↓
Finding
```

这里要注意：

> **不要让 EvidenceCollector 直接让 LLM 自由生成 Finding。**

可以采用：

```text
Verified Evidence
       ↓
Finding Builder
       ↓
Finding
```

---

# 二十一、Finding Builder

建议增加：

```text
runtime/finding_builder.py
```

职责：

```python
build_finding(
    execution,
    evidence,
    task,
)
```

产生：

```python
Finding(
    statement=...,
    supported_by=[evidence.evidence_id],
    depends_on=[...],
)
```

对于第一阶段：

```text
statement
```

可以来自结构化工具结果。

复杂诊断任务再让：

```text
ReAct
```

生成候选 Finding。

但是最终必须：

```text
Finding
 ↓
supported_by
 ↓
verified Evidence
```

---

# 二十二、Commit 12 同时改 Report

现在：

```text
finish_report()
```

仍然是旧 Agent 模式的重要部分。

目标：

```text
finish_report(
    goal,
    verified_findings,
    evidence,
    artifacts
)
```

而不是：

```text
finish_report()
```

---

# 二十三、Report 的权限必须降低

Report Agent：

### 可以：

```text
总结 Finding
解释 Evidence
引用 Artifact
组织章节
```

### 不可以：

```text
重新读取 DataFrame
重新计算核心指标
自己发现新的数字
自己创造 Evidence
```

最终：

```text
Raw Data
   ↓
Analysis
   ↓
Finding
   ↓
Report
```

而不是：

```text
Raw Data
   ↓
Report Agent
   ↓
“我觉得是……”
```

---

# 二十四、Commit 13：真正的 E2E

这是我认为你现在最缺的一部分。

目前已有：

```text
test_runtime_schemas_commit1.py
test_runtime_scheduler_commit2.py
test_runtime_executor_commit3.py
test_runtime_runner_commit4.py
test_runtime_graph_commit5.py
test_verification_commit6.py
test_evidence_commit7.py
test_skill_retriever_commit8.py
```

这些很好。

但是它们主要证明：

> **组件存在且单独工作。**

还没有证明：

> **真实 DeepAnalyze 请求可以完整走完 Runtime。**

---

# 二十五、必须新增真正的 E2E

建议：

```text
tests/e2e/test_runtime_v2_sales_analysis.py
```

---

## 输入

数据：

```text
grouped_sales
```

问题：

```text
分析各部门销售额同比变化，
找出下降最大的部门，
并说明主要贡献因素。
```

---

# 二十六、预期 Runtime DAG

建议：

```text
T01
schema
 │
 ↓
T02
profile
 │
 ↓
T03
comparison
 │
 ↓
T04
breakdown
 │
 ↓
T05
contribution
 │
 ↓
T06
verification
 │
 ↓
T07
finding
 │
 ↓
T08
report
```

但注意：

**不要求一定是这 8 个 Task。**

真正验收的是能力链：

```text
数据理解
 ↓
比较
 ↓
分组
 ↓
贡献
 ↓
验证
 ↓
Finding
 ↓
Report
```

---

# 二十七、E2E 必须检查什么

不能只：

```python
assert "华东" in report
```

要检查：

```python
assert run.status == "completed"

assert all(
    task.status == "succeeded"
    for task in runtime.tasks
)

assert len(runtime.executions) > 0

assert len(runtime.artifacts) > 0

assert len(runtime.verification_results) > 0

assert len(runtime.evidence) > 0

assert len(runtime.findings) > 0
```

最关键：

```python
for finding in runtime.findings:

    assert finding.supported_by

    for evidence_id in finding.supported_by:
        assert evidence_id in runtime.evidence
```

然后：

```text
Finding
 ↓
Evidence
 ↓
Verification
 ↓
Execution
 ↓
Task
 ↓
Dataset
```

必须可以完整追踪。

---

# 二十八、增加一个“旁路检测”测试

这个非常重要。

你现在最大的架构风险就是：

```text
Runtime
+
Legacy Agent
```

双轨并存。

所以增加：

```text
test_runtime_is_authoritative_controller()
```

测试：

```text
RUNTIME_V2_ENABLED=true
```

执行真实请求。

检查：

```text
TaskScheduler 调用了
TaskExecutor

而不是：

LangGraph → create_react_agent → tool
```

---

# 二十九、Legacy ReAct 怎么处理

不是立即删除。

建议：

```text
Runtime V2
    ↓
Task
    ↓
Executor
    ├── Structured
    ├── ReAct
    └── Python
```

只有：

```text
Runtime 初始化失败
```

或者：

```text
计划无法执行
```

才允许：

```text
Emergency Fallback
       ↓
Legacy ReAct
```

而不是：

```text
Runtime task failed
       ↓
正常切换 Legacy
```

这个区别非常重要。

否则 Runtime 永远无法真正成为 Controller。

---

# 三十、Feature Flag 改造

当前：

```python
RUNTIME_V2_ENABLED
```

是：

```text
false
```

下一阶段建议：

```text
RUNTIME_V2_ENABLED=true
```

成为测试/生产目标配置。

然后新增：

```text
RUNTIME_V2_FALLBACK_ENABLED=true
```

这样：

```text
正常：
Runtime → Executor

Runtime infrastructure failure：
        ↓
Fallback
        ↓
Legacy ReAct
```

而：

```text
Task business failure
```

不能直接 fallback。

例如：

```text
compare_periods 失败
```

应该：

```text
Task failed
 ↓
Retry / Replan
```

而不是：

```text
Legacy ReAct
```

---

# 三十一、下一轮不要继续做的东西

你现在已经有：

```text
SkillRetriever
Verification
Evidence
Scheduler
Executor
Runner
RuntimeGraph
```

所以以下全部暂缓：

```text
❌ Vector DB
❌ MCP
❌ Multi Agent
❌ GEPA
❌ Skill Evolution
❌ Insight Graph 大重构
❌ PostgreSQL
❌ Parallel Execution
```

现在继续做这些，容易变成：

> 基础设施越来越多，但生产链路仍然由 ReAct 控制。

---

# 三十二、下一轮实施任务表

可以直接给 Codex：

| 优先级 | Task                    | 文件                           | 验收                 |
| --- | ----------------------- | ---------------------------- | ------------------ |
| P0  | Runtime 持有 Controller   | `runtime/context.py`         | Runtime 可执行 Task   |
| P0  | 三 Executor 注入           | `runtime/graph.py`           | 3 类 Task 均可执行      |
| P0  | Runtime 保存 Execution    | `runtime/context.py`         | executions 可回读     |
| P0  | Runtime 保存 Verification | `runtime/context.py`         | verification 可回读   |
| P0  | Runtime 保存 Evidence     | `runtime/context.py`         | evidence 可回读       |
| P0  | LangGraph 主链切换          | `langgraph_agent.py`         | Runtime 成主控制器      |
| P0  | E2E                     | `tests/e2e/`                 | 完整跑通               |
| P1  | Finding Builder         | `runtime/finding_builder.py` | Evidence → Finding |
| P1  | Report 输入收敛             | `finish_report`              | 只消费 verified data  |
| P1  | Fallback 策略             | `langgraph_agent.py`         | 仅基础设施失败 fallback   |
| P1  | Provenance 验收           | `tests/e2e/`                 | F→E→V→X→T→D        |
| P2  | UI Runtime Timeline     | frontend                     | 展示 Task 生命周期       |

---

# 三十三、Phase 2 验收文档

建议新建：

```text
docs/Runtime/V2_Phase2_Production_Acceptance.md
```

内容可以直接按下面执行。

---

## Runtime V2 Phase 2 生产执行链验收

### 1. 验收目标

证明：

```text
Runtime
```

已经成为 DeepAnalyze 的正式执行控制面。

---

### 2. P0 验收

#### P0-01 Runtime Controller

要求：

```text
Question
 ↓
Runtime
 ↓
Scheduler
 ↓
Runner
 ↓
Executor
```

**PASS：**

生产执行路径 100% 经过 Runtime。

---

#### P0-02 Executor Routing

测试：

```text
structured
react
python
```

结果：

```text
structured → StructuredTaskExecutor
react → ReactTaskExecutor
python → PythonTaskExecutor
```

**PASS：3/3**

---

#### P0-03 Execution Persistence

每个 Task：

```text
Task
 ↓
ExecutionResult
 ↓
Runtime.executions
```

必须存在。

**PASS：**

```text
task_count == execution_count
```

允许跳过任务除外。

---

#### P0-04 Verification Persistence

每个需要验证的 Execution：

```text
Execution
 ↓
VerificationResult
```

**PASS：**

所有关键 Execution 都有 Verification。

---

#### P0-05 Evidence Persistence

```text
Verification
 ↓
Evidence
```

**PASS：**

每个最终 Finding 的 Evidence：

```text
verification_status == "verified"
```

---

#### P0-06 Finding Provenance

每个 Finding：

```text
Finding
 ↓
Evidence
 ↓
Verification
 ↓
Execution
 ↓
Task
 ↓
Dataset
```

必须能够追踪。

---

#### P0-07 Report Integrity

报告不得出现：

```text
没有 Evidence 的数字
```

不得出现：

```text
Report Agent 自己计算的新指标
```

---

# 三十四、核心 E2E 验收表

| ID      | 验收内容           | PASS 条件                                   |
| ------- | -------------- | ----------------------------------------- |
| E2E-001 | 用户问题进入 Runtime | Runtime 创建 Run                            |
| E2E-002 | Plan 创建        | Plan 可序列化                                 |
| E2E-003 | Task 创建        | Task ≥ 1                                  |
| E2E-004 | Scheduler      | 按依赖执行                                     |
| E2E-005 | Structured     | 至少一个 Structured Task                      |
| E2E-006 | Python         | 至少一个 Python Task                          |
| E2E-007 | ReAct          | 至少一个复杂 Task 可走 ReAct                      |
| E2E-008 | Execution      | 每个任务产生 Result                             |
| E2E-009 | Artifact       | Artifact 存在                               |
| E2E-010 | Verification   | 验证成功                                      |
| E2E-011 | Evidence       | Evidence verified                         |
| E2E-012 | Finding        | Finding 引用 Evidence                       |
| E2E-013 | Report         | Report 只引用可信 Finding                      |
| E2E-014 | Provenance     | 完整血缘                                      |
| E2E-015 | SSE            | 前端事件正常                                    |
| E2E-016 | Failure        | 失败可恢复                                     |
| E2E-017 | Persistence    | 重启后 Run 可恢复                               |
| E2E-018 | Fallback       | 仅 Runtime infrastructure failure fallback |

---

# 三十五、最终通过标准

Phase 2 不建议用“感觉完成”。

定义为：

```text
P0 = 100% PASS

P1 = ≥ 90% PASS

P2 = 可有遗留
```

且：

```text
P0-01 Runtime Controller              PASS
P0-02 Executor Routing               PASS
P0-03 Execution Persistence           PASS
P0-04 Verification Persistence       PASS
P0-05 Evidence Persistence           PASS
P0-06 Finding Provenance              PASS
P0-07 Report Integrity                PASS
```

任何一个 P0 失败：

> **Phase 2 = NOT READY**

---

# 三十六、这一轮完成后的架构

最终应该从现在：

```text
                    Question
                       │
             ┌─────────┴─────────┐
             ↓                   ↓
       Runtime V2            Legacy Agent
             │                   │
       Scheduler                ReAct
             │                   │
       TaskRunner               Tools
             │
       Structured only
```

变成：

```text
                         Question
                            │
                            ↓
                     LangGraph Workflow
                            │
                            ↓
                    ┌───────────────┐
                    │ AnalysisRuntime│
                    └───────┬───────┘
                            │
                         Planner
                            │
                         Plan
                            │
                       Scheduler
                            │
                        TaskRunner
                            │
                       TaskExecutor
                            │
             ┌──────────────┼──────────────┐
             ↓              ↓              ↓
        Structured         ReAct          Python
             │              │              │
             └──────────────┼──────────────┘
                            ↓
                     ExecutionResult
                            ↓
                       Artifact Store
                            ↓
                       Verification
                            ↓
                         Evidence
                            ↓
                         Finding
                            ↓
                         Report
```

而：

```text
Legacy ReAct
```

变成：

```text
Emergency Fallback
```

而不是主执行链。

---

# 三十七、所以我对你当前仓库的最终判断

你现在其实已经完成了上一轮最难的**基础设施建设**：

```text
Schema             ✅
Scheduler          ✅
Executor           ✅
Runner             ✅
Runtime Graph      ✅
Verification       ✅
Evidence           ✅
Skill Retriever    ✅
Tests              ✅
```

但是还缺最重要的最后一公里：

```text
             ┌──────────────┐
             │ Infrastructure│
             └──────┬───────┘
                    │
                    ↓
          ┌──────────────────┐
          │ Production Chain │  ← 现在就在这里
          └────────┬─────────┘
                   ↓
          ┌──────────────────┐
          │ True E2E Runtime │
          └──────────────────┘
```

**因此下一轮不要再“造 Runtime”。**

应该做：

> **把已经造好的 Runtime 接管真实分析执行。**

尤其是下面 4 个 P0，我建议你下一步直接让 Codex 按这四项开始改：

```text
P0-1  AnalysisRuntime ←→ RuntimeV2Controller 统一
P0-2  TaskExecutor 正式接入 Structured / ReAct / Python 三执行器
P0-3  Execution → Verification → Evidence → Finding 全部回写 Runtime
P0-4  langgraph_agent.py 将 Runtime 改成主路径，并增加真正 E2E
```

完成这四项以后，你的 DeepAnalyze 才真正从：

> **“有 Runtime V2 组件的 ReAct Agent”**

变成：

> **“Runtime 控制、LLM/Agent 执行的 Data Analysis Agent”。**

这也是下一阶段再做 **Skill Retrieval → Evaluation → Failure Memory → Skill Evolution** 的正确基础。

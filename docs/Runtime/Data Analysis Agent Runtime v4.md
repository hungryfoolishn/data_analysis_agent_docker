继续。现在我们进入你这个仓库真正可以落代码的阶段。

我重新核对了当前 `langgraph_agent.py` 和 `schemas.py`。这里有一个重要修正：

> **你现在并不是“缺 Runtime”。而是 Runtime 已经存在，但 `langgraph_agent.py` 仍然承担了太多 Runtime/Agent/Session/Tool/Report 职责。**
>
> 所以 V2 第一阶段不是“新增一个 Runtime”，而是**让现有 Runtime 真正成为控制中心，然后逐步把 `langgraph_agent.py` 瘦下来。**

下面直接做方法级改造。

---

# 1. 先给你 V2 的最终调用链

我建议先把目标固定成这个：

```text
HTTP Request
    │
    ▼
api_server_langgraph.py
    │
    ▼
run_analysis_stream()
    │
    ▼
AnalysisRuntime
    │
    ├── ① Goal Understanding
    │
    ├── ② Plan
    │
    ├── ③ Validate Plan
    │
    └── ④ Scheduler
             │
             ▼
        AnalysisTask
             │
             ▼
        TaskExecutor
       /      |       \
      /       |        \
 ToolExecutor ReactExecutor PythonExecutor
      \       |        /
       \      |       /
        ▼     ▼      ▼
          Execution
              │
              ▼
           Artifact
              │
              ▼
         Verification
              │
        ┌─────┴─────┐
        ▼           ▼
     Evidence     Failure
        │           │
        ▼           ▼
     Finding      Replan
        │
        ▼
   Insight Graph
        │
        ▼
      Report
        │
        ▼
    Evaluation
```

这个链路以后不要再变。

---

# 2. `langgraph_agent.py` 以后只保留什么？

目前这个文件明显是“大总管”。

它现在同时处理：

```text
Session
DataFrame namespace
Python helpers
Tool construction
Skill loading
Prompt
ReAct
Analysis stages
Runtime
Finding
Evidence
Report
Error recovery
Trace
Streaming
```

V2 最终目标：

```text
langgraph_agent.py

只负责：

1. 创建 Runtime
2. 创建 LangGraph
3. 注册节点
4. 启动 Graph
5. 输出 stream
```

也就是说最终应该接近：

```python
def build_analysis_graph(...):
    ...


async def run_analysis_stream(...):
    runtime = create_runtime(...)
    graph = build_analysis_graph(runtime)
    async for event in graph.astream(...):
        yield event
```

---

# 3. 第一刀：`_Session` 不删除

这个地方我特别强调。

你现在 `_Session` 里面有大量非常有价值的东西：

```text
workspace
source_path
session_id
user_question
current_stage
stage_history
stage_failures
total_steps
Python namespace
save_fig
load_csv
fix_chinese
profile_dimension
compare_segments
time_trend
detect_anomalies
explain_metric_change
...
```

**不要直接删除。**

现在先定义：

> `_Session = Analysis Execution Context`

也就是说它不是最终 Runtime。

---

# 4. `_Session` 和 `AnalysisRuntime` 要明确分工

以后：

### `_Session`

负责：

```text
当前数据分析环境
    │
    ├── workspace
    ├── dataframe
    ├── python namespace
    ├── matplotlib
    ├── 当前 session
    └── 临时执行上下文
```

### `AnalysisRuntime`

负责：

```text
整个分析生命周期
    │
    ├── Run
    ├── Plan
    ├── Task
    ├── Execution
    ├── Artifact
    ├── Evidence
    ├── Finding
    ├── Verification
    ├── Failure
    └── Report
```

这是非常重要的边界。

---

# 5. 你现在的 `_Session` 最终应该变成这样

```text
_Session
   │
   ├── session_id
   ├── workspace
   ├── source_path
   ├── user_question
   │
   ├── data_context
   │
   ├── python_context
   │
   └── execution_context
```

而：

```text
AnalysisRuntime
   │
   ├── run_id
   ├── goal
   ├── plan
   ├── task_queue
   ├── current_task
   ├── executions
   ├── artifacts
   ├── verification_results
   ├── evidence
   ├── findings
   ├── failures
   ├── report
   └── evaluation
```

---

# 6. `schemas.py` 第一阶段怎么改

你现在已经有：

```python
PlanStep
Plan
EvidenceItem
Finding
ArtifactRef
ConclusionTrace
SessionLineage
```

所以不要重新造一套。

建议增加：

```python
TaskType = Literal[
    "schema",
    "profile",
    "metric",
    "comparison",
    "trend",
    "breakdown",
    "contribution",
    "anomaly",
    "correlation",
    "statistical_test",
    "root_cause",
    "visualization",
    "report",
]
```

然后：

```python
ExecutorType = Literal[
    "structured_tool",
    "react",
    "python",
    "system",
]
```

---

# 7. 新增 `AnalysisTask`

建议：

```python
class AnalysisTask(BaseModel):
    task_id: str

    objective: str

    task_type: TaskType

    skill_id: Optional[str] = None

    executor: ExecutorType = "structured_tool"

    required_inputs: Dict[str, Any] = Field(default_factory=dict)

    expected_outputs: List[str] = Field(default_factory=list)

    verification_rules: List[str] = Field(default_factory=list)

    depends_on: List[str] = Field(default_factory=list)

    status: Literal[
        "pending",
        "running",
        "succeeded",
        "failed",
        "needs_revision",
        "skipped",
    ] = "pending"

    retry_count: int = 0

    error: Optional[str] = None
```

---

# 8. `RuntimePlanStep` 怎么处理？

如果你现在已经有 `RuntimePlanStep`：

**不要同时维护两套模型。**

这是非常容易踩坑的地方。

最终建议：

```text
AnalysisTask
     ▲
     │
RuntimePlanStep
```

甚至最终：

```text
RuntimePlanStep = AnalysisTask
```

如果当前代码大量依赖 `RuntimePlanStep`，第一阶段不要 rename。

直接：

```python
class RuntimePlanStep(BaseModel):
    ...
    task_type: TaskType
    skill_id: Optional[str]
    executor: ExecutorType
    verification_rules: List[str]
```

然后逐步把旧字段淘汰。

---

# 9. `PlanStep` 暂时不要动

你现在：

```python
class PlanStep:
    index
    title
    code_task
    expected_artifacts
    is_final_step
```

它明显是早期 Python-code driven Plan。

所以标记：

```text
Legacy Plan
```

以后：

```text
PlanStep
   ↓
Legacy compatibility
```

新系统：

```text
RuntimePlanStep
   ↓
AnalysisTask
```

暂时不要删除。

---

# 10. `Plan` 以后也分两层

你现在：

```python
class Plan:
    steps: List[PlanStep]
```

这其实是旧 Plan。

V2：

```python
class AnalysisPlan(BaseModel):
    plan_id: str

    goal: str

    tasks: List[RuntimePlanStep]

    assumptions: List[AnalysisAssumption]

    expected_findings: List[str]

    created_at: str
```

于是：

```text
旧：

Plan
 └── PlanStep
       └── code_task


新：

AnalysisPlan
 └── RuntimePlanStep
       ├── task_type
       ├── skill
       ├── executor
       ├── tool
       ├── verification
       └── dependencies
```

---

# 11. 接下来改 `langgraph_agent.py`

这里是最关键的一步。

目前它里面大量逻辑可以分成 6 类。

---

## A. Session

保留：

```python
class _Session:
```

但以后逐渐移动。

---

## B. Tool Construction

现在 `_Session` 和 Agent 创建过程中存在工具绑定。

目标：

```text
tools/
    registry.py
    tool_factory.py
```

新增：

```python
class ToolRegistry:

    def get(self, name: str):
        ...

    def list(self):
        ...

    def execute(self, name: str, **kwargs):
        ...
```

你现在的：

```text
tools/tool_analysis_methods.py
tools/registry.py
```

直接作为基础。

---

# 12. `create_react_agent` 搬到 ReactExecutor

新文件：

```text
execution/react_executor.py
```

：

```python
class ReactExecutor:

    def __init__(
        self,
        model,
        tools,
        prompt,
    ):
        self.agent = create_react_agent(
            model=model,
            tools=tools,
            prompt=prompt,
        )

    async def execute(
        self,
        task: RuntimePlanStep,
        session: _Session,
    ):
        ...
```

于是：

```text
langgraph_agent.py
       │
       X
       │
create_react_agent
```

变成：

```text
TaskExecutor
      │
      ▼
ReactExecutor
      │
      ▼
create_react_agent
```

---

# 13. 这是整个 V2 最关键的控制权转移

以前：

```text
LLM
 ↓
决定 tool
 ↓
决定下一步
 ↓
决定结束
```

以后：

```text
Runtime
 ↓
决定 Task
 ↓
决定 Executor
 ↓
Executor
 ↓
LLM只负责完成 Task
```

也就是说：

> **LLM 从 Controller 变成 Worker。**

这是你的 DeepAnalyze 从 Agent Demo 走向 Agent Runtime 最重要的一步。

---

# 14. `TaskScheduler` 具体怎么写

新建：

```text
langgraph_langchain/runtime/scheduler.py
```

第一版甚至不需要复杂。

```python
class TaskScheduler:

    def ready_tasks(
        self,
        tasks: list[RuntimePlanStep],
    ) -> list[RuntimePlanStep]:

        completed = {
            task.step_id
            for task in tasks
            if task.status == "succeeded"
        }

        return [
            task
            for task in tasks
            if task.status == "pending"
            and all(dep in completed for dep in task.depends_on)
        ]
```

然后：

```python
def next_task(self, tasks):
    ready = self.ready_tasks(tasks)

    if not ready:
        return None

    return ready[0]
```

第一版就够了。

---

# 15. 为什么先不要做复杂 Scheduler？

因为你现在真正需要验证的是：

```text
Plan
 ↓
Task
 ↓
Execute
 ↓
Complete
 ↓
Next Task
```

不是：

```text
DAG Scheduler
Priority Queue
Parallel Execution
Resource Allocation
```

这些以后再做。

---

# 16. `TaskExecutor` 第一版

新增：

```text
runtime/executor.py
```

：

```python
class TaskExecutor:

    async def execute(
        self,
        task: RuntimePlanStep,
        runtime: AnalysisRuntime,
        session: _Session,
    ):

        if task.executor == "structured_tool":
            return await self._execute_structured(task, runtime, session)

        if task.executor == "react":
            return await self._execute_react(task, runtime, session)

        if task.executor == "python":
            return await self._execute_python(task, runtime, session)

        raise ValueError(
            f"Unsupported executor: {task.executor}"
        )
```

---

# 17. `_execute_structured`

这个是第一优先级。

```python
async def _execute_structured(...):

    tool_name = self._resolve_tool(task)

    result = await self.tool_registry.execute(
        tool_name,
        task.required_inputs,
        session=session,
    )

    return result
```

重点：

> 不让 LLM 写 Python。

---

# 18. `_execute_react`

：

```python
async def _execute_react(...):

    return await self.react_executor.execute(
        task=task,
        session=session,
    )
```

---

# 19. `_execute_python`

暂时可以：

```python
async def _execute_python(...):

    return await self.python_executor.execute(
        task=task,
        session=session,
    )
```

但是注意：

PythonExecutor 最终还是调用你现有的：

```text
python_repl
```

所以第一阶段完全不用重新实现 Python sandbox。

---

# 20. 这样你现有工具全部可以复用

例如：

```text
compare_periods
compare_groups
analyze_time_trend
decompose_contribution
detect_anomalies
analyze_correlation
test_group_difference
analyze_concentration
```

以后变成：

```text
Task
 ↓
Tool Registry
 ↓
Tool
 ↓
ExecutionResult
```

而不是：

```text
LLM
 ↓
工具描述
 ↓
自己判断
```

---

# 21. 现在最重要的是建立 Tool → Task 映射

可以先硬编码：

```python
TOOL_BY_TASK_TYPE = {

    "profile":
        "profile_dimension",

    "comparison":
        "compare_periods",

    "breakdown":
        "compare_groups",

    "trend":
        "analyze_time_trend",

    "contribution":
        "decompose_contribution",

    "anomaly":
        "detect_anomalies",

    "correlation":
        "analyze_correlation",

}
```

这是 V2 第一阶段非常值得做的。

以后再让 Skill Registry 动态决定。

---

# 22. 例如用户问题

> 2026年Q2各部门销售额同比变化，找出下降最多的部门。

Planner 生成：

```json
{
  "tasks": [
    {
      "step_id": "T01",
      "task_type": "comparison",
      "objective": "计算各部门销售额同比变化",
      "executor": "structured_tool",
      "method": "compare_periods"
    },
    {
      "step_id": "T02",
      "task_type": "breakdown",
      "objective": "按部门分析销售额变化",
      "executor": "structured_tool",
      "method": "compare_groups",
      "depends_on": ["T01"]
    },
    {
      "step_id": "T03",
      "task_type": "verification",
      "objective": "验证同比计算结果",
      "executor": "system",
      "depends_on": ["T02"]
    },
    {
      "step_id": "T04",
      "task_type": "report",
      "objective": "生成分析报告",
      "executor": "system",
      "depends_on": ["T03"]
    }
  ]
}
```

---

# 23. 这时候 LangGraph Node 就非常清晰了

建议第一版只做：

```text
START
  │
  ▼
load_context
  │
  ▼
build_plan
  │
  ▼
validate_plan
  │
  ▼
execute_task
  │
  ▼
verify_result
  │
  ▼
collect_evidence
  │
  ▼
record_finding
  │
  ▼
should_replan?
  │
 ┌┴───────┐
 │        │
YES      NO
 │        │
 ▼        ▼
replan   next_task
 │        │
 └───┬────┘
     ▼
  next_task
     │
     ▼
 no task?
   /   \
 YES    NO
  │      │
  ▼      └──→ execute_task
report
  │
  ▼
evaluation
  │
  ▼
END
```

---

# 24. 这里有一个非常关键的 LangGraph State

你现在不要继续把各种状态塞 `_Session`。

新建：

```python
class AnalysisGraphState(TypedDict):
    session_id: str
    run_id: str

    user_question: str

    goal: dict

    plan: dict

    current_task_id: str | None

    task_results: list[dict]

    artifacts: list[dict]

    verification_results: list[dict]

    evidence: list[dict]

    findings: list[dict]

    failures: list[dict]

    report: str | None

    status: str
```

这里：

> **Graph State ≠ Session。**

这个边界非常重要。

---

# 25. Session、Runtime、GraphState 三者关系

最终：

```text
Session
   │
   │ 1:N
   ▼
Run
   │
   ▼
AnalysisRuntime
   │
   ├── Plan
   ├── Tasks
   ├── Artifacts
   ├── Evidence
   └── Findings
   │
   ▼
LangGraph State
```

简单理解：

### Session

> 用户的一次持续分析会话

### Run

> 一次完整分析执行

### Runtime

> 管理这次 Run

### GraphState

> LangGraph 节点之间传递状态

---

# 26. `Finding` 现在需要做一个很小但非常关键的升级

你现在：

```python
class Finding:
    finding_id
    statement
    evidence
    assumptions
    metric_definitions
    confidence_level
    evidence_level
    hypothesis_flag
    ...
```

已经很好。

增加：

```python
finding_type: Literal[
    "factual",
    "comparative",
    "descriptive",
    "diagnostic",
    "causal_hypothesis",
]
```

再增加：

```python
supported_by: List[str] = Field(default_factory=list)
depends_on: List[str] = Field(default_factory=list)
```

于是：

```text
F003
 │
 ├── supported_by
 │      ├── E011
 │      └── E012
 │
 └── depends_on
        └── F002
```

---

# 27. `EvidenceItem` 基本不用改

你现在这个结构其实已经很成熟。

尤其：

```text
source_artifact_ids
source_execution_ids
source_step_ids
source_asset_ids
time_window
group_dimension
filters
stats
calculation_method
span_id
```

这些全部保留。

唯一建议增加：

```python
verification_status:
    "pending"
    "verified"
    "rejected"
```

以及：

```python
verification_ids: List[str]
```

这样：

```text
Evidence
   ↓
Verification
```

有明确关系。

---

# 28. 新增 `VerificationResult`

建议：

```python
class VerificationResult(BaseModel):

    verification_id: str

    execution_id: str

    artifact_id: Optional[str]

    rule: str

    status: Literal[
        "passed",
        "failed",
        "warning",
    ]

    expected: Optional[Any] = None

    actual: Optional[Any] = None

    message: Optional[str] = None
```

例如：

```json
{
  "rule": "growth_rate_consistency",
  "status": "passed",
  "expected": -0.3158,
  "actual": -0.3158
}
```

---

# 29. 于是 Evidence 就变得真正可靠

完整链路：

```text
compare_periods
      │
      ▼
ExecutionResult
      │
      ▼
Artifact
      │
      ▼
VerificationResult
      │
      ├── passed
      │
      ▼
EvidenceItem
      │
      ▼
Finding
```

而不是：

```text
LLM
 ↓
Finding
 ↓
Evidence
```

这是整个系统可信度提升最大的地方。

---

# 30. 最后一个关键改造：`finish_report`

你现在的 `finish_report` 很重要。

**不要删除。**

但是以后把它从：

```text
Agent Tool
```

逐步改成：

```text
Report Generator
```

输入应该变成：

```python
generate_report(
    goal,
    findings,
    evidence,
    artifacts,
)
```

它不允许：

```text
读取原始 dataframe
重新分析
重新计算
```

只允许：

```text
Verified Evidence
        +
Findings
        +
Charts
        ↓
Report
```

---

# 31. 所以最终 `langgraph_agent.py` 会瘦成这样

最终目标：

```python
# langgraph_agent.py

def build_graph(...):
    return (
        StateGraph(AnalysisGraphState)
        .add_node("load_context", ...)
        .add_node("build_plan", ...)
        .add_node("validate_plan", ...)
        .add_node("execute_task", ...)
        .add_node("verify_result", ...)
        .add_node("collect_evidence", ...)
        .add_node("record_finding", ...)
        .add_node("replan", ...)
        .add_node("generate_report", ...)
        .add_node("evaluate", ...)
        ...
    )


async def run_analysis_stream(...):
    runtime = create_runtime(...)

    graph = build_graph(...)

    async for event in graph.astream(...):
        yield event
```

这才是它最终应该承担的职责。

---

# 32. 实际迁移顺序，我建议严格按照这个来

不要同时改十几个地方。

## Commit 1

```text
schemas.py
```

增加：

```text
TaskType
ExecutorType
AnalysisTask
VerificationResult
Finding.fining_type
Finding.supported_by
Finding.depends_on
Evidence.verification_status
```

---

## Commit 2

新增：

```text
runtime/scheduler.py
runtime/executor.py
```

先不用接 LangGraph。

写单元测试：

```text
Plan
 ↓
Scheduler
 ↓
Task
```

---

## Commit 3

新增：

```text
execution/react_executor.py
execution/python_executor.py
execution/structured_executor.py
```

把：

```text
create_react_agent
python_repl
tool registry
```

分别包进去。

---

## Commit 4

让：

```text
AnalysisRuntime
 ↓
TaskScheduler
 ↓
TaskExecutor
```

真正跑起来。

---

## Commit 5

才改：

```text
langgraph_agent.py
```

把现在的：

```text
直接调用 ReAct
```

替换为：

```text
Runtime → TaskExecutor
```

---

## Commit 6

加入：

```text
verification/
```

---

## Commit 7

加入：

```text
EvidenceCollector
```

---

## Commit 8

加入：

```text
SkillRetriever
```

---

# 33. 现在我建议你暂时不要做的东西

这几个现在都不要碰：

```text
❌ PostgreSQL
❌ 向量数据库
❌ Multi-Agent
❌ 多 Agent 协作
❌ Parallel Agent
❌ MCP 大规模改造
❌ 自动 Skill Evolution
❌ Complex DAG Scheduler
❌ 全面重写 frontend
```

因为你的核心问题现在不是这些。

你现在最重要的是证明：

```text
Question
 ↓
Plan
 ↓
Task
 ↓
Tool
 ↓
Artifact
 ↓
Verification
 ↓
Evidence
 ↓
Finding
 ↓
Report
```

能够稳定跑通。

---

# 34. V2 第一条真正的 E2E 测试

建议直接拿你现有测试数据：

```text
grouped_sales
```

测试问题：

> **分析各部门销售额同比变化，找出下降最大的部门，并说明主要贡献因素。**

期望 Runtime：

```text
Run
 │
 ├── T01 schema
 │
 ├── T02 metric
 │
 ├── T03 period_comparison
 │
 ├── T04 department_breakdown
 │
 ├── T05 contribution
 │
 ├── T06 verification
 │
 ├── T07 findings
 │
 └── T08 report
```

然后必须能够得到：

```text
T03
 ↓
Artifact A03
 ↓
Verification V03
 ↓
Evidence E03
 ↓
Finding F03
```

并且：

```text
F03
 ↓
E03
 ↓
A03
 ↓
Execution E03
 ↓
Task T03
 ↓
Skill period_comparison
 ↓
Dataset D01
```

**这条链跑通以后，你的 DeepAnalyze V2 架构就真正立住了。**

---

## 下一步就不再做设计讨论了

下一步应该直接进入**第一刀代码改造**：

### `schemas.py` → 精确给出 V2 修改后的完整模型

然后紧接着：

```text
runtime/scheduler.py
runtime/executor.py
execution/structured_executor.py
execution/react_executor.py
execution/python_executor.py
```

最后再改 `langgraph_agent.py` 的实际调用链。

这样我们可以按照 **Commit 1 → Commit 2 → Commit 3** 的方式，一步一步改你这个真实仓库，而不是做一次风险很大的“大重构”。

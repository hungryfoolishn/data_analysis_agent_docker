可以。我们现在已经把你这个仓库的**真实代码结构**读了一遍，可以进入真正的 **V2.0 方法级改造设计**了。

我先给结论：

> **你的 DeepAnalyze 已经不是“从 0 到 1”的 Agent 了。现在最重要的不是继续加 Agent，而是把现有的 `runtime + plan + tool registry + evidence + quality + ReAct` 真正串成一条统一执行链。**

我下面按 **“现状 → 问题 → V2目标 → 文件级改造 → 方法级改造 → LangGraph链路 → 实施顺序”** 来拆。

---

# 一、先确定你现在真实的架构

我实际看了你仓库：

[hungryfoolishn/data_analysis_agent_docker](https://github.com/hungryfoolishn/data_analysis_agent_docker?utm_source=chatgpt.com)

目前已经存在：

```text
API
 │
 ▼
api_server_langgraph.py
 │
 ▼
run_analysis_stream()
 │
 ▼
langgraph_agent.py
 │
 ├── _Session
 │     ├── dataframe namespace
 │     ├── stage machine
 │     ├── findings
 │     ├── artifacts
 │     ├── semantic context
 │     └── AnalysisRuntime
 │
 ├── create_react_agent()
 │
 ├── SkillsLoader
 │
 └── tools
        │
        ├── load_data
        ├── eda_profile
        ├── python_repl
        ├── compare_groups
        ├── analyze_time_trend
        ├── decompose_contribution
        ├── detect_anomalies
        ├── compare_periods
        ├── calculate_ratio
        ├── ...
        ├── record_finding
        └── finish_report
```

而且你已经有一个非常重要的东西：

```text
AnalysisRuntime
    │
    ├── AnalysisTask
    ├── AnalysisRun
    ├── RuntimePlanStep
    ├── ExecutionResult
    ├── RuntimeArtifact
    ├── plan
    ├── executions
    ├── findings
    └── persistence
```

这说明你的代码已经开始从：

> Agent-centric

往：

> Runtime-centric

演进了。

---

# 二、但是现在有一个非常明显的“架构断层”

目前实际上存在**两套执行模型**。

## 模型 A：老的 Agent 模型

```text
create_react_agent
      │
      ▼
LLM
      │
      ▼
tool
      │
      ▼
python / analysis
      │
      ▼
finish_report
```

核心控制权：

> LLM

---

## 模型 B：新的 Runtime 模型

你已经开始有：

```text
AnalysisRuntime
      │
      ▼
AnalysisPlan
      │
      ▼
RuntimePlanStep
      │
      ▼
ExecutionResult
      │
      ▼
Artifact
      │
      ▼
Finding
      │
      ▼
Quality
```

核心控制权：

> Runtime

---

## 现在的问题

两套体系目前还没有真正统一。

也就是：

```text
             ┌──────────────┐
             │ AnalysisPlan │
             └──────┬───────┘
                    │
                    │
                    ▼
             AnalysisRuntime
                    │
                    │
                    │
                    X
                    │
                    │
             create_react_agent
                    │
                    ▼
                   LLM
```

很多时候还是：

```text
LLM决定：
下一步做什么
↓
调用什么工具
↓
产生什么结果
↓
什么时候结束
```

Runtime 更多是在旁边：

```text
记录
追踪
持久化
校验
```

而不是：

> **真正控制执行。**

这就是 V2 最重要的改造点。

---

# 三、V2 的核心目标

我要把你的系统从：

```text
ReAct Agent + Runtime记录系统
```

改成：

```text
Runtime 控制系统
        +
ReAct 执行引擎
```

也就是说：

```text
                DeepAnalyze Runtime
                       │
                       ▼
                 Goal Understanding
                       │
                       ▼
                  Analysis Planner
                       │
                       ▼
                  Analysis Plan
                       │
                       ▼
                 Task Scheduler
                       │
          ┌────────────┴────────────┐
          ▼                         ▼
   Structured Tool             ReAct Agent
          │                         │
          └────────────┬────────────┘
                       ▼
                  Execution
                       │
                       ▼
                   Artifact
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
                Replan / Continue
                       │
                       ▼
                    Report
```

注意：

**ReAct 不删除。**

而是从：

> 总控制器

变成：

> Task Executor。

---

# 四、第一处核心改造：`AnalysisTask`

你现在的：

```python
class AnalysisTask(BaseModel):
    task_id: str
    session_id: str
    question: str
    input_asset_ids: list[str]
    constraints: dict[str, Any]
    external_context: Optional[dict[str, Any]]
```

实际上还是比较弱。

它只是：

> 用户请求

而不是：

> Runtime 要执行的任务。

---

## V2 建议

改成：

```python
class AnalysisTask(BaseModel):
    task_id: str
    session_id: str

    objective: str

    task_type: Literal[
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

    skill_id: str | None = None

    input_asset_ids: list[str] = []

    required_inputs: dict[str, Any] = {}

    expected_outputs: list[str] = []

    verification_rules: list[str] = []

    depends_on: list[str] = []

    status: Literal[
        "pending",
        "running",
        "succeeded",
        "needs_revision",
        "failed",
        "skipped",
    ] = "pending"

    retry_count: int = 0

    created_at: str
```

---

# 五、第二处核心改造：`RuntimePlanStep`

你现在这个模型：

```python
class RuntimePlanStep:
    step_id
    objective
    method
    required_inputs
    expected_outputs
    depends_on
    status
```

已经是正确方向。

所以：

> **不要推翻。**

而是把它升级成真正的 Task。

建议：

```python
class RuntimePlanStep(BaseModel):

    step_id: str

    objective: str

    task_type: TaskType

    method: str

    skill_id: str | None

    required_inputs: list[str]

    expected_outputs: list[str]

    verification_rules: list[str]

    depends_on: list[str]

    executor: Literal[
        "structured_tool",
        "react",
        "python",
        "system",
    ]

    status: Literal[
        "pending",
        "running",
        "paused",
        "succeeded",
        "needs_revision",
        "failed",
        "skipped",
    ]

    started_at: str | None
    completed_at: str | None

    error: str | None
```

这里最重要的是：

```python
executor
```

例如：

```text
compare_periods
        ↓
executor = structured_tool
```

而：

```text
“分析为什么华东下降，并寻找可能原因”
        ↓
executor = react
```

---

# 六、第三处核心改造：建立真正的 Task Scheduler

现在你虽然有：

```python
AnalysisRuntime.start_step()
AnalysisRuntime.complete_step()
```

但它还是偏“记录”。

我要新增：

```text
runtime/
    scheduler.py
```

核心：

```python
class TaskScheduler:

    def get_ready_tasks(
        self,
        run: AnalysisRun
    ) -> list[RuntimePlanStep]:
        ...

    def select_next_task(
        self,
        run: AnalysisRun
    ) -> RuntimePlanStep | None:
        ...

    def mark_running(
        self,
        task_id: str
    ):
        ...

    def mark_completed(
        self,
        task_id: str
    ):
        ...

    def mark_failed(
        self,
        task_id: str,
        error: str
    ):
        ...
```

这样：

```text
AnalysisPlan
      ↓
TaskScheduler
      ↓
Ready Tasks
      ↓
TaskExecutor
```

---

# 七、第四处核心改造：增加 `TaskExecutor`

新增：

```text
runtime/executor.py
```

核心：

```python
class TaskExecutor:

    async def execute(
        self,
        task: RuntimePlanStep,
        runtime: AnalysisRuntime,
        session: _Session,
    ) -> ExecutionResult:

        if task.executor == "structured_tool":
            return await self.execute_structured_tool(...)

        if task.executor == "react":
            return await self.execute_react(...)

        if task.executor == "python":
            return await self.execute_python(...)

        raise ValueError(...)
```

于是以后：

```text
Scheduler
    │
    ▼
TaskExecutor
    │
    ├── Structured Tool
    │
    ├── ReAct
    │
    └── Python
```

这一步是整个 V2 的关键。

---

# 八、第五处：把 `create_react_agent()` 降级成 Executor

你现在 `langgraph_agent.py` 中直接依赖：

```python
create_react_agent
```

以后不要让整个系统直接围绕它展开。

改成：

```text
runtime/executors/react_executor.py
```

例如：

```python
class ReactExecutor:

    def __init__(
        self,
        llm,
        tools,
        system_prompt,
    ):
        self.agent = create_react_agent(
            model=llm,
            tools=tools,
            prompt=system_prompt,
        )

    async def execute(
        self,
        task: RuntimePlanStep,
        context: dict,
    ) -> ExecutionResult:

        ...
```

这样你的老 ReAct 能完整保留。

但是它的地位发生变化：

### V1

```text
ReAct
  ↓
控制整个分析
```

### V2

```text
Runtime
  ↓
Task
  ↓
ReactExecutor
  ↓
完成这个 Task
```

这个变化非常重要。

---

# 九、第六处：你现有 `tool_analysis_methods.py` 是非常好的基础

我看了实际代码。

你已经有：

```text
compare_groups
analyze_time_trend
decompose_contribution
detect_anomalies
profile_distribution
analyze_correlation
compare_periods
calculate_ratio
analyze_funnel
analyze_retention
test_group_difference
analyze_concentration
run_sensitivity_check
```

这实际上已经接近：

> Data Analysis Tool Registry

而且每个工具已经有：

```python
SessionExecutionRecorder
```

以及：

```text
Tool
 ↓
compute
 ↓
artifact
 ↓
ExecutionResult
```

这是非常正确的。

---

# 十、所以 V2 不应该继续增加大量 Agent

而应该做：

```text
User Question
      ↓
Planner
      ↓
Task Type
      ↓
Tool Selection
      ↓
Existing Analysis Tool
```

例如：

用户：

> 2026 Q2 哪个部门销售额同比下降最多？

Planner：

```json
{
  "task_type": "comparison",
  "skill_id": "period_comparison",
  "method": "compare_periods",
  "executor": "structured_tool"
}
```

然后：

```text
compare_periods
        ↓
Artifact
        ↓
Verification
        ↓
Evidence
```

而不是：

```text
LLM
 ↓
写 pandas
 ↓
执行
 ↓
再写 pandas
 ↓
执行
```

---

# 十一、Python REPL 的定位也要改变

你目前：

```text
python_repl
```

能力非常强。

但它不应该再是：

> 默认分析工具。

建议变成：

```text
Structured Analysis
       ↓
   能解决？
    /    \
  YES     NO
   ↓       ↓
Tool      ReAct
            ↓
        python_repl
```

也就是说：

### 常规任务

```text
同比
环比
分组
趋势
贡献
异常
相关性
比例
集中度
```

直接结构化工具。

### 非常规任务

```text
复杂业务逻辑
自定义计算
复杂多表处理
特殊统计分析
```

再进入：

```text
ReAct → Python
```

---

# 十二、第七处：Skill 系统要进一步升级

你当前 `SkillsLoader` 已经实现：

```text
Tier 1
metadata

Tier 2
SKILL.md

Tier 3
references
```

这是对的。

但目前还有一个问题：

```python
build_skills_prompt()
```

会把所有 Skill metadata 放进 System Prompt。

随着 Skill 数量增长：

```text
100 skills
 ↓
全部 metadata
 ↓
Prompt越来越大
 ↓
LLM选择能力下降
```

这正好对应你之前问的：

> “很多 skills 导致查找混乱，业界怎么解决？”

---

# 十三、V2 Skill Retrieval 应该改成这样

不要：

```text
Question
 ↓
全部 Skills
 ↓
LLM自己找
```

改：

```text
Question
   ↓
Intent Parser
   ↓
Skill Retriever
   ↓
Top-K
   ↓
Skill Selector
   ↓
Full SKILL.md
```

新增：

```text
skills/
    registry.py
    retriever.py
    selector.py
    loader.py
    models.py
```

---

## Skill Metadata

你现在：

```python
SkillMeta
```

已经不错。

增加：

```python
class SkillMeta(BaseModel):

    name: str

    description: str

    category: str

    tags: list[str]

    trigger_keywords: list[str]

    task_types: list[str]

    required_inputs: list[str]

    output_types: list[str]

    tools: list[str]

    verification_rules: list[str]

    risk_level: str

    version: str
```

这样 Skill 就不只是：

> 一段 Prompt。

而是：

> **Analysis Capability Definition**

---

# 十四、Skill 与 Tool 要建立明确关系

例如：

```yaml
skill:
  id: period_comparison

  task_types:
    - comparison

  tools:
    - compare_periods

  required_inputs:
    - metric
    - date_field

  outputs:
    - period_comparison
    - evidence

  verification:
    - denominator_check
    - period_consistency
```

于是：

```text
Planner
 ↓
period_comparison
 ↓
Tool Registry
 ↓
compare_periods
```

这比：

```text
LLM看到 Skill
 ↓
自己决定怎么做
```

稳定得多。

---

# 十五、第八处：Evidence 是你 V2 最重要的资产

你现在 `EvidenceItem` 已经相当丰富：

```text
source_fields
source_artifacts
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

这部分我建议：

> **不要大改。**

真正需要改的是产生机制。

---

# 十六、Evidence 以后必须由 Runtime 自动产生

现在容易出现：

```text
LLM
 ↓
说：华东下降31%
 ↓
生成 Finding
 ↓
Evidence
```

这是危险的。

V2：

```text
Tool
 ↓
ExecutionResult
 ↓
Artifact
 ↓
Verification
 ↓
EvidenceCollector
 ↓
EvidenceItem
 ↓
Finding
```

新增：

```text
evidence/
    collector.py
    validator.py
    graph.py
```

例如：

```python
class EvidenceCollector:

    def collect(
        self,
        execution: ExecutionResult,
        artifact: RuntimeArtifact,
        verification: VerificationResult,
    ) -> EvidenceItem:
        ...
```

---

# 十七、第九处：新增 Verification Layer

你现在有：

```text
runtime/quality.py
```

但是它更像：

> Run-level quality gate。

还缺一个：

> Execution-level verification。

新增：

```text
verification/
    verifier.py
    numeric.py
    temporal.py
    aggregation.py
    artifact.py
```

---

## 例如同比

工具计算：

```text
2025 = 15.2M
2026 = 10.4M
```

Verifier：

```python
expected = (10.4 - 15.2) / 15.2
```

得到：

```text
-31.58%
```

然后检查：

```text
Tool output = -31.58%
Calculated  = -31.58%

✓
```

然后：

```text
Evidence.status = verified
```

---

# 十八、Finding 模型也不需要推翻

你现在：

```python
Finding
```

已经有：

```text
confidence_level
evidence_level
hypothesis_flag
stats
calculation_method
source_tool
source_step
run_id
```

这是很好的基础。

我只建议增加：

```python
finding_type:
    factual
    comparative
    descriptive
    diagnostic
    causal_hypothesis
```

以及：

```python
supported_by: list[str]      # Evidence IDs
depends_on: list[str]        # Finding IDs
contradicts: list[str]
```

这样：

```text
Finding Graph
```

就建立起来了。

---

# 十九、最终形成 Insight Graph

例如：

```text
F001
销售额同比下降 18.2%
 │
 ├── F002
 │   华东下降 31.4%
 │
 │    ├── F003
 │    产品A下降 45%
 │    │
 │    └── F004
 │         客户B下降 38%
 │
 └── F005
     华南下降 12%
```

每个 Finding 都能反查：

```text
Finding
 ↓
Evidence
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

这会成为你 DeepAnalyze 最核心的竞争力之一。

---

# 二十、`finish_report()` 的位置也要调整

现在它还是 Agent Tool。

以后应该变成：

```text
Verified Findings
       +
Evidence
       +
Insight Graph
       +
Artifacts
       ↓
Report Generator
```

也就是说：

> Report 不再负责“分析”。

它只负责：

> **组织已经验证过的分析结果。**

---

# 二十一、最终 LangGraph 应该变成什么？

我建议不要把整个系统做成一个巨大的 LangGraph。

而是：

```text
                  LangGraph Runtime
                         │
                         ▼
                 ┌───────────────┐
                 │ Goal Parser   │
                 └───────┬───────┘
                         ▼
                 ┌───────────────┐
                 │ Planner       │
                 └───────┬───────┘
                         ▼
                 ┌───────────────┐
                 │ Plan Validator│
                 └───────┬───────┘
                         ▼
                 ┌───────────────┐
                 │ Task Scheduler│
                 └───────┬───────┘
                         ▼
                    TaskExecutor
                    /     |      \
                   /      |       \
             Tool      ReAct     Python
               \         |        /
                \        |       /
                 ▼       ▼      ▼
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

---

# 二十二、所以 `langgraph_agent.py` 应该怎么拆

这是目前最大的文件，也是下一步最应该处理的。

现在它承担了太多职责：

```text
Session
Tool binding
LLM
ReAct
Plan
Artifact
Finding
Report
Streaming
Recovery
Stage
Python namespace
```

我建议最终变成：

```text
langgraph_langchain/
│
├── runtime/
│   ├── context.py
│   ├── models.py
│   ├── plans.py
│   ├── scheduler.py       ← 新
│   ├── executor.py        ← 新
│   └── recovery.py
│
├── planning/
│   ├── planner.py         ← 新
│   ├── goal_parser.py     ← 新
│   └── validator.py       ← 新
│
├── execution/
│   ├── structured.py      ← 新
│   ├── react.py           ← 新
│   └── python.py          ← 新
│
├── verification/
│   ├── verifier.py        ← 新
│   ├── numeric.py         ← 新
│   └── consistency.py     ← 新
│
├── evidence/
│   ├── collector.py       ← 新
│   └── validator.py       ← 新
│
├── insights/
│   ├── generator.py       ← 新
│   └── graph.py           ← 新
│
├── reports/
│   └── generator.py       ← 新
│
├── skills/
│   ├── registry.py        ← 新
│   ├── retriever.py       ← 新
│   ├── selector.py        ← 新
│   └── loader.py
│
└── langgraph_agent.py
       ↓
   只负责 Graph / orchestration
```

---

# 二十三、但是千万不要现在直接拆文件

这个很重要。

**不要一次性重构。**

你的仓库目前已经有很多功能：

```text
runtime
state machine
recovery
SSE
task API
quality
lineage
report rebuild
semantic context
tool registry
```

如果一次重构，极容易把已有功能打坏。

应该：

```text
V2.0
 │
 ├── Step 1 Runtime真正接管Task
 │
 ├── Step 2 Structured Tool优先
 │
 ├── Step 3 Verification
 │
 ├── Step 4 Evidence自动生成
 │
 ├── Step 5 Insight Graph
 │
 ├── Step 6 Skill Retrieval
 │
 └── Step 7 Evaluation / Evolution
```

---

# 二十四、我建议你现在第一刀就改这里

目前：

```text
AnalysisRuntime
       │
       └── propose_default_plan()
```

生成：

```text
load_data
 ↓
eda_profile
 ↓
python_repl
 ↓
record_finding
 ↓
finish_report
```

这个 Plan 太粗。

应该变成：

```text
User Question
      ↓
Goal Parser
      ↓
AnalysisPlan
      ↓
具体 Task
```

例如：

用户：

> 分析2026年Q2各部门销售额同比变化，并找出下降最大的部门以及主要贡献因素

真正的 Plan 应该类似：

```text
T01
schema understanding
    ↓
T02
data quality
    ↓
T03
metric definition
    ↓
T04
period comparison
    ↓
T05
department breakdown
    ↓
T06
contribution analysis
    ↓
T07
verification
    ↓
T08
finding generation
    ↓
T09
report
```

而不是：

```text
T03 = python_repl
```

---

# 二十五、具体到你的代码，V2 第一阶段改造表

| 当前                         | 处理       | V2                  |
| -------------------------- | -------- | ------------------- |
| `PlanStep`                 | 保留兼容     | Legacy              |
| `RuntimePlanStep`          | **升级**   | 核心 Task             |
| `AnalysisTask`             | **升级**   | 用户任务                |
| `AnalysisPlan`             | 保留并增强    | Planner输出           |
| `AnalysisRuntime`          | **保留**   | Runtime核心           |
| `start_step()`             | 保留       | Scheduler调用         |
| `complete_step()`          | 保留       | Executor调用          |
| `create_react_agent()`     | 保留       | ReactExecutor内部     |
| `tool_analysis_methods.py` | **保留**   | Structured Executor |
| `SkillsLoader`             | 保留       | Retriever之下         |
| `EvidenceItem`             | 保留       | Evidence系统          |
| `Finding`                  | 增强       | Insight节点           |
| `ArtifactRef`              | 保留兼容     | RuntimeArtifact为主   |
| `quality.py`               | 保留       | Run-level Gate      |
| `_Session`                 | **逐步瘦身** | Execution Context   |
| `langgraph_agent.py`       | **逐步瘦身** | Orchestrator        |
| `api_server_langgraph.py`  | 基本保留     | API Layer           |

---

# 二十六、第一阶段我建议只新增 5 个文件

不要一下新增几十个。

第一阶段：

```text
runtime/
├── scheduler.py
└── executor.py

planning/
├── planner.py
└── models.py

verification/
└── verifier.py
```

然后把现有：

```text
AnalysisRuntime
RuntimePlanStep
AnalysisPlan
Tool Registry
ReAct
```

串起来。

---

# 二十七、第一阶段的新调用链

最终先做到：

```text
POST /v1/chat/completions
             │
             ▼
     run_analysis_stream()
             │
             ▼
      AnalysisRuntime
             │
             ▼
        AnalysisPlan
             │
             ▼
       TaskScheduler
             │
             ▼
       TaskExecutor
        /          \
       /            \
Structured          ReAct
Tool                Agent
       \            /
        \          /
         Execution
             │
             ▼
          Artifact
             │
             ▼
        Verification
             │
             ▼
          Finding
             │
             ▼
          Report
```

这样你就完成了第一次真正意义上的：

> **Runtime 化。**

---

# 二十八、然后第二阶段才做 Skill Retrieval

顺序一定要这样：

```text
Runtime
 ↓
Task
 ↓
Executor
 ↓
Verification
```

稳定以后再：

```text
Task
 ↓
Skill Retrieval
 ↓
Skill
 ↓
Executor
```

否则 Skill 系统很容易变成“Prompt 装饰层”。

---

# 二十九、第三阶段才接你的 Skill Evolution

到这里，你之前做的 AgentScope Java Skill 自优化项目就可以和 DeepAnalyze 接起来：

```text
DeepAnalyze Run
      ↓
Trace
      ↓
Task
      ↓
Skill
      ↓
Tool
      ↓
Execution
      ↓
Verification
      ↓
Evaluation
      ↓
Failure
      ↓
Skill Failure Case
      ↓
GEPA / Skill Evolution
      ↓
New SKILL.md
      ↓
Golden Evaluation
      ↓
Approval
      ↓
Skill Version
```

这时候你的两个项目实际上就形成了一条完整闭环。

---

# 三十、我建议下一步直接进入“代码级改造”

现在我们已经把真实仓库的：

* `schemas.py`
* `langgraph_agent.py`
* `api_server_langgraph.py`
* `runtime/context.py`
* `runtime/models.py`
* `runtime/plans.py`
* `runtime/quality.py`
* `runtime/events.py`
* `skills_loader.py`
* `tools/registry.py`
* `tools/tool_analysis_methods.py`

都对上了。

**下一步不要再讲架构概念了。**

应该直接做：

### V2.0 第一刀：`langgraph_agent.py` 方法级改造图

具体列成这种格式：

```text
当前方法
    ↓
保留 / 修改 / 移动 / 废弃
    ↓
新文件
    ↓
新方法签名
    ↓
调用方
    ↓
State变化
```

重点把你现在 `langgraph_agent.py` 里的：

```text
_Session.__init__
_make_tools
_run_analysis
_run_analysis_stream
create_react_agent
plan相关逻辑
record_finding
finish_report
artifact处理
runtime处理
stage处理
```

**一个一个拆出来，并画出 V2 的 LangGraph Node + State + 调用链。**

这一步做完，基本就可以直接开始改代码，而不是停留在架构设计层面。

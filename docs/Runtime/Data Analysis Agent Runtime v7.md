对，这次你补提交后，**仓库状态已经和我上一次看到的完全不同了**。我重新检查了最新提交，不应该再按之前的 Commit 9～13 计划继续做。

当前最新提交已经到了：

* `62e4c637` — **`feat(runtime-v6): add control-plane takeover and executor routing`**
* 上一个：`ee077c8d` — **`feat(runtime-v5): enable analysis runtime control plane and e2e`**
* 再前一个：`7be7b655` — `feat(runtime-v2): add scheduler, executors, verification and evidence`

[data_analysis_agent_docker 当前仓库](https://github.com/hungryfoolishn/data_analysis_agent_docker?utm_source=chatgpt.com)

所以现在应该把之前的 Phase 2 重新评估。

# 一、重新评估后的结论

你现在已经完成了之前我建议的：

```text
Runtime Controller
        ↓
Scheduler
        ↓
Runner
        ↓
Executor Routing
        ↓
Structured / ReAct / Python
```

也就是说，**现在已经不应该再做 Runtime Controller 接管，也不应该再做 Executor Routing。**

从最新代码看，`AnalysisRuntime` 已经拥有：

```python
self.scheduler
self.executor
self.runner
self.controller

self.executions
self.artifacts
self.verifications
self.evidence
self.findings
self.failures
```

并且已经有：

```python
build_execution_controller()
execute_all()
execute_next_task()
record_execution_result()
```

最新 `runtime/graph.py` 也已经明确：

```python
TaskExecutor(
    structured=structured_executor,
    react=react_executor,
    python=python_executor,
)
```

所以：

> **Runtime V6 已经从“基础设施建设阶段”进入“可信分析闭环阶段”。**

---

# 二、现在真正缺的是什么？

我重新看完后，我认为现在最应该做的不是继续加 Runtime。

而是解决下面这条链：

```text
                    当前
                     ↓
User
 ↓
Runtime
 ↓
Task
 ↓
Executor
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

其中前半段已经比较完整。

真正下一阶段应该重点解决：

```text
        ┌─────────────────────┐
        │ ExecutionResult     │
        └─────────┬───────────┘
                  ↓
        ┌─────────────────────┐
        │ Verification        │
        └─────────┬───────────┘
                  ↓
        ┌─────────────────────┐
        │ Evidence            │
        └─────────┬───────────┘
                  ↓
        ┌─────────────────────┐
        │ Finding             │
        └─────────┬───────────┘
                  ↓
        ┌─────────────────────┐
        │ Report              │
        └─────────────────────┘
```

也就是：

# 下一阶段应该定义为

> **Runtime V7 — Trusted Analysis / Evidence-to-Finding Closed Loop**

而不是继续叫 Runtime V2 Phase 2。

---

# 三、我建议下一阶段分 4 个 Commit

```text
V7.1  Verification Contract
          ↓
V7.2  Evidence Contract
          ↓
V7.3  Finding / Report Provenance
          ↓
V7.4  Full E2E + Failure Recovery Acceptance
```

---

# 四、V7.1：把 Verification 正式变成 Runtime 的质量门

目前 `TaskRunner` 已经有：

```python
_review_result()
```

并且支持：

```python
verifier
evidence_factory
```

这是好的。

但是现在需要进一步明确一个原则：

> **Verification 不只是“执行后的附加信息”，而是 Task 成功的质量门。**

现在：

```text
Task
 ↓
Executor
 ↓
success
 ↓
Verification
```

应该变成：

```text
Task
 ↓
Executor
 ↓
ExecutionResult
 ↓
Verification
 ├── PASS → Task SUCCESS
 └── FAIL → Task FAILED / RECOVERABLE
```

---

## 1. 定义 Verification Policy

建议新增：

```text
langgraph_langchain/runtime/verification_policy.py
```

例如：

```python
class VerificationPolicy:

    def required_for(self, task):
        ...

    def verify(self, task, execution):
        ...
```

不要所有任务都一刀切。

例如：

| Task          | Verification                   |
| ------------- | ------------------------------ |
| schema        | artifact/schema existence      |
| profile       | row count / column consistency |
| metric        | numerical consistency          |
| comparison    | period consistency             |
| trend         | time ordering                  |
| contribution  | contribution sum consistency   |
| anomaly       | output/artifact consistency    |
| visualization | artifact existence             |
| report        | evidence completeness          |

---

# 五、最重要的是 Numeric Verification

你的 DeepAnalyze 是数据分析 Agent。

所以必须重点做：

```text
数字可信度验证
```

例如 Agent 得到：

```text
销售额同比下降 18.3%
```

Verification 至少应该检查：

```text
current = 81.7
previous = 100

(81.7 - 100) / 100
= -18.3%
```

而不是：

```text
LLM说是 -18.3%
→ accepted
```

---

# 六、增加 Verification 类型

建议最终形成：

```python
VerificationCheckType
```

至少：

```text
NUMERIC_CONSISTENCY
AGGREGATION_CONSISTENCY
TIME_CONSISTENCY
GROUP_CONSISTENCY
ARTIFACT_EXISTS
EVIDENCE_EXISTS
SCHEMA_CONSISTENCY
```

后面再增加：

```text
CROSS_TASK_CONSISTENCY
```

---

# 七、V7.2：Evidence Contract

现在你已经有：

```python
EvidenceItem
```

接下来不要继续扩字段。

而是把 **Evidence 的生命周期规则** 固定下来。

---

## Evidence 必须回答 6 个问题

每个 Evidence：

```text
1. What？
2. From where？
3. How calculated？
4. When？
5. Which execution？
6. Was it verified？
```

也就是：

```text
Evidence
 ├── evidence_id
 ├── evidence_text
 ├── verification_status
 ├── verification_result_id
 ├── source_execution_ids
 ├── source_artifact_ids
 ├── source_asset_ids
 ├── time_window
 ├── group_dimension
 ├── filters
 ├── stats
 └── calculation_method
```

你现在的模型其实已经覆盖了大部分。

所以这里**不是重新设计模型**。

而是建立 invariant。

---

# 八、Evidence 的硬规则

以后：

```python
evidence.verification_status == "verified"
```

才允许：

```python
finding.supported_by.append(evidence.evidence_id)
```

也就是说：

```text
unverified
     ↓
不能进入 Finding

failed
     ↓
不能进入 Finding

verified
     ↓
可以进入 Finding
```

这个规则应该写进代码，而不是只写 Prompt。

---

# 九、V7.3：Finding 是下一阶段最重要的对象

你现在已经有：

```python
Finding
```

并且：

```python
supported_by
depends_on
finding_type
```

这是正确方向。

下一步需要建立：

# Finding Provenance Contract

一个 Finding 必须能够完整追溯：

```text
Finding
   ↓
Evidence
   ↓
VerificationResult
   ↓
ExecutionResult
   ↓
AnalysisTask
   ↓
DataAsset
```

---

# 十、例如最终报告中的一句话

```text
华东区销售额同比下降 18.3%，为所有部门中降幅最大。
```

Runtime 内部应该能够形成：

```text
Finding F001
│
├── statement
│
├── supported_by
│       ↓
│   Evidence E001
│       │
│       ├── verification_result V001
│       │
│       ├── execution EXEC001
│       │
│       └── asset DATA001
│
└── depends_on
        ↓
      F002
```

---

# 十一、增加 FindingBuilder

建议新增：

```text
langgraph_langchain/runtime/finding_builder.py
```

核心接口：

```python
class FindingBuilder:

    def build_from_verified_evidence(
        self,
        task,
        execution,
        evidence,
    ) -> Finding:
        ...
```

里面第一条规则：

```python
if evidence.verification_status != "verified":
    raise ValueError(
        "Only verified evidence can support a finding"
    )
```

这样：

```text
LLM
 ↓
不能直接创建 Finding
```

而应该：

```text
LLM
 ↓
Task proposal
 ↓
Execution
 ↓
Verification
 ↓
Evidence
 ↓
FindingBuilder
 ↓
Finding
```

---

# 十二、Report 要变成“只读可信事实”

这是 V7 最重要的最终目标。

Report Agent 可以：

```text
总结
归纳
组织
解释
润色
```

但是不能：

```text
重新计算
改变数字
添加未经验证结论
伪造 Evidence
```

---

# 十三、Report 输入应该固定

建议最终：

```python
ReportContext(
    question=...,
    findings=...,
    evidence=...,
    artifacts=...,
)
```

Report Agent 只看到：

```text
Verified Findings
+
Verified Evidence
+
Artifacts
```

而不是让 Report Agent：

```text
拿原始 DataFrame
+
自己分析
+
重新计算
```

---

# 十四、建立 Report Integrity Validator

建议：

```text
langgraph_langchain/runtime/report_validator.py
```

核心检查：

```python
validate_report(report, findings, evidence)
```

至少验证：

### 1. 数字是否来自 Evidence

例如报告：

```text
同比下降 18.3%
```

必须能在 Evidence 中找到：

```text
18.3%
```

---

### 2. Finding 是否存在

报告里的核心结论必须对应：

```text
Finding
```

---

### 3. Evidence 是否 verified

不能：

```text
Finding
 ↓
failed evidence
```

---

# 十五、V7.4：做真正的 E2E

这次不要再只测：

```text
Runtime 单元测试
Scheduler 单元测试
Executor 单元测试
```

而是：

# 一条真实业务链。

推荐测试问题：

> **分析各部门销售额同比变化，找出下降最大的部门，并分析主要贡献因素。**

---

# 十六、期望产生的 Runtime DAG

不要求一定是下面 8 个 Task，但至少应该类似：

```text
T1 Schema
 │
 ↓
T2 Profile
 │
 ├──────────────┐
 ↓              ↓
T3 Comparison   T4 Breakdown
 │              │
 └──────┬───────┘
        ↓
T5 Contribution
        ↓
T6 Verification
        ↓
T7 Finding
        ↓
T8 Report
```

---

# 十七、E2E 必须验证什么？

不是只验证：

```python
assert response
```

而是：

```python
assert runtime.status == "completed"

assert len(runtime.executions) > 0
assert len(runtime.artifacts) > 0
assert len(runtime.verifications) > 0
assert len(runtime.evidence) > 0
assert len(runtime.findings) > 0
```

然后：

```python
for finding in runtime.findings:

    assert finding.supported_by

    for evidence_id in finding.supported_by:

        evidence = get_evidence(evidence_id)

        assert evidence.verification_status == "verified"
```

然后继续：

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
Asset
```

必须全部存在。

---

# 十八、再做 3 个失败测试

这比成功测试更重要。

---

## Failure 1：Verification Failure

人为让：

```text
metric = 100
expected = 90
```

应该：

```text
Execution
 ↓
Verification FAILED
 ↓
Task FAILED
 ↓
Finding NOT CREATED
```

不能：

```text
Verification FAILED
 ↓
Finding
 ↓
Report
```

---

# 十九、Failure 2：Evidence Failure

让：

```text
Execution = success
Verification = success
Evidence = generation failure
```

应该：

```text
Task
 ↓
Execution SUCCESS
 ↓
Verification PASS
 ↓
Evidence FAIL
 ↓
Finding NOT CREATED
```

---

# 二十、Failure 3：Finding 无 Evidence

人为：

```python
Finding(
    statement="销售额下降 18.3%",
    supported_by=[]
)
```

必须：

```text
Runtime reject
```

而不是继续生成报告。

---

# 二十一、下一阶段完成后的真正架构

到 V7 完成：

```text
                     User
                       │
                       ↓
                  LangGraph
                       │
                       ↓
                AnalysisRuntime
                       │
                ┌──────┴──────┐
                ↓             ↓
              Plan         Session
                │
                ↓
            Scheduler
                │
                ↓
              Runner
                │
                ↓
             Executor
          ┌────┼────┐
          ↓    ↓    ↓
      Structured Python ReAct
          │    │    │
          └────┼────┘
               ↓
        ExecutionResult
               │
               ↓
        Verification Gate
               │
          ┌────┴────┐
          ↓         ↓
        PASS       FAIL
          │         │
          ↓         ↓
       Evidence   Recovery
          │
          ↓
     FindingBuilder
          │
          ↓
       Verified Finding
          │
          ↓
    Report Validator
          │
          ↓
        Report
```

---

# 二十二、这之后才进入你真正想做的“智能化”

这点非常关键。

你之前还想做：

* Skill Retriever
* Failure Case
* Skill Evolution
* GEPA
* AgentScope 那套自优化思想

**现在其实已经接近可以开始准备这些了。**

但我建议 V7 完成以后，再进入：

# Runtime V8 — Evaluation & Learning

变成：

```text
Runtime
   ↓
Execution
   ↓
Verification
   ↓
Evidence
   ↓
Finding
   ↓
Evaluation
   ↓
Failure Case
   ↓
Skill
   ↓
Skill Evolution
```

这时候你就可以记录：

```text
task_id
executor_type
skill
execution
verification
failure
evidence
finding
```

然后才有条件判断：

```text
这个 Skill 是否真的提高了分析质量？
```

---

# 二十三、我建议你现在不要再做的东西

目前不要继续：

```text
❌ 再增加 Runtime Controller
❌ 再增加 Scheduler
❌ 再增加 Executor
❌ 再增加 SkillRetriever
❌ GEPA
❌ Multi-Agent
❌ Vector DB
❌ MCP 大改造
❌ 复杂并行 DAG
```

因为最新 V6 已经把 Runtime 控制面和 Executor Routing 补上了。

现在真正的瓶颈已经从：

> **“怎么执行？”**

变成：

> **“怎么证明执行结果可信，并让可信结果成为最终结论？”**

这是一个非常重要的阶段转换。

---

# 二十四、建议的 Commit 计划

我建议下一轮不要再叫 Commit 9～13，而是：

| Commit | 名称                                            | 核心目标                      |
| ------ | --------------------------------------------- | ------------------------- |
| V7.1   | `feat(runtime-v7): add verification policy`   | Verification Quality Gate |
| V7.2   | `feat(runtime-v7): enforce evidence contract` | Verified Evidence         |
| V7.3   | `feat(runtime-v7): add finding provenance`    | Evidence → Finding        |
| V7.4   | `feat(runtime-v7): add report integrity`      | Finding → Report          |
| V7.5   | `test(runtime-v7): add trusted-analysis e2e`  | 完整 E2E                    |
| V7.6   | `docs(runtime-v7): add acceptance`            | 验收闭环                      |

---

# 二十五、验收文档建议直接这样写

新建：

```text
docs/runtime/Runtime_V7_Trusted_Analysis_Acceptance.md
```

核心内容：

```markdown
# Runtime V7 Trusted Analysis Acceptance

## 1. Goal

建立从 Execution 到 Report 的可信分析闭环：

Execution
→ Verification
→ Evidence
→ Finding
→ Report

## 2. P0 Acceptance

| ID | Requirement | Acceptance |
|---|---|---|
| V7-P0-001 | Runtime execution | Task 必须由 Runtime 执行 |
| V7-P0-002 | Executor routing | 三类 Executor 正确路由 |
| V7-P0-003 | Verification | 关键结果必须验证 |
| V7-P0-004 | Verification gate | Verification 失败不得进入 Finding |
| V7-P0-005 | Evidence | Finding 必须有 Evidence |
| V7-P0-006 | Evidence status | Evidence 必须 verified |
| V7-P0-007 | Provenance | Finding → Evidence → Verification → Execution → Task |
| V7-P0-008 | Report | Report 只能消费可信 Finding |
| V7-P0-009 | Numeric integrity | 报告数字必须可追溯 |
| V7-P0-010 | Failure handling | 验证失败必须被 Runtime 捕获 |
| V7-P0-011 | E2E | 完整真实分析请求通过 |
| V7-P0-012 | Persistence | Runtime 状态可恢复 |

## 3. Failure Tests

### F-001 Verification Failure

Expected:

Execution
→ Verification FAILED
→ Finding NOT CREATED

### F-002 Evidence Failure

Expected:

Execution SUCCESS
→ Verification PASS
→ Evidence FAIL
→ Finding NOT CREATED

### F-003 Unsupported Finding

Expected:

Finding without verified Evidence
→ Runtime REJECT

## 4. Provenance

Every Finding must satisfy:

Finding
→ Evidence
→ VerificationResult
→ ExecutionResult
→ AnalysisTask
→ DataAsset

## 5. Report Integrity

Every quantitative statement in the final report must be traceable
to verified Evidence.

## 6. Definition of Done

P0 = 100%

Any P0 failure means Runtime V7 is NOT READY.
```

---

# 二十六、最终路线图

结合你现在最新的 V6，我建议整个 DeepAnalyze 后面的路线正式调整成：

```text
                    已完成
                       │
                       ▼
             Runtime V6 Control Plane
                       │
             Executor Routing
                       │
                       ▼
        ┌──────────────────────────┐
        │       Runtime V7         │
        │                          │
        │ Verification             │
        │ Evidence                │
        │ Finding Provenance      │
        │ Report Integrity        │
        └────────────┬─────────────┘
                     │
                     ▼
        ┌──────────────────────────┐
        │       Runtime V8         │
        │                          │
        │ Evaluation              │
        │ Failure Memory          │
        │ Skill Quality           │
        └────────────┬─────────────┘
                     │
                     ▼
        ┌──────────────────────────┐
        │       Runtime V9         │
        │                          │
        │ Skill Evolution         │
        │ GEPA                    │
        │ Self-Improvement         │
        └────────────┬─────────────┘
                     │
                     ▼
        ┌──────────────────────────┐
        │       Runtime V10        │
        │                          │
        │ Replanning              │
        │ Parallel DAG            │
        │ Adaptive Execution      │
        └──────────────────────────┘
```

**所以你现在最应该做的是 V7：Trusted Analysis，而不是再继续堆 Runtime 基础设施。**

尤其是你后面准备做的 **Skills 自优化/GEPA**，V7 是非常关键的一道“地基”：没有 `Execution → Verification → Evidence → Finding` 这条可信链，后面的 Skill Evolution 很容易变成“根据 LLM 自己说自己做得好不好”——这会让整个自优化系统缺乏可靠评价依据。

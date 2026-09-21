继续。基于我刚刚重新核对的仓库最新 V8 提交，**下一阶段我建议正式进入 Runtime V8.5，而不是马上做 V9 Skill Evolution**。

目前 V8 已经把“运行结果 → 学习信号”打通了，但它现在更像一个 **Learning Telemetry MVP**：能够记录成功/失败、FailureCase、SkillQuality，但还不能可靠回答：

> **“这个 Agent 到底分析对没有？哪个 Skill 真正有效？为什么失败？一个 Skill 修改后到底有没有变好？”**

这正是下一阶段要补齐的。

---

# 一、下一阶段总体目标

我建议把 Runtime 的演进明确成：

```text
                    Runtime V8
                        │
                        ▼
              Task Evaluation
                        │
             ┌──────────┴──────────┐
             ▼                     ▼
        Failure Memory        Skill Quality
             │                     │
             └──────────┬──────────┘
                        ▼
              ┌──────────────────┐
              │    Runtime 8.5   │
              │ Evaluation Infra │
              └────────┬─────────┘
                       │
       ┌───────────────┼────────────────┐
       ▼               ▼                ▼
 Golden Dataset   Multi-Dim Score   Skill Version
       │               │                │
       └───────────────┼────────────────┘
                       ▼
              Reliable Evaluation
                       │
             ┌─────────┴─────────┐
             ▼                   ▼
      Cross Task Verify    Failure Taxonomy
             │                   │
             └─────────┬─────────┘
                       ▼
                Claim Provenance
                       │
                       ▼
              Runtime V8.5 Stable
                       │
                       ▼
                Runtime V9
              Skill Evolution
                 GEPA/DSPy
```

这里有一个非常重要的原则：

**V8.5 的目标不是“让 Agent 学会修改 Skill”，而是先建立一个可信的评价体系。**

否则 V9 做 Skill Evolution 时，会出现一个非常严重的问题：

> GEPA 优化出来的 Skill 到底是真的变好了，还是只是评价器变宽松了？

---

# 二、V8.5 我建议拆成 8 个 Commit

不要一次性大改。

## Commit 1：Golden Dataset

这是现在最优先的。

新增：

```text
tests/
└── evaluation/
    ├── golden_cases/
    │   ├── q2_1_metrics.json
    │   ├── monthly_trend.json
    │   ├── department_compare.json
    │   ├── yoy_analysis.json
    │   └── anomaly_detection.json
    │
    ├── expected/
    │   └── ...
    │
    └── evaluation_runner.py
```

每个 Golden Case 至少包含：

```json
{
  "case_id": "q2_1_dept_yoy_001",
  "question": "杭州开发二部7月Q2-1达标率同比是多少？",
  "dataset": "xxx.csv",

  "expected": {
    "metric": "Q2-1达标率",
    "value": 0.82,
    "tolerance": 0.001,
    "period": "2026-07",
    "dimension": "部门",
    "filter": {
      "department": "杭州开发二部"
    }
  },

  "required_evidence": true,
  "required_sql": true,
  "required_finding": true
}
```

第一批不要做 500 个。

**30～50 个就够。**

建议：

| 类型    | 数量 |
| ----- | -: |
| 单指标   |  8 |
| 多指标   |  6 |
| 同比/环比 |  8 |
| 分组比较  |  8 |
| 趋势    |  5 |
| 异常    |  5 |
| 综合分析  |  5 |

总计：

**45～50 cases。**

---

# 三、Commit 2：Evaluation Runner

新增：

```text
langgraph_langchain/runtime/evaluation/
├── __init__.py
├── models.py
├── runner.py
├── comparator.py
└── golden_loader.py
```

核心接口：

```python
class GoldenEvaluationRunner:

    def run_case(
        self,
        case: GoldenCase,
    ) -> CaseEvaluation:
        ...

    def run_dataset(
        self,
        cases: list[GoldenCase],
    ) -> EvaluationRun:
        ...
```

最终输出：

```text
EvaluationRun
│
├── total_cases
├── passed_cases
├── failed_cases
├── pass_rate
├── average_score
│
├── by_task_type
├── by_executor
├── by_skill
└── failures
```

例如：

```json
{
  "run_id": "eval_20260921_001",
  "total_cases": 50,
  "passed": 43,
  "failed": 7,
  "pass_rate": 0.86
}
```

但是这里有一个关键点：

**不能把 `passed` 简单等同于 Runtime V8 的 `TaskEvaluation.passed`。**

V8 的：

```python
passed=True
```

目前更多表示：

> Runtime 执行链没有发生关键失败。

而 Golden Evaluation 要判断：

> **分析结果本身是否正确。**

这是两个不同层次。

---

# 四、Commit 3：Multi-Dimensional Evaluation

这是 V8.5 最核心的一步。

现在：

```text
passed = True / False
```

信息量太低。

改成：

```python
class EvaluationScore(BaseModel):

    execution_score: float
    verification_score: float
    numeric_score: float
    evidence_score: float
    finding_score: float
    report_score: float

    latency_score: float | None = None
    cost_score: float | None = None

    total_score: float
```

建议初期：

```text
Total Score

= 0.10 × execution
+ 0.10 × verification
+ 0.30 × numeric
+ 0.15 × evidence
+ 0.15 × finding
+ 0.20 × report
```

**注意：这个权重只是工程初始配置，不是最终标准。**

以后可以通过 Golden Dataset 调整。

---

# 五、最重要：Numeric Correctness

对于 Data Analysis Agent，这一项应该成为核心。

例如用户问：

> 2026 年 7 月 Q2-1 达标率是多少？

Agent 输出：

```text
82.5%
```

Golden：

```text
82.3%
```

如果 tolerance：

```text
0.001
```

那么：

```text
abs(0.825 - 0.823)
= 0.002
```

直接判定：

```text
FAIL
```

而不是因为：

```text
Execution SUCCESS
Verification SUCCESS
Report SUCCESS
```

就认为整个任务成功。

---

# 六、Commit 4：Skill Version / Hash

这个必须在 V9 之前完成。

现在 V8 的 SkillQuality 基本按照：

```text
skill_name
```

统计。

这在 Skill Evolution 之后会出问题。

例如：

```text
q2_1_by_dept_version_metrics
```

存在：

```text
v1
v2
v3
```

如果都统计在一起：

```text
attempts = 100
success = 82
```

你根本不知道：

```text
v1 → 90%
v2 → 75%
v3 → 94%
```

所以必须增加：

```python
skill_id
skill_name
skill_version
skill_hash
```

例如：

```json
{
  "skill_id": "q2_1_by_dept_version_metrics",
  "skill_version": "1.3.0",
  "skill_hash": "sha256:abc123..."
}
```

Evaluation 必须记录：

```text
TaskEvaluation
        │
        └── skill
             ├── skill_id
             ├── version
             └── hash
```

以后才能形成：

```text
Skill v1
   ↓
Golden Evaluation
   ↓
82%

Skill v2
   ↓
Golden Evaluation
   ↓
91%
```

这才有资格进入 Evolution。

---

# 七、Commit 5：重新设计 SkillQuality

当前 V8：

```text
attempts
successes
failures
success_rate
quality_score
recommendation
```

方向没问题。

但有一个统计学问题：

```text
Skill A:
2 / 2 = 100%

Skill B:
94 / 100 = 94%
```

现在 A 可能比 B 得到更高评价。

这是不合理的。

建议增加：

```python
SkillQuality:

    attempts
    successes
    failures

    success_rate
    smoothed_success_rate

    recent_success_rate
    historical_success_rate

    confidence

    task_type_success_rate
    executor_success_rate

    quality_score
```

例如采用简单 Bayesian smoothing：

```text
smoothed_rate =
(successes + α)
/
(attempts + α + β)
```

初始：

```text
α = 1
β = 1
```

这样：

```text
2/2
```

不会被直接认为是绝对可靠。

---

# 八、Commit 6：Structured Failure Taxonomy

V8 现在：

```text
execution
verification
evidence
finding
report
cancelled
```

作为第一层非常好。

V8.5 不要删除，而是变成：

```text
stage
category
root_cause
symptom
```

例如：

```json
{
  "stage": "verification",
  "category": "numeric_consistency",
  "root_cause": "wrong_time_window",
  "symptom": "yoy_value_mismatch"
}
```

另一个：

```json
{
  "stage": "execution",
  "category": "tool_selection",
  "root_cause": "wrong_tool",
  "symptom": "csv_tool_used_for_database_task"
}
```

再比如：

```json
{
  "stage": "analysis",
  "category": "workflow",
  "root_cause": "missing_group_by",
  "symptom": "dimension_analysis_missing"
}
```

这一步实际上是在给未来的 Skill Evolution 准备“训练数据”。

---

# 九、Commit 7：Cross-Task Verification

这一项我建议放在 V8.5 后半段。

因为数据分析 Agent 会出现一个很隐蔽的问题：

```text
Task A：
7月达标率 = 82%

Task B：
7月达标率 = 79%
```

两个 Task 单独看：

```text
A → PASS
B → PASS
```

但是：

```text
A ↔ B
```

矛盾。

所以增加：

```python
class CrossTaskConsistencyVerifier:

    def verify(
        self,
        evaluations: list[TaskEvaluation],
    ) -> list[ConsistencyIssue]:
        ...
```

比较：

```text
metric
dimension
filters
period
population
value
```

最终：

```text
Task Evaluation
       ↓
Cross Task Verification
       ↓
Consistency Issue
```

这会让 DeepAnalyze 从“单次分析正确”向“整个 Session 分析一致”迈一步。

---

# 十、Commit 8：Claim-Level Provenance

这是我非常建议你做的一项。

现在 V7 已经有：

```text
Execution
 ↓
Verification
 ↓
Evidence
 ↓
Finding
 ↓
Report
```

V8.5 再往前走一步：

```text
Report Claim
      ↓
Finding
      ↓
Evidence
      ↓
Artifact
      ↓
Execution
```

定义：

```python
class ReportClaim(BaseModel):

    claim_id: str
    text: str

    finding_ids: list[str]
    evidence_ids: list[str]

    confidence: float
```

例如报告：

> 杭州开发二部 7 月 Q2-1 达标率为 82.3%，同比下降 3.2 个百分点。

应该能追踪：

```text
Claim C001
│
├── Finding F017
│
├── Evidence E032
│     └── Artifact A021
│           └── Execution X109
│
└── Evidence E033
      └── Artifact A022
            └── Execution X110
```

这样最终报告不再只是：

```text
LLM 生成的一段文字
```

而是：

```text
可验证的分析结论集合
```

---

# 十一、最终 V8.5 架构

完成以后，我建议 Runtime 达到这个结构：

```text
                    AnalysisRuntime
                          │
        ┌─────────────────┼─────────────────┐
        │                 │                 │
        ▼                 ▼                 ▼
    Scheduler          Executor         Controller
        │                 │
        └──────────┬──────┘
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
             Report Claim
                   │
                   ▼
                 Report
                   │
                   ▼
            Task Evaluation
                   │
       ┌───────────┼────────────┐
       ▼           ▼            ▼
 FailureCase   SkillQuality   Score
       │           │            │
       └───────────┼────────────┘
                   ▼
            Learning Memory
                   │
                   ▼
         Golden Evaluation
                   │
                   ▼
        Cross Task Verification
```

---

# 十二、V9 才开始真正做 Skill Evolution

等 V8.5 完成以后，再做：

```text
FailureCase
     ↓
Failure Cluster
     ↓
Skill Weakness
     ↓
Evolution Candidate
     ↓
SKILL.md Mutation
     ↓
Candidate Skill Version
     ↓
Golden Evaluation
     ↓
Regression Evaluation
     ↓
Baseline vs Candidate
     ↓
Regression Gate
     ↓
Manual Approval
     ↓
Publish
```

而不是：

```text
失败
 ↓
LLM
 ↓
直接修改 Skill
```

后者风险非常高。

---

# 十三、我建议你现在直接按这个顺序实施

| 阶段     | 内容                             | 优先级    |
| ------ | ------------------------------ | ------ |
| V8.5.1 | Golden Dataset                 | **P0** |
| V8.5.2 | Offline Evaluation Runner      | **P0** |
| V8.5.3 | Multi-dimensional Score        | **P0** |
| V8.5.4 | Skill Version/Hash             | **P0** |
| V8.5.5 | Confidence-aware SkillQuality  | **P0** |
| V8.5.6 | Structured Failure Taxonomy    | P1     |
| V8.5.7 | Cross-task Verification        | P1     |
| V8.5.8 | Claim-level Provenance         | P1     |
| V8.5.9 | SQLite/Postgres Learning Store | P1     |
| V9.0   | Skill Evolution / GEPA         | P2     |

---

# 十四、最关键的验收指标

V8.5 不要再只验：

```text
pytest 全绿
```

应该增加真正的 Agent Evaluation：

### A. Runtime 正确性

```text
100% Task 都产生 Evaluation
100% Evaluation 可恢复
100% Evaluation 有 lineage
```

### B. Golden Evaluation

第一阶段：

```text
≥ 50 Golden Cases
```

每个 Case：

```text
Question
Dataset
Expected Answer
Tolerance
Expected Evidence
Expected Finding
```

### C. Evaluation 可重复

同一个 Case：

```text
Run #1
Run #2
Run #3
```

评价结果应该能够解释差异，而不是简单：

```text
PASS
PASS
FAIL
```

### D. Skill 可比较

必须可以得到：

```text
Skill v1.0
    82.1%

Skill v1.1
    88.7%

Skill v1.2
    91.3%
```

并且知道：

```text
为什么提高
在哪里提高
在哪里退化
```

### E. Evolution 安全性

未来任何 Candidate：

```text
Candidate Score > Baseline
AND
Critical Regression = 0
AND
Golden Pass Rate >= threshold
```

才允许进入：

```text
Manual Approval
```

**绝不能让自动优化直接覆盖生产 Skill。**

---

# 十五、你这个项目现在真正缺的是什么

我重新看 V8 的实现后，我认为现在不是缺：

```text
Agent
Tool
Executor
Scheduler
Memory
SkillRetriever
```

这些 Runtime 基础设施已经逐渐成型。

现在真正缺的是：

> **Evaluation Infrastructure（评价基础设施）**

也就是从：

```text
“Agent 做完了”
```

升级成：

```text
“Agent 做完了
 → 我知道它做了什么
 → 我知道结果是否可信
 → 我知道哪里错了
 → 我知道哪个 Skill 导致的
 → 我能用 Golden Dataset 重现
 → 我能比较两个 Skill 版本
 → 最终才能安全地自动优化”
```

这一步完成后，**Runtime V9 的 GEPA / Skill Evolution 才真正有工程基础。**

另外，你现在 V8 已经明确把 **Skill 自动修改、GEPA、vector DB、parallel DAG replanning 等列为 Non-Goals**，这个边界我建议继续保持，不要在 V8.5 混进去。

如果按照实际编码顺序，我建议**下一次直接开始做 V8.5.1 + V8.5.2**：我可以进一步给你拆成 **具体到仓库文件、Python 类、方法签名、数据结构、pytest 用例，以及 Commit 1～8 的 Codex 执行清单**，这样你可以直接丢给 Codex 实施。

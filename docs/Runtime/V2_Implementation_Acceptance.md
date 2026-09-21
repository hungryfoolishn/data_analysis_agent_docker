# Data Analysis Agent Runtime V2 实施验收方案

- 文档状态：待评审
- 实施基线：`docs/Runtime/Data Analysis Agent Runtime v4.md`
- 参考设计：
  - `docs/Runtime/Data Analysis Agent Runtime v2.md`
  - `docs/Runtime/Data Analysis Agent Runtime v3.md`
  - `docs/Runtime/Data Analysis Agent Runtime v4.md`
- 目标版本：Runtime V2.0
- 适用环境：`docker-host` / `/python/pragrams/data_analysis_agent`
- 推荐执行环境：Conda `analysis`

---

## 1. 文档目的

本文档定义 Data Analysis Agent Runtime V2 改造的实施范围、验收标准、分阶段交付门槛、端到端验收场景、回归要求和回滚策略。

它不是新的架构设计文档。架构方向以 Runtime V2/V3/V4 设计文档为准；本文档只回答一个问题：

> Runtime V2 什么时候可以被认为实施完成，并且可以进入后续迭代？

---

## 2. 实施基线

Runtime V4 是本阶段的实施基线。

| 文档 | 定位 | 使用方式 |
|---|---|---|
| Runtime V2 | 架构方向和核心概念 | 理解 Evidence-Centered Runtime 的目标 |
| Runtime V3 | 方法级改造设计 | 理解 Runtime 与 ReAct 的控制权转移 |
| Runtime V4 | 实施基线 | 按该文档落地代码与测试 |
| 本文档 | 实施验收 | 判断每一阶段是否达到交付标准 |

实施过程中如果发现 V4 设计需要调整，必须：

1. 先更新 V4 或新增补充设计；
2. 再同步更新本验收文档；
3. 最后修改代码和测试；
4. 不允许只改代码而不更新验收标准。

---

## 3. 目标

### 3.1 总体目标

将现有系统从：

```text
ReAct Agent + Runtime 记录系统
```

升级为：

```text
Runtime 控制系统 + ReAct 执行引擎
```

### 3.2 目标链路

V2 必须稳定支持以下主链路：

```text
Question
  ↓
Goal Understanding
  ↓
Analysis Plan
  ↓
Task Scheduler
  ↓
Task Executor
  ↓
Tool / ReAct / Python
  ↓
ExecutionResult
  ↓
Artifact
  ↓
VerificationResult
  ↓
EvidenceItem
  ↓
Finding
  ↓
Report
```

### 3.3 核心控制权要求

Runtime 必须承担以下职责：

1. 持有当前 Run、Plan 和 Task 状态；
2. 调度任务执行顺序；
3. 选择 Structured、ReAct 或 Python Executor；
4. 接收 ExecutionResult；
5. 触发 Verification；
6. 生成 Evidence；
7. 维护 Finding 与 Evidence、Artifact、Execution、Task 的关联；
8. 驱动报告生成；
9. 处理失败和恢复。

ReAct Agent 只能作为 Executor 存在，不能再作为主控制流。

---

## 4. 非目标

本阶段明确不做以下内容：

```text
❌ PostgreSQL
❌ 向量数据库
❌ Multi-Agent
❌ Parallel Agent
❌ 大规模 MCP 改造
❌ 自动 Skill Evolution
❌ Complex DAG Scheduler
❌ 全面重写前端
```

如果实施过程中涉及以上内容，必须先重新评审范围。

---

## 5. 核心对象验收定义

### 5.1 AnalysisTask

验收要求：

- 能表达一个用户可理解的分析任务；
- 具有稳定的 `task_id`；
- 有明确的 `task_type`；
- 指定执行器类型；
- 声明依赖任务；
- 声明输入数据或上游产物；
- 声明期望输出；
- 能完整序列化和反序列化。

不允许出现：

- 只有自然语言描述、无法调度的任务；
- 依赖关系缺失导致无法判断执行顺序；
- 无法追踪到执行结果的任务。

### 5.2 RuntimePlanStep

验收要求：

- 是 Scheduler 调度的基础单位；
- 保存任务状态；
- 记录开始时间、结束时间、失败信息；
- 能关联 ExecutionResult；
- 能关联 Artifact、VerificationResult 和 EvidenceItem。

### 5.3 ExecutionResult

验收要求：

- 由 Executor 统一返回；
- 包含执行状态；
- 包含错误信息；
- 包含产物引用；
- 可关联使用的 Tool、Skill 或 ReAct 会话；
- 不允许执行器只返回字符串而丢失结构化上下文。

### 5.4 RuntimeArtifact

验收要求：

- 文件真实存在；
- 路径可解析；
- 类型明确；
- 大小、哈希或校验信息可用；
- 可追溯到产生它的 Execution；
- 可被 Verification、Evidence 和 Report 使用。

### 5.5 VerificationResult

验收要求：

- 明确 `passed` 状态；
- 记录检查项和检查结果；
- 关联被验证的 Artifact 或 Execution；
- 记录失败原因；
- 支持机器判断和人工审查。

### 5.6 EvidenceItem

验收要求：

- Evidence 必须由 Runtime 从执行和验证结果中产生；
- 必须关联 VerificationResult 或等价验证信息；
- 必须能追溯到 Artifact、Execution 和 Task；
- 不允许报告阶段凭空构造 Evidence；
- 未通过验证的数据不能升级为可信 Evidence。

### 5.7 Finding

验收要求：

- Finding 必须引用至少一个 EvidenceItem；
- 必须声明结论类型；
- 必须能反向追溯到数据源、任务、执行、产物和验证结果；
- 不允许出现没有 Evidence 支撑的结论。

### 5.8 Report

验收要求：

- 报告只能基于 Finding、Evidence 和 Artifact 生成；
- 报告生成阶段不允许重新读取原始 DataFrame 做主分析；
- 报告不得引入没有 Evidence 支撑的新结论；
- 每个关键结论都可回溯。

---

## 6. 分阶段验收标准

### Commit 1：Schema 与模型基础

交付内容：

```text
schemas.py
```

新增或升级：

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

> 注意：字段名如与现有模型冲突，可以调整命名，但语义必须保留，并同步更新文档。

验收标准：

1. 模型字段完整；
2. Pydantic 校验通过；
3. JSON 序列化和反序列化通过；
4. 旧字段不破坏现有 API；
5. 新模型能表达 V4 设计中的任务、验证和结论依赖；
6. 单元测试通过。

必须提供的测试证据：

```text
Schema round-trip test
Schema validation test
Legacy compatibility test
```

### Commit 2：TaskScheduler

交付内容：

```text
runtime/scheduler.py
```

验收标准：

1. 能接收 AnalysisPlan；
2. 能生成可执行 Task 队列；
3. 能处理依赖顺序；
4. 能标记任务状态；
5. 能拒绝非法计划；
6. 能识别缺失依赖；
7. 能避免循环依赖；
8. 单元测试通过。

必须覆盖：

```text
空 Plan
单任务 Plan
多任务依赖 Plan
无依赖任务
依赖缺失
循环依赖
重复 task_id
非法 task_type
```

### Commit 3：Executor 层

交付内容：

```text
execution/structured_executor.py
execution/react_executor.py
execution/python_executor.py
runtime/executor.py
```

验收标准：

1. 三类 Executor 有统一返回结构；
2. Structured Executor 可调用已有分析工具；
3. ReAct Executor 包装 `create_react_agent`，不改变其内部能力；
4. Python Executor 管理命名空间、超时和错误；
5. ExecutionResult 包含 artifact 引用；
6. 单元测试通过。

必须覆盖：

```text
Structured task
ReAct task
Python task
执行成功
执行失败
超时
产物不存在
ExecutionResult round-trip
```

### Commit 4：Runtime 主链路

交付内容：

```text
AnalysisRuntime
TaskScheduler
TaskExecutor
```

验收标准：

1. Runtime 能从 Plan 驱动任务调度；
2. Runtime 能调用 Executor；
3. Runtime 能接收 ExecutionResult；
4. Runtime 能记录 Artifact；
5. Runtime 能持久化 Run 状态；
6. 单个任务失败不会导致整个 Run 状态不可恢复；
7. 集成测试通过。

验收链路：

```text
AnalysisPlan
  ↓
TaskScheduler
  ↓
TaskExecutor
  ↓
ExecutionResult
  ↓
RuntimeArtifact
```

### Commit 5：接入 LangGraph

交付内容：

```text
langgraph_agent.py
```

验收标准：

1. Runtime 成为主控制流；
2. `create_react_agent` 被 ReactExecutor 调用；
3. LangGraph State 只承载运行上下文；
4. `_Session` 职责被限制为执行环境；
5. 现有 API 和 SSE 行为不回归；
6. 旧 ReAct fallback 可用；
7. 集成测试通过。

### Commit 6：Verification Layer

交付内容：

```text
verification/verifier.py
```

验收标准：

1. 能对 ExecutionResult 或 Artifact 执行验证；
2. 第一批至少覆盖：
   - 数值一致性；
   - 时间一致性；
   - 聚合一致性；
   - Artifact 存在性；
   - Evidence 存在性。
3. 验证结果可持久化；
4. 失败原因可读；
5. Verification 能阻止未验证结果进入可信 Evidence；
6. 单元测试和集成测试通过。

### Commit 7：Evidence Collector

交付内容：

```text
EvidenceCollector
```

验收标准：

1. Evidence 由 Runtime 自动产生；
2. Evidence 能关联 Execution、Artifact 和 VerificationResult；
3. Evidence 不依赖报告阶段手工填写；
4. Evidence 可序列化、持久化、回读；
5. 集成测试通过。

### Commit 8：Skill Retrieval

交付内容：

```text
SkillRetriever
```

验收标准：

1. 能根据 Task 或 Plan 检索 Skill；
2. Skill 与 Task、Tool 的关系明确；
3. 未检索到 Skill 时有稳定 fallback；
4. Skill 使用情况可追踪；
5. 不引入向量数据库或外部复杂依赖；
6. 单元测试和集成测试通过。

---

## 7. 首条端到端验收场景

### 7.1 输入数据

使用现有测试数据：

```text
grouped_sales
```

### 7.2 用户问题

```text
分析各部门销售额同比变化，找出下降最大的部门，并说明主要贡献因素。
```

### 7.3 期望任务链

```text
Run
 ↓
T01 schema
 ↓
T02 metric
 ↓
T03 period_comparison
 ↓
T04 department_breakdown
 ↓
T05 contribution
 ↓
T06 verification
 ↓
T07 findings
 ↓
T08 report
```

实际任务编号和任务类型可以调整，但必须保留完整能力链。

### 7.4 必须生成的链路

```text
Task
  ↓
Execution
  ↓
Artifact
  ↓
VerificationResult
  ↓
EvidenceItem
  ↓
Finding
  ↓
Report
```

以 `T03 period_comparison` 为例，必须能够追踪：

```text
Finding
  ↓
EvidenceItem
  ↓
VerificationResult
  ↓
RuntimeArtifact
  ↓
ExecutionResult
  ↓
RuntimePlanStep
  ↓
AnalysisTask
  ↓
Skill / Tool
  ↓
Dataset
```

### 7.5 E2E 通过标准

E2E 测试通过必须同时满足：

1. Run 正常完成；
2. Plan 中所有必须任务完成；
3. 每个关键 Artifact 存在；
4. 每个 Artifact 有验证结果；
5. 每个 Finding 至少引用一个 Evidence；
6. 报告包含下降最大的部门；
7. 报告包含主要贡献因素；
8. 报告中的关键结论可回溯；
9. 没有未验证数据被用于最终结论；
10. API / SSE 事件正常；
11. 测试无回归。

---

## 8. 总体验收标准

V2 第一阶段整体验收通过时，必须满足以下条件。

### 8.1 功能验收

- [ ] Runtime 是主控制流；
- [ ] ReAct 是 Executor 而不是主控制器；
- [ ] Plan 能生成结构化任务；
- [ ] Scheduler 能正确调度任务；
- [ ] Executor 能返回结构化结果；
- [ ] Artifact 真实存在且可读取；
- [ ] VerificationResult 可记录并通过或失败；
- [ ] Evidence 只来自已验证执行结果；
- [ ] Finding 必须引用 Evidence；
- [ ] Report 只基于 Finding、Evidence 和 Artifact；
- [ ] 全链路可追踪。

### 8.2 质量验收

- [ ] 所有单元测试通过；
- [ ] 所有集成测试通过；
- [ ] 所有 E2E 测试通过；
- [ ] 现有测试无回归；
- [ ] API 兼容性测试通过；
- [ ] SSE 行为测试通过；
- [ ] 失败恢复测试通过；
- [ ] 序列化与持久化测试通过。

### 8.3 可观测性验收

- [ ] Run 状态可查询；
- [ ] Task 状态可查询；
- [ ] ExecutionResult 可查询；
- [ ] Artifact 可定位；
- [ ] VerificationResult 可查询；
- [ ] Evidence 可查询；
- [ ] Finding 可查询；
- [ ] 报告可关联 Run；
- [ ] 失败原因可读。

### 8.4 兼容性验收

- [ ] 旧 `PlanStep` 不破坏现有路径；
- [ ] 旧 Artifact 可读或提供迁移逻辑；
- [ ] 旧 Report 生成路径不回归；
- [ ] 现有 API 响应结构不发生破坏性变化；
- [ ] 现有前端主要功能不回归。

---

## 9. 测试证据要求

每一阶段提交时，必须记录：

```text
Commit ID
分支
执行环境
测试命令
测试结果
失败测试列表
修复说明
```

建议测试命令：

```bash
conda activate analysis
pytest tests
```

如后续拆分测试目录，应补充更精确的命令，例如：

```bash
pytest tests/test_schema.py tests/test_scheduler.py
```

测试证据必须能证明：

1. 没有跳过关键测试；
2. 没有删除失败测试来提高通过率；
3. 新增功能都有测试覆盖；
4. 旧功能没有回归。

---

## 10. 失败处理与回滚策略

### 10.1 单任务失败

要求：

1. Runtime 记录失败任务；
2. 不静默吞掉错误；
3. 不破坏已完成任务和 Artifact；
4. 支持重试或进入 Replan；
5. Run 状态可恢复。

### 10.2 验证失败

要求：

1. VerificationResult 记录失败原因；
2. 相关结果不能进入可信 Evidence；
3. 不能生成基于失败数据的 Finding；
4. 可以触发重试或 Replan。

### 10.3 Runtime 主链路失败

要求：

1. API 返回明确错误；
2. SSE 输出错误事件；
3. 本地状态不损坏；
4. 可使用旧 ReAct fallback；
5. 不需要人工清理临时文件才能恢复。

### 10.4 回滚

V2 第一阶段应提供以下至少一种回退方式：

```text
1. 配置开关切换到旧 ReAct 路径
2. 回滚到上一个已验证 Commit
3. 保留旧 Schema 并禁用新 Runtime 链路
```

回滚后必须满足：

1. 旧模式可运行；
2. 旧 API 可访问；
3. 已生成文件不损坏；
4. 核心测试通过。

---

## 11. 发布门槛

以下条件全部满足后，V2 第一阶段才能视为可发布：

- [ ] Commit 1～8 全部通过阶段验收；
- [ ] E2E `grouped_sales` 场景通过；
- [ ] 旧功能回归测试通过；
- [ ] API / SSE 测试通过；
- [ ] 失败恢复测试通过；
- [ ] 文档与代码一致；
- [ ] 无未解释的跳过测试；
- [ ] 无高优先级缺陷；
- [ ] 回滚策略验证通过；
- [ ] 代码已同步回本地工作区；
- [ ] 本地 Git diff 已人工审查。

---

## 12. 缺陷等级定义

| 等级 | 定义 | 发布要求 |
|---|---|---|
| P0 | 主链路无法运行、数据错误、报告结论无 Evidence 支撑 | 必须修复 |
| P1 | 任务调度错误、验证失效、Evidence 断链、回滚失败 | 必须修复 |
| P2 | 非关键可观测性缺失、部分日志不可读 | 评估后决定 |
| P3 | 文案、注释、非关键 UI 问题 | 可延后 |

发布前不允许存在未处理的 P0/P1 缺陷。

---

## 13. 最终验收结论模板

```text
Runtime V2 第一阶段验收结论

验收日期：
验收人：
代码版本：
测试环境：

测试结果：
- 单元测试：通过 / 失败
- 集成测试：通过 / 失败
- E2E 测试：通过 / 失败
- 回归测试：通过 / 失败
- API / SSE 测试：通过 / 失败
- 回滚测试：通过 / 失败

核心链路：
- Plan → Task：通过 / 失败
- Task → Execution：通过 / 失败
- Execution → Artifact：通过 / 失败
- Artifact → Verification：通过 / 失败
- Verification → Evidence：通过 / 失败
- Evidence → Finding：通过 / 失败
- Finding → Report：通过 / 失败

遗留问题：
- P0：
- P1：
- P2：
- P3：

结论：
□ 验收通过
□ 有条件通过
□ 验收不通过

说明：
```

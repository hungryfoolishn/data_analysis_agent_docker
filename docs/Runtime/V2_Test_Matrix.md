# Data Analysis Agent Runtime V2 测试矩阵

- 文档状态：待评审
- 实施基线：`docs/Runtime/Data Analysis Agent Runtime v4.md`
- 配套文档：`docs/Runtime/V2_Implementation_Acceptance.md`
- 目标版本：Runtime V2.0
- 测试环境：`docker-host` / `/python/pragrams/data_analysis_agent`
- 推荐执行环境：Conda `analysis`

---

## 1. 文档目的

本文档定义 Runtime V2 实施过程中必须覆盖的单元测试、集成测试、端到端测试和回归测试。

测试矩阵与实施验收方案配套使用：

- `V2_Implementation_Acceptance.md` 判断阶段是否完成；
- `V2_Test_Matrix.md` 判断测试证据是否充分。

任何测试项默认状态为：

```text
Pending
```

状态只允许使用：

| 状态 | 含义 |
|---|---|
| Pending | 尚未实施或尚未执行 |
| Passed | 测试通过，且已有可追溯证据 |
| Failed | 测试失败，存在缺陷 |
| Blocked | 因依赖缺失无法执行 |
| N/A | 明确不适用于当前阶段，必须写原因 |

---

## 2. 测试通过总原则

Runtime V2 测试必须证明以下四件事：

1. **控制权已经转移**
   - Runtime 驱动 Plan、Task、Execution；
   - ReAct 只是 Executor。

2. **结构化链路真实存在**
   ```text
   Task
     → Execution
     → Artifact
     → Verification
     → Evidence
     → Finding
     → Report
   ```

3. **结论可信**
   - Finding 必须引用 Evidence；
   - Evidence 必须来自已验证结果；
   - Report 不重新分析原始数据。

4. **旧功能不回归**
   - 旧 API / SSE / ReAct fallback 仍可用；
   - 现有测试不因 V2 改造失败。

---

## 3. 测试范围

### 3.1 单元测试

覆盖对象：

```text
schemas.py
runtime/scheduler.py
runtime/executor.py
execution/structured_executor.py
execution/react_executor.py
execution/python_executor.py
verification/verifier.py
EvidenceCollector
SkillRetriever
```

### 3.2 集成测试

覆盖对象：

```text
AnalysisRuntime
TaskScheduler
TaskExecutor
LangGraph
Verification Layer
Evidence Collector
Persistence
```

### 3.3 端到端测试

覆盖场景：

```text
grouped_sales
```

核心问题：

```text
分析各部门销售额同比变化，找出下降最大的部门，并说明主要贡献因素。
```

### 3.4 回归测试

覆盖对象：

```text
API
SSE
旧 ReAct 路径
旧 Report 路径
现有 Web UI 核心功能
```

---

## 4. 单元测试矩阵

### 4.1 Schema 测试

| ID | 对象 | 测试场景 | 断言 | 状态 |
|---|---|---|---|---|
| UT-SCHEMA-001 | `TaskType` | 枚举定义完整 | 覆盖 V4 定义的核心任务类型 | Pending |
| UT-SCHEMA-002 | `ExecutorType` | 枚举定义完整 | 包含 structured / react / python | Pending |
| UT-SCHEMA-003 | `AnalysisTask` | 正常构造 | 必填字段存在且类型正确 | Pending |
| UT-SCHEMA-004 | `AnalysisTask` | 缺少必填字段 | 抛出校验错误 | Pending |
| UT-SCHEMA-005 | `AnalysisTask` | JSON round-trip | 反序列化后对象等价 | Pending |
| UT-SCHEMA-006 | `AnalysisTask` | 依赖字段 | 能表达依赖任务和输入产物 | Pending |
| UT-SCHEMA-007 | `RuntimePlanStep` | 状态字段 | pending / running / completed / failed 可表达 | Pending |
| UT-SCHEMA-008 | `RuntimePlanStep` | JSON round-trip | 反序列化后字段完整 | Pending |
| UT-SCHEMA-009 | `ExecutionResult` | 正常构造 | 状态、错误、产物引用字段可用 | Pending |
| UT-SCHEMA-010 | `ExecutionResult` | JSON round-trip | 反序列化后字段完整 | Pending |
| UT-SCHEMA-011 | `RuntimeArtifact` | 路径校验 | 保存路径、类型、哈希或校验信息 | Pending |
| UT-SCHEMA-012 | `RuntimeArtifact` | JSON round-trip | 反序列化后字段完整 | Pending |
| UT-SCHEMA-013 | `VerificationResult` | passed 状态 | 通过结果能记录检查项 | Pending |
| UT-SCHEMA-014 | `VerificationResult` | failed 状态 | 失败结果能记录失败原因 | Pending |
| UT-SCHEMA-015 | `VerificationResult` | JSON round-trip | 反序列化后字段完整 | Pending |
| UT-SCHEMA-016 | `EvidenceItem` | 验证状态 | 能表达 verified / unverified / failed | Pending |
| UT-SCHEMA-017 | `Finding` | 结论依赖 | 能引用 Evidence、Artifact 和上游结论 | Pending |
| UT-SCHEMA-018 | Legacy 兼容 | 旧 `PlanStep` | 旧字段构造不失败 | Pending |
| UT-SCHEMA-019 | Legacy 兼容 | 旧报告数据 | 旧对象能被解析 | Pending |
| UT-SCHEMA-020 | Legacy 兼容 | 旧 Artifact 引用 | 旧路径或旧字段不丢失 | Pending |

### 4.2 Scheduler 测试

| ID | 场景 | 输入 | 断言 | 状态 |
|---|---|---|---|---|
| UT-SCHED-001 | 空 Plan | 无任务 | 不抛未知异常，返回空队列或明确错误 | Pending |
| UT-SCHED-002 | 单任务 Plan | 一个无依赖任务 | 队列包含该任务 | Pending |
| UT-SCHED-003 | 多任务顺序依赖 | A → B → C | 输出顺序为 A、B、C | Pending |
| UT-SCHED-004 | 多任务部分依赖 | A、B 独立，C 依赖 A、B | C 在 A 和 B 之后 | Pending |
| UT-SCHED-005 | 依赖缺失 | B 依赖不存在的 A | Scheduler 拒绝计划并返回错误 | Pending |
| UT-SCHED-006 | 循环依赖 | A → B → A | Scheduler 拒绝计划并返回错误 | Pending |
| UT-SCHED-007 | 重复 task_id | 两个任务 ID 相同 | Scheduler 拒绝计划 | Pending |
| UT-SCHED-008 | 非法 task_type | 未知类型 | Scheduler 拒绝计划 | Pending |
| UT-SCHED-009 | 任务状态更新 | running → completed | 状态字段正确变化 | Pending |
| UT-SCHED-010 | 任务失败 | running → failed | 状态和失败信息正确记录 | Pending |
| UT-SCHED-011 | 可恢复失败 | failed 后重试 | 能重新进入 pending 或 running | Pending |
| UT-SCHED-012 | Scheduler 状态持久化 | 中断后恢复 | 队列和状态可回读 | Pending |

### 4.3 Executor 测试

| ID | Executor | 场景 | 断言 | 状态 |
|---|---|---|---|---|
| UT-EXEC-001 | Structured | 正常执行 | 返回成功 ExecutionResult | Pending |
| UT-EXEC-002 | Structured | 工具返回数据 | ExecutionResult 包含 Artifact 引用 | Pending |
| UT-EXEC-003 | Structured | 工具抛错 | 返回失败 ExecutionResult 并包含错误 | Pending |
| UT-EXEC-004 | Structured | 工具不存在 | 返回明确错误，不崩溃 Runtime | Pending |
| UT-EXEC-005 | React | 正常执行 | ReAct 输出被包装为 ExecutionResult | Pending |
| UT-EXEC-006 | React | Agent 抛错 | 返回失败 ExecutionResult | Pending |
| UT-EXEC-007 | React | 输出无 Artifact | 返回明确警告或失败原因 | Pending |
| UT-EXEC-008 | Python | 正常执行 | 命名空间结果被捕获 | Pending |
| UT-EXEC-009 | Python | 语法错误 | 返回失败 ExecutionResult | Pending |
| UT-EXEC-010 | Python | 运行时错误 | 返回失败 ExecutionResult | Pending |
| UT-EXEC-011 | Python | 超时 | 执行被终止并返回超时错误 | Pending |
| UT-EXEC-012 | Python | 命名空间隔离 | 不同任务不污染彼此变量 | Pending |
| UT-EXEC-013 | Unified | 所有 Executor 返回结构 | 状态、错误、Artifact 字段一致 | Pending |
| UT-EXEC-014 | Unified | ExecutionResult round-trip | 序列化和反序列化一致 | Pending |
| UT-EXEC-015 | Unified | Artifact 不存在 | 返回明确失败，不生成假成功 | Pending |

### 4.4 Verification 测试

| ID | 检查项 | 场景 | 断言 | 状态 |
|---|---|---|---|---|
| UT-VERIFY-001 | 数值一致性 | 聚合值与明细计算一致 | VerificationResult passed | Pending |
| UT-VERIFY-002 | 数值一致性 | 聚合值与明细计算不一致 | VerificationResult failed 并说明差异 | Pending |
| UT-VERIFY-003 | 时间一致性 | 起止时间合法 | VerificationResult passed | Pending |
| UT-VERIFY-004 | 时间一致性 | 结束时间早于开始时间 | VerificationResult failed | Pending |
| UT-VERIFY-005 | 聚合一致性 | 分组求和等于总量 | VerificationResult passed | Pending |
| UT-VERIFY-006 | 聚合一致性 | 分组求和不等于总量 | VerificationResult failed 并说明差异 | Pending |
| UT-VERIFY-007 | Artifact 存在性 | 文件存在 | VerificationResult passed | Pending |
| UT-VERIFY-008 | Artifact 存在性 | 文件不存在 | VerificationResult failed | Pending |
| UT-VERIFY-009 | Evidence 存在性 | Evidence 可读取 | VerificationResult passed | Pending |
| UT-VERIFY-010 | Evidence 存在性 | Evidence 缺失 | VerificationResult failed | Pending |
| UT-VERIFY-011 | 验证结果持久化 | 保存后回读 | 字段和关联关系完整 | Pending |
| UT-VERIFY-012 | 验证失败阻断 | Verification failed | 对应结果不能进入可信 Evidence | Pending |

### 4.5 Evidence 与 Finding 测试

| ID | 场景 | 断言 | 状态 |
|---|---|---|---|
| UT-EVID-001 | Evidence 正常生成 | 关联 Execution、Artifact、Verification | Pending |
| UT-EVID-002 | Evidence 序列化 | JSON round-trip 后字段完整 | Pending |
| UT-EVID-003 | Evidence 缺少验证 | 生成失败或标记为不可信 | Pending |
| UT-EVID-004 | Evidence 缺少 Artifact | 生成失败或标记为不可信 | Pending |
| UT-EVID-005 | Evidence 回读 | 持久化后能恢复 | Pending |
| UT-FIND-001 | Finding 正常生成 | 至少引用一个 Evidence | Pending |
| UT-FIND-002 | Finding 缺少 Evidence | 拒绝生成或标记为无效 | Pending |
| UT-FIND-003 | Finding 类型 | 能表达 metric / comparison / contribution / anomaly 等类型 | Pending |
| UT-FIND-004 | Finding 追溯 | 能回溯到 Task、Execution、Artifact | Pending |
| UT-FIND-005 | Finding 序列化 | JSON round-trip 后字段完整 | Pending |

### 4.6 Skill Retrieval 测试

| ID | 场景 | 断言 | 状态 |
|---|---|---|---|
| UT-SKILL-001 | 根据 Task 检索 Skill | 返回候选 Skill 或明确空结果 | Pending |
| UT-SKILL-002 | 根据 Plan 检索 Skill | 每个关键任务有 Skill 或 fallback | Pending |
| UT-SKILL-003 | Skill 不存在 | 不抛异常，走默认执行器 | Pending |
| UT-SKILL-004 | Skill 元数据加载 | Skill 名称、适用任务、工具映射完整 | Pending |
| UT-SKILL-005 | Skill 使用记录 | Task 能关联最终使用的 Skill | Pending |
| UT-SKILL-006 | Skill fallback | fallback 路径可追踪 | Pending |

---

## 5. 集成测试矩阵

### 5.1 Runtime 主链路

| ID | 场景 | 断言 | 状态 |
|---|---|---|---|
| IT-RUNTIME-001 | Plan → Scheduler | Runtime 能生成 Task 队列 | Pending |
| IT-RUNTIME-002 | Scheduler → Executor | 任务按依赖顺序执行 | Pending |
| IT-RUNTIME-003 | Executor → ExecutionResult | Runtime 接收结构化执行结果 | Pending |
| IT-RUNTIME-004 | Execution → Artifact | Artifact 被注册到 Runtime | Pending |
| IT-RUNTIME-005 | Artifact → Verification | Artifact 自动进入验证流程 | Pending |
| IT-RUNTIME-006 | Verification → Evidence | 通过验证后生成 Evidence | Pending |
| IT-RUNTIME-007 | Evidence → Finding | Finding 引用 Evidence | Pending |
| IT-RUNTIME-008 | Finding → Report | Report 只基于 Finding / Evidence / Artifact | Pending |
| IT-RUNTIME-009 | Run 状态持久化 | 中断后可回读 Run | Pending |
| IT-RUNTIME-010 | Task 失败恢复 | 已完成任务保留，失败任务可重试 | Pending |
| IT-RUNTIME-011 | Verification 失败恢复 | 失败结果不进入 Evidence，可重试 | Pending |
| IT-RUNTIME-012 | ReAct fallback | Runtime 失败后可走旧 ReAct 路径 | Pending |

### 5.2 LangGraph 集成

| ID | 场景 | 断言 | 状态 |
|---|---|---|---|
| IT-GRAPH-001 | Runtime 接入 LangGraph | LangGraph 调用 Runtime 而不是直接驱动 ReAct | Pending |
| IT-GRAPH-002 | GraphState 初始化 | Session、Runtime、Run 边界正确 | Pending |
| IT-GRAPH-003 | 任务节点执行 | State 保存当前 Task 和状态 | Pending |
| IT-GRAPH-004 | 验证节点执行 | State 保存 VerificationResult | Pending |
| IT-GRAPH-005 | Replan 节点执行 | 失败后能生成或更新计划 | Pending |
| IT-GRAPH-006 | 报告节点执行 | 只读取可信结论和产物 | Pending |
| IT-GRAPH-007 | 流式事件输出 | SSE 事件顺序符合运行阶段 | Pending |

### 5.3 API / SSE / Web UI 回归

| ID | 场景 | 断言 | 状态 |
|---|---|---|---|
| REG-API-001 | 启动分析请求 | API 返回成功响应 | Pending |
| REG-API-002 | 查询 Run 状态 | Run 状态可获取 | Pending |
| REG-API-003 | 查询 Task 状态 | Task 状态可获取 | Pending |
| REG-API-004 | 查询 Artifact | Artifact 路径或内容可获取 | Pending |
| REG-API-005 | 查询 Evidence | Evidence 可获取 | Pending |
| REG-API-006 | 查询 Finding | Finding 可获取 | Pending |
| REG-SSE-001 | 分析开始事件 | 前端能接收事件 | Pending |
| REG-SSE-002 | 任务执行事件 | 前端能显示当前任务 | Pending |
| REG-SSE-003 | 验证结果事件 | 前端能显示验证状态 | Pending |
| REG-SSE-004 | 报告完成事件 | 前端能展示报告 | Pending |
| REG-SSE-005 | 错误事件 | 前端能展示失败原因 | Pending |
| REG-UI-001 | 工作台核心流程 | 用户能发起分析 | Pending |
| REG-UI-002 | 报告展示 | 用户能查看最终报告 | Pending |
| REG-UI-003 | 结论追溯 | 用户能查看 Finding 的 Evidence 链 | Pending |

---

## 6. 端到端测试矩阵

### E2E-001：`grouped_sales` 同比下降分析

#### 输入数据

```text
grouped_sales
```

#### 用户问题

```text
分析各部门销售额同比变化，找出下降最大的部门，并说明主要贡献因素。
```

#### 期望任务

```text
T01 schema
T02 metric
T03 period_comparison
T04 department_breakdown
T05 contribution
T06 verification
T07 findings
T08 report
```

#### 断言

| ID | 断言 | 状态 |
|---|---|---|
| E2E-001-A01 | Run 正常完成 | Pending |
| E2E-001-A02 | Plan 生成完整 | Pending |
| E2E-001-A03 | Scheduler 调度顺序正确 | Pending |
| E2E-001-A04 | 所有关键任务完成 | Pending |
| E2E-001-A05 | Schema 任务识别数据结构 | Pending |
| E2E-001-A06 | Metric 任务计算销售额 | Pending |
| E2E-001-A07 | Period comparison 任务计算同比 | Pending |
| E2E-001-A08 | Breakdown 任务按部门拆分 | Pending |
| E2E-001-A09 | Contribution 任务识别主要贡献因素 | Pending |
| E2E-001-A10 | 每个关键 Artifact 存在 | Pending |
| E2E-001-A11 | 每个关键 Artifact 有验证结果 | Pending |
| E2E-001-A12 | Finding 引用 Evidence | Pending |
| E2E-001-A13 | 报告包含下降最大的部门 | Pending |
| E2E-001-A14 | 报告包含主要贡献因素 | Pending |
| E2E-001-A15 | 报告不引入无 Evidence 支撑的新结论 | Pending |
| E2E-001-A16 | Finding 能追溯到 Evidence | Pending |
| E2E-001-A17 | Evidence 能追溯到 Verification | Pending |
| E2E-001-A18 | Verification 能追溯到 Artifact | Pending |
| E2E-001-A19 | Artifact 能追溯到 Execution | Pending |
| E2E-001-A20 | Execution 能追溯到 Task | Pending |
| E2E-001-A21 | Task 能追溯到 Tool / Skill | Pending |
| E2E-001-A22 | API / SSE 正常 | Pending |
| E2E-001-A23 | Run 状态持久化正常 | Pending |
| E2E-001-A24 | 旧功能无回归 | Pending |

#### 期望追溯链

```text
Report
  ↓
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

---

### E2E-002：失败与恢复

#### 场景

人为制造一个任务失败，例如：

```text
period_comparison 工具执行失败
```

#### 断言

| ID | 断言 | 状态 |
|---|---|---|
| E2E-002-A01 | Runtime 记录失败任务 | Pending |
| E2E-002-A02 | 已完成任务和 Artifact 不丢失 | Pending |
| E2E-002-A03 | 失败任务不生成可信 Evidence | Pending |
| E2E-002-A04 | Run 状态可恢复 | Pending |
| E2E-002-A05 | 重试后能继续执行 | Pending |
| E2E-002-A06 | 重试成功后链路完整 | Pending |
| E2E-002-A07 | SSE 错误事件可读 | Pending |

---

### E2E-003：验证失败阻断

#### 场景

人为构造一个验证失败，例如：

```text
分组求和不等于总量
```

#### 断言

| ID | 断言 | 状态 |
|---|---|---|
| E2E-003-A01 | VerificationResult 为 failed | Pending |
| E2E-003-A02 | 失败原因可读 | Pending |
| E2E-003-A03 | 对应结果不进入可信 Evidence | Pending |
| E2E-003-A04 | 不生成基于失败数据的 Finding | Pending |
| E2E-003-A05 | 报告不引用失败结论 | Pending |
| E2E-003-A06 | 系统能触发重试或 Replan | Pending |

---

### E2E-004：ReAct fallback

#### 场景

关闭或破坏 Runtime 主链路，验证旧路径可用。

#### 断言

| ID | 断言 | 状态 |
|---|---|---|
| E2E-004-A01 | 旧 ReAct 路径可启动 | Pending |
| E2E-004-A02 | API 正常响应 | Pending |
| E2E-004-A03 | SSE 正常输出 | Pending |
| E2E-004-A04 | 报告可生成 | Pending |
| E2E-004-A05 | 回滚后核心测试通过 | Pending |

---

## 7. 手动验收检查清单

以下项目用于补充自动化测试。

### 7.1 架构边界

- [ ] Runtime 是主控制流；
- [ ] ReAct 只作为 Executor 使用；
- [ ] `_Session` 不再承担 Run 生命周期管理；
- [ ] `langgraph_agent.py` 逐步变薄；
- [ ] GraphState 只保存运行上下文；
- [ ] Artifact、Evidence、Finding 的生成路径清晰。

### 7.2 数据与文件

- [ ] workspace/session 目录结构清晰；
- [ ] Artifact 文件真实存在；
- [ ] Artifact 类型正确；
- [ ] 报告引用的图表存在；
- [ ] 中文字符渲染正常；
- [ ] 不存在不可追踪的临时文件。

### 7.3 结论可信度

- [ ] 每个 Finding 至少引用一个 Evidence；
- [ ] 每个 Evidence 有验证状态；
- [ ] 未验证数据没有进入报告；
- [ ] 报告没有重新计算主指标；
- [ ] 报告中的核心结论可点击或查询溯源。

### 7.4 可观测性

- [ ] Run ID 可见；
- [ ] Task ID 可见；
- [ ] Execution ID 可见；
- [ ] Artifact ID 可见；
- [ ] Verification ID 可见；
- [ ] Evidence ID 可见；
- [ ] Finding ID 可见；
- [ ] 错误信息可读。

---

## 8. 测试命令

### 8.1 全量测试

```bash
conda activate analysis
cd /python/pragrams/data_analysis_agent
pytest tests
```

### 8.2 按阶段测试

建议新增以下测试文件或等价测试：

```bash
pytest tests/test_runtime_schema.py
pytest tests/test_runtime_scheduler.py
pytest tests/test_runtime_executors.py
pytest tests/test_runtime_verification.py
pytest tests/test_runtime_evidence.py
pytest tests/test_runtime_langgraph.py
```

### 8.3 单个 E2E 测试

```bash
pytest tests/test_runtime_e2e_grouped_sales.py
```

实际文件名可以调整，但 Commit 中必须给出对应的测试命令。

---

## 9. 测试证据记录模板

每个 Commit 必须记录以下信息：

```text
Commit ID：
分支：
测试时间：
测试环境：
Python 版本：
依赖环境：

测试命令：

测试结果：
- 总数：
- 通过：
- 失败：
- 跳过：

失败测试：

缺陷：

证据文件：

结论：
```

---

## 10. 通过 / 不通过标准

### 10.1 单元测试通过标准

- [ ] 所有必填单元测试通过；
- [ ] 无未解释的跳过测试；
- [ ] 未通过删除断言让测试通过；
- [ ] Schema round-trip 全部通过；
- [ ] Scheduler 异常场景全部通过。

### 10.2 集成测试通过标准

- [ ] Runtime 主链路测试通过；
- [ ] LangGraph 集成测试通过；
- [ ] 持久化测试通过；
- [ ] 失败恢复测试通过；
- [ ] ReAct fallback 测试通过。

### 10.3 E2E 通过标准

- [ ] `grouped_sales` 场景通过；
- [ ] 失败恢复场景通过；
- [ ] 验证失败阻断场景通过；
- [ ] fallback 场景通过；
- [ ] API / SSE 无回归。

### 10.4 整体发布标准

以下条件全部满足，测试矩阵才算通过：

- [ ] 无 P0 缺陷；
- [ ] 无未处理的 P1 缺陷；
- [ ] 必填测试全部 Passed；
- [ ] Blocked 项必须有明确阻塞原因和解决计划；
- [ ] N/A 项必须有明确说明；
- [ ] 测试证据可追溯；
- [ ] E2E-001 通过；
- [ ] 回归测试通过；
- [ ] 回滚验证通过。

---

## 11. 测试结果汇总模板

```text
Runtime V2 测试结果汇总

测试日期：
代码版本：
测试环境：

单元测试：
- 总数：
- 通过：
- 失败：
- 跳过：

集成测试：
- 总数：
- 通过：
- 失败：
- 跳过：

E2E 测试：
- E2E-001：
- E2E-002：
- E2E-003：
- E2E-004：

回归测试：
- API：
- SSE：
- Web UI：
- 旧 ReAct：
- 旧 Report：

主要缺陷：
- P0：
- P1：
- P2：
- P3：

测试证据：
- 日志：
- Run ID：
- Artifact：
- Report：

结论：
□ 通过
□ 有条件通过
□ 不通过
```

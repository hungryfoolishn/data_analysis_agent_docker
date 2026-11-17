# B11.0 检查结论与下一步实施方案

结论：B11.0 的工作方向正确，可以进入 B11.1，但我建议先调整 B11.1 的实施边界。

B11.0 最重要的价值，是把“代码模块存在”与“真实产品链路已经闭环”区分开了。尤其是报告收尾、Evidence 关联、输出资产传播和 API Task Outcome 这几个问题，确实不能通过增加几个模块或单元测试就认定完成。

我检查了你提供的检查文档，并抽查了仓库中的 `runtime_route.py`、`runtime/context.py`、`runtime/analysis_workflow.py`、`tools/tool_analysis_operation.py`、`tools/tool_sqlite_query.py`、`runtime/analysis_report.py` 和 Adapter Registry 等实现。

## 一、B11.0 评审结果

通过

B11.0 检查阶段可以关闭

| 检查项                                        | 评审结果 | 说明                       |
| ------------------------------------------ | ---- | ------------------------ |
| 当前链路与目标链路分开描述                              | 通过   | 没有把目标架构误写成已实现能力          |
| Implemented / Registered / Called / E2E 区分 | 通过   | 这是后续验收的重要基础              |
| P1 / P2 / P3 问题分级                          | 基本合理 | 可以指导后续分批处理               |
| 暂不实施事项                                     | 通过   | 没有提前切换 V2 默认值或删除 Legacy  |
| B11.1–B11.4 拆分                             | 需要微调 | 报告闭环依赖 Evidence，不能简单先做报告 |

### 我认为最重要的三个发现

1\. Runtime V2 现在更接近执行引擎，还不是完整的分析产品流程。

目前可以执行确定性分析操作，也能执行 SQLite 查询。但工具执行成功，并不等于用户最终获得了一份经过验证的分析报告。

这是当前最应该优先解决的问题。

2\. B7 AnalysisReport 不能直接接入就算完成。

我检查了 `runtime/analysis_report.py`：

- `ReportFinding.supported_by` 要求存在 Evidence ID。
- `validate_analysis_report()` 会检查 Evidence 是否存在、是否为 `verified`。
- 报告中的数字还要能在 Evidence 的数值或文本中找到依据。
- Artifact ID 和 Asset ID 也必须能够解析。

因此，当前如果直接调用 `build_analysis_report()`，但没有可靠的 Evidence Record，报告依然无法通过验证。

3\. B11.1 原计划存在一个实施顺序问题。

原计划是：

```
Runtime Selection
    ↓
Plan Validation
    ↓
Report Builder
    ↓
AnalysisReport
```

但实际依赖应该是：

```
Execution
    ↓
Canonical Evidence
    ↓
Evidence Validation
    ↓
AnalysisReport Builder
    ↓
AnalysisReport Validator
```

文件分析工具目前能记录 Execution、输出 DataAsset 和 Artifact，但还没有统一的分析 Evidence；SQLite 已经有 `SqliteQueryEvidence`，却没有统一进入 B7 的报告验证链路。

所以，B11.1 必须把最小可用的 Canonical Evidence 纳入范围，不能只做报告收尾。

## 二、调整后的 B11 实施顺序

我建议保留 B11.2、B11.3、B11.4 的总体方向，但重新明确 B11.1 的交付边界：

B11.1 Runtime 控制与报告闭环

下一步

Runtime Selection 接入、Plan 双重验证、最小 Canonical Evidence、确定性报告收尾。

B11.2 文件分析链路完善

多步骤 Output Asset 传播、文件操作 Evidence、CSV / Excel / Parquet API + SSE E2E。

B11.3 SQLite 分析链路完善

Database Asset 语义规范、SQL Evidence 统一、Snapshot 消费、SQLite API + SSE E2E。

B11.4 Adapter 数据接入

DataIngestionService、refresh 透传、Parquet 持久化、DataAsset 注册、Provenance。

注意：B11.1 的 Canonical Evidence 是最小公共契约，不代表一次性完成所有 Evidence 能力。B11.2 负责完善文件操作的 Evidence 生成和多步骤资产传播；B11.3 负责 SQLite 专属 Evidence 和 Snapshot 的完整关联。

### B11.1 具体实施范围

| 模块                 | 具体修改                                           | 验收标准                                        |
| ------------------ | ---------------------------------------------- | ------------------------------------------- |
| Runtime Selection  | 将 `runtime_route.py` 的策略接入真实执行入口               | 不再直接散落判断 V2 开关和 fallback                    |
| Plan Validation    | General Plan + Tool Registry 两层验证              | 非法工具、参数、依赖在执行前被拒绝                           |
| Planner Failure    | 取消静默 fallback 到 inspect/data-quality           | 明确返回 clarification 或 failed                 |
| Canonical Evidence | 建立统一 Evidence Record 和注册入口                     | Evidence 能关联 Execution、输入/输出 Asset、Artifact |
| SQLite Evidence    | 将现有 `SqliteQueryEvidence` 转换/映射到公共 Evidence 契约 | 不丢失 SQL、参数指纹、截断状态、Snapshot 信息               |
| Report Synthesis   | 从已验证的 Execution / Evidence 构建确定性报告             | 不依赖 LLM 编造数字或结论                             |
| Report Validation  | 正式调用 B7 `validate_analysis_report()`           | 未验证 Evidence、错误 Asset、错误 Artifact 均拒绝       |
| Report Output      | 保存结构化 AnalysisReport 和 Markdown                | `session.report` 与结构化报告保持一致                 |

### 一个关键设计约束：报告不能伪造分析发现

B11.1 的第一版报告不需要追求复杂的自然语言洞察。

例如，可以确定性生成：

- 执行了哪些分析操作；
- 每个操作是否成功；
- 输入和输出分别是什么；
- 输出数据的行列数量；
- 是否发生截断；
- 产生了哪些图表或数据文件；
- 哪些步骤失败、哪些步骤成功。

这些都是可验证的事实。

不要为了满足 `AnalysisReport.findings` 至少一条的约束，就把“操作执行成功”伪装成“发现了业务规律”。报告应当明确区分执行结果、数据发现和分析结论。

## 三、B11.1 详细实施步骤

### B11.1.1 Runtime Selection 接入

重点文件：

- `langgraph_langchain/runtime/runtime_route.py`
- `langgraph_langchain/langgraph_agent.py`
- `langgraph_langchain/config.py`

要求：

1. 真实入口统一构建 `RuntimeSelectionPolicy`。
2. Legacy / Runtime V2 的选择由统一策略决定。
3. Runtime V2 执行成功、业务失败、基础设施失败必须区分。
4. 只有符合策略的基础设施故障才允许 fallback。
5. Planner 失败、Plan Validation 失败、分析操作失败不得被当成基础设施故障。
6. 记录选择原因、fallback 原因和实际执行模式。

不修改：

- `RUNTIME_V2_ENABLED` 默认值；
- `RUNTIME_V2_FALLBACK_ENABLED` 默认值；
- Legacy ReAct 实现。

### B11.1.2 Plan 双重验证

重点文件：

- `langgraph_langchain/runtime/context.py`
- `langgraph_langchain/runtime/plans.py`
- `langgraph_langchain/runtime/general_planner.py`
- `langgraph_langchain/tools/registry.py`

在 `initialize_plan()` 或统一 Plan 初始化入口，建立明确的验证链：

```
Planner Output
    ↓
General Plan Validation
    ↓
Runtime Plan Conversion
    ↓
Runtime Plan Schema Validation
    ↓
Tool Registry Validation
    ↓
Dependency / Input Validation
    ↓
Initialize Runtime Plan
    ↓
TaskRunner
```

必须验证：

- method 是否在当前 Session 的 Tool Registry 中；
- 参数是否满足对应工具 Schema；
- input_asset_ids 是否存在；
- task 依赖是否存在；
- 是否存在循环依赖；
- 输出引用是否合法；
- SQLite SQL 是否符合只读查询约束。

特别注意：不能只验证工具名称存在，还必须验证工具参数结构。

### B11.1.3 Canonical Evidence 最小公共契约

建议新增：

```
langgraph_langchain/evidence/
    models.py
    registry.py
    adapters.py
```

如果现有目录中已有等价模块，应复用，不要重复建立一套。

最小 Evidence 需要支持：

```
evidence_id
evidence_type
execution_id
task_id
tool_name

input_asset_ids
output_asset_ids
artifact_ids

evidence_text
numeric_values
validation_status

source_metadata
created_at
```

其中：

- `validation_status` 必须有明确的状态转换，不能默认所有 Evidence 都是 `verified`；
- `verified` 只能由实际验证逻辑产生；
- SQLite Evidence 保留 SQL 指纹、Snapshot ID、truncated 等专属信息；
- Evidence ID 必须稳定地关联到对应 Execution；
- 不能仅仅把 SQLite JSON 文件保存为 Artifact，就认为已经进入统一 Evidence Registry。

第一阶段可以保留旧模型，通过 Adapter 做兼容，不要直接删除 `EvidenceItem` 或 `SqliteQueryEvidence`。

### B11.1.4 确定性 Report Builder

复用：

`langgraph_langchain/runtime/analysis_report.py`

要求：

1. 从 Runtime Execution 和 Canonical Evidence 中收集报告素材。
2. 只使用 `verified` Evidence 生成 Findings。
3. Finding 必须引用 Evidence ID。
4. Finding 中的数字必须能从 Evidence 中追溯。
5. Artifact、Asset 引用必须通过验证。
6. 部分步骤失败时，报告状态必须为 `partial_success`，不能伪装成 `success`。
7. 所有步骤失败时，不允许生成成功报告。

报告链：

```
Runtime Execution Results
          ↓
Canonical Evidence Registry
          ↓
Evidence Verification
          ↓
AnalysisReport Builder
          ↓
validate_analysis_report()
          ↓
AnalysisReport
          ↓
render_analysis_report()
          ↓
session.report
```

### B11.1.5 Runtime 最终状态

B11.1 至少要让 Runtime V2 有一个明确的最终结果对象，而不是仅依赖 `session.report` 是否为空。

建议：

```
@dataclass(frozen=True)class RuntimeOutcome:    run_id: str    status: str    runtime_mode: str    report_id: str | None    completed_steps: int    failed_steps: int    evidence_ids: tuple[str, ...]    error_code: str | None    fallback_used: bool
```

状态至少包括：

```
completed
partial_success
failed
cancelled
```

这个对象是 B11.4 API Task Outcome 的基础。B11.1 先在 Runtime 内形成可靠结果，B11.4 再负责映射到 API/SSE。

## 四、B11.1 验收标准

不要只看单元测试通过。至少要完成以下测试矩阵：

| 测试场景                               | 预期结果                                         |
| ---------------------------------- | -------------------------------------------- |
| V2 关闭                              | 选择 Legacy，不改变现有行为                            |
| V2 开启                              | 选择 Runtime V2                                |
| V2 业务执行失败                          | 返回 failed，不静默 fallback                       |
| V2 基础设施失败 + fallback 开启 + 非 strict | 按策略 fallback，并记录原因                           |
| V2 基础设施失败 + strict                 | 不 fallback                                   |
| Planner 返回非法工具                     | Plan 初始化前拒绝                                  |
| Planner 返回非法参数                     | Plan 初始化前拒绝                                  |
| Planner 返回不存在的 Asset               | Plan 初始化前拒绝                                  |
| Planner 产生循环依赖                     | Plan 初始化前拒绝                                  |
| 文件操作成功                             | 生成 Execution、Output Asset、Canonical Evidence |
| SQLite 查询成功                        | 生成 Execution、Snapshot、Canonical Evidence     |
| Evidence 未验证                       | Report Validation 失败                         |
| Finding 引用不存在的 Evidence            | Report Validation 失败                         |
| Finding 中出现无依据数字                   | Report Validation 失败                         |
| 所有步骤成功                             | 生成 success 报告                                |
| 部分步骤成功                             | 生成 partial_success 报告                        |
| 所有步骤失败                             | 不生成 success 报告                               |
| 没有可验证 Evidence                     | 不生成成功报告                                      |

测试分层：

1. Unit：Runtime Selection、Plan Validator、Evidence Registry、Report Builder。
2. Integration：文件工具和 SQLite 工具分别进入统一 Evidence Registry。
3. Runtime E2E：真实 Runtime V2 执行 Plan → Evidence → Report。
4. API 验证：至少验证 Runtime Outcome 能正确区分 completed / partial_success / failed。

B6 原有 10 个数据集、30 个问题的 Golden 基线保持不变，不要通过修改原有预期结果让新测试通过。

建议新增：

```
tests/runtime/test_runtime_selection_integration.py
tests/runtime/test_plan_execution_validation.py
tests/evidence/test_canonical_evidence.py
tests/runtime/test_runtime_report_closure.py
tests/integration/test_file_runtime_report.py
tests/integration/test_sqlite_runtime_report.py

docs/architecture/reviews/b11.1-runtime-control-and-report-closure.md
```

测试文件名称可根据现有项目目录结构调整，避免重复建立测试体系。

## 五、可以直接交给 Codex 的实施任务

下面这份任务要求 Codex 先检查现有实现，再修改代码，避免重复造轮子。

# B11.1 Runtime Control & Report Closure 实施任务

## 1. 角色

你是一名资深 Python Agent Runtime 架构师，负责 DeepAnalyze 项目的 Runtime V2 生产链路集成。

必须基于当前代码实际实现，不允许仅根据架构文档推断代码行为。

## 2. 项目基线

Repository: `hungryfoolishn/data_analysis_agent_docker`

Branch: `codex/phase1-m2.4-sqlite-schema`

B11.0 commit: `7d1ced9`

B11.0 检查基线：`1524c6c`

B11.0 文档：

`docs/architecture/reviews/b11-runtime-integration-inspection.md`

B11.0 是纯检查阶段，没有修改业务代码。

## 3. 本批次目标

完成 Runtime V2 从 Plan 到 Report 的最小生产闭环：

```
Runtime Selection
    ↓
Planner
    ↓
Plan Validation
    ↓
Runtime V2 Execution
    ↓
Canonical Evidence
    ↓
Evidence Validation
    ↓
AnalysisReport Builder
    ↓
AnalysisReport Validator
    ↓
RuntimeOutcome
```

本批次必须解决：

1. Runtime Selection Policy 未接入真实执行入口。
2. Plan 缺少统一的 Tool Registry 验证。
3. Planner 失败可能静默 fallback。
4. Runtime V2 确定性计划没有可靠的报告收尾。
5. B7 AnalysisReport 没有进入真实 Runtime 路径。
6. Legacy Evidence、SQLite Evidence 尚未统一接入报告验证。

## 4. 实施原则

- 先检查已有代码，再决定复用或修改。
- 优先扩展现有模块，不重复创建同类 Registry、Validator。
- 不进行 Agent 架构大规模重写。
- 不删除 Legacy ReAct。
- 不修改 RUNTIME_V2_ENABLED 默认值。
- 不修改 RUNTIME_V2_FALLBACK_ENABLED 默认值。
- 不改变 B6 Golden 原有预期结果。
- 不引入新的公开 Adapter。
- 不把 LLM 生成的数字直接当作 Evidence。
- 不允许未经验证的 Evidence 进入成功报告。
- 不允许业务分析失败被当作基础设施故障并静默 fallback。

## 5. 第一阶段：代码检查

重点检查：

- `runtime/runtime_route.py`
- `runtime/context.py`
- `runtime/plans.py`
- `runtime/general_planner.py`
- `runtime/analysis_workflow.py`
- `runtime/sqlite_workflow.py`
- `runtime/analysis_report.py`
- `runtime/runner.py`
- `runtime/executor.py`
- `tools/registry.py`
- `tools/tool_analysis_operation.py`
- `tools/tool_sqlite_query.py`
- `evidence/` 目录
- `langgraph_agent.py`

输出每个模块的现状：

- 已实现能力；
- 真实调用入口；
- 当前数据结构；
- 可复用接口；
- 必须修改的接口；
- 测试覆盖。

特别检查 AnalysisReport 所需的 Evidence 字段是否已经能从现有 Execution 和 SQLite Evidence 中获得。

如果发现 B11.1 目标与实际代码结构冲突，先给出调整建议，不得为了符合设计而重复实现已有功能。

## 6. 第二阶段：Runtime Selection

将现有 `RuntimeSelectionPolicy` / `decide_runtime_route()` 接入真实 Agent 执行入口。

要求：

1. Legacy / Runtime V2 选择使用统一策略。
2. 记录实际选择的 Runtime Mode 和原因。
3. 区分业务失败、Plan Validation 失败、基础设施失败。
4. 仅在明确允许的基础设施场景执行 fallback。
5. strict mode 生效。
6. fallback 必须记录原因。
7. 默认配置保持不变。

新增测试覆盖所有决策分支。

## 7. 第三阶段：Plan Validation

建立统一 Plan 执行前验证入口。

至少验证：

- Plan Schema；
- Tool Registry；
- 工具名称；
- 工具参数结构；
- Input Asset 存在性；
- Output Reference；
- Dependency 存在性；
- Dependency 循环；
- SQLite SQL 只读约束。

General Plan Validation 和 Runtime Plan Validation 不得互相替代。

所有 Plan 必须在 Runtime TaskRunner 执行之前完成验证。

Planner 失败时：

- 需要澄清的问题返回 clarification；
- 不可恢复的错误返回 failed；
- 不允许静默生成 inspect/data-quality fallback Plan。

如果产品确实需要 fallback Plan，必须由明确的策略和可观测状态控制，不能吞掉 Planner 原始异常。

## 8. 第四阶段：Canonical Evidence

检查现有 `EvidenceItem`、`SqliteQueryEvidence`、Runtime Evidence 相关模型。

优先设计一个统一公共契约，建议至少包含：

- evidence_id
- evidence_type
- task_id
- execution_id
- tool_name
- input_asset_ids
- output_asset_ids
- artifact_ids
- evidence_text
- numeric_values
- validation_status
- source_metadata
- created_at

要求：

1. Evidence 必须关联 Execution。
2. Evidence 必须关联输入、输出 Asset。
3. Artifact 引用必须可解析。
4. SQLite Evidence 必须保留 SQL fingerprint、Snapshot 和 truncated 信息。
5. 现有模型可以通过 Adapter 兼容。
6. 不得直接删除旧 Evidence 模型。
7. `verified` 必须来自真实验证逻辑，不能默认赋值。
8. Evidence 注册必须防止重复 ID 和无效引用。

优先建立统一 Evidence Registry / Adapter，而不是同时维护三套互不相干的数据结构。

## 9. 第五阶段：AnalysisReport 集成

复用：

`runtime/analysis_report.py`

接入：

- `build_analysis_report()`
- `validate_analysis_report()`
- `render_analysis_report()`

要求：

1. 从真实 Execution 和 Canonical Evidence 构建报告。
2. Findings 必须引用 Evidence ID。
3. Findings 中的数字必须能从 Evidence 追溯。
4. Asset 和 Artifact 引用必须存在。
5. 截断数据必须有明确限制说明。
6. 只允许 verified Evidence 支持成功报告。
7. 所有验证错误必须阻止成功报告生成。
8. 报告 Markdown 必须从结构化 AnalysisReport 渲染，不能分别维护两套报告内容。

报告应区分：

- 执行结果；
- 数据发现；
- 分析结论；
- 数据限制。

不要把工具执行成功直接描述成业务分析发现。

第一版可以只输出确定性执行摘要、操作结果、数据规模、Evidence 和 Artifact，不要求复杂的 LLM 洞察生成。

## 10. 第六阶段：RuntimeOutcome

建立统一 Runtime 最终结果对象，至少包含：

- run_id
- status
- runtime_mode
- report_id
- completed_steps
- failed_steps
- evidence_ids
- error_code
- fallback_used

状态：

- completed
- partial_success
- failed
- cancelled

Runtime Outcome 必须来自真实执行结果，不能通过 SSE generator 是否结束来推断。

B11.4 再负责将它映射到 API Task Outcome。

## 11. 测试要求

### Unit

- Runtime Selection Policy 所有分支；
- Tool Registry 验证；
- Plan 参数验证；
- Dependency 验证；
- Evidence Registry；
- Evidence Validation；
- AnalysisReport Builder；
- AnalysisReport Validator；
- RuntimeOutcome 状态映射。

### Integration

- 文件分析操作 → Execution → Output Asset → Evidence；
- SQLite SQL → Execution → Snapshot → Evidence；
- Evidence → AnalysisReport；
- Report Validation 失败时阻止成功状态。

### Runtime E2E

至少覆盖：

1. 文件分析所有步骤成功；
2. SQLite 查询成功；
3. 一个步骤失败；
4. 多步骤部分成功；
5. 无 Evidence；
6. Evidence 不存在；
7. Evidence 未验证；
8. Finding 数值不受 Evidence 支持；
9. Plan 验证失败；
10. Runtime V2 业务失败不允许静默 fallback。

### Regression

保留 B6 原有 Golden 结果。

运行相关测试及完整回归测试，分别记录：

- passed；
- failed；
- skipped；
- skipped 原因。

不能将 skipped 计入通过数量。

## 12. 文档

新增：

`docs/architecture/reviews/b11.1-runtime-control-and-report-closure.md`

必须说明：

- 修改文件；
- 新增数据模型；
- Runtime 真实调用链；
- Evidence 数据流；
- Report 数据流；
- RuntimeOutcome 状态映射；
- 测试结果；
- 未完成问题；
- B11.2 的前置条件。

## 13. Git 要求

完成后：

1. 执行 `git diff --check`。
2. 确认无无关文件修改。
3. 执行全部要求的测试。
4. 更新实施文档。
5. 独立 commit。
6. 推送当前分支。
7. 报告 commit SHA 和本地/远端状态。

Commit message：

`feat(runtime): close runtime control and report chain`

## 14. 严格禁止

- 不切换 Runtime V2 默认开关。
- 不删除 Legacy。
- 不实现 B11.2 的多步骤 Output Asset 传播扩展。
- 不重构 SQLite Database Asset。
- 不实现 DataIngestionService。
- 不做 Docker 部署 E2E。
- 不将取消传播提前扩展成 B12 的完整生命周期改造。

如果发现某项 B11.1 工作必须依赖上述后续批次，记录依赖和边界，不得擅自扩大范围。

## 15. 最终交付

必须提供：

1. 实际修改文件清单；
2. Runtime 调用链变化；
3. Runtime Selection 决策矩阵；
4. Plan Validation 结果；
5. Canonical Evidence 结构及示例；
6. AnalysisReport 示例；
7. RuntimeOutcome 示例；
8. 测试结果；
9. 未解决问题；
10. Commit SHA 和 Push 结果；
11. B11.2 的准确前置条件。

只有真实 API/Runtime 调用链能够生成并验证报告，才算 B11.1 完成。

## 六、B11.1 完成后的进入条件

B11.1 不应以“新增了 Evidence 类”和“新增了 Report Builder”作为完成标准。

我会按以下四个条件判断是否可以进入 B11.2：

1. 真实调用： Runtime V2 执行结束时确实调用了 Evidence Registry 和 AnalysisReport Builder。
2. 证据可靠： 文件操作和 SQLite 查询都能生成可追溯 Evidence。
3. 失败正确： Plan 错误、Evidence 验证失败、分析执行失败都不会生成虚假的成功报告。
4. 状态可靠： RuntimeOutcome 能准确表达成功、部分成功和失败。

另外，B11.0 文档将检查基线记为 `1524c6c`，而 `7d1ced9` 是文档提交。这是合理的：检查的是 B10.1 之后、B11.0 文档提交之前的业务代码状态。它不构成基线错误。

我的建议：现在只实施 B11.1，不要让 Codex 同时推进 B11.2。 B11.1 完成后，把实施文档、测试结果、Commit SHA 发给我，我再按真实代码审查 Evidence 和 Report 的数据链路，确认 B11.2 的具体实施边界。

这样可以避免一次性改动 Runtime、Evidence、Report、API 太多模块，最后虽然测试通过，却无法判断真正的产品闭环是否成立。
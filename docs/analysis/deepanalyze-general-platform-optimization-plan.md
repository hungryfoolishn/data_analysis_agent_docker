# DeepAnalyze 通用数据分析平台内核优化方案

> 版本：v1.0
>
> 适用范围：当前阶段的 DeepAnalyze 项目。目标是打磨通用分析能力、分析执行内核和平台能力，为未来接入 WrenAI 语义层、本体模型和企业内网业务知识做好准备。

## 1. 方案结论

DeepAnalyze 当前不应被定义为某个行业的智能分析应用，也不应与 WrenAI 重复建设语义层和本体建模。当前最合理的定位是：

> **一个可被语义层和领域知识激活的通用数据分析执行平台。**

平台接收数据、字段元数据、指标定义和业务上下文，负责把分析任务组织成可执行、可观察、可恢复、可复现的分析过程，并产出表格、图表、代码、报告和分析证据。

目标架构：

```text
WrenAI / 企业语义层 / 领域知识
  指标定义、实体关系、字段含义、权限上下文
                    ↓ 标准契约
DeepAnalyze 分析平台内核
  数据接入 → 分析计划 → 工具执行 → 产物管理 → 结果组织
                    ↓
  Streamlit 工作台 / API / 报告 / 图表 / 可复现分析包
```

本方案不以“马上具备所有行业专家能力”为目标，而以以下结果为目标：

1. 任意领域数据接入后，平台能稳定完成一套通用分析工作流。
2. 分析工具、数据源、模型和领域规则可以插拔替换。
3. 分析过程、代码、数据、产物和会话状态完整可观察、可恢复。
4. 未来接入 WrenAI 后，不需要推倒重写 Agent 核心。
5. 用户看到的是分析工作台和过程，而不只是一个聊天框和一篇长报告。

## 2. 当前项目基线

当前代码已经具备不少平台雏形，应优先整理边界，而不是全部重写：

| 当前模块 | 已有能力 | 在目标架构中的位置 | 当前主要问题 |
|---|---|---|---|
| `api_server_langgraph.py` | FastAPI、SSE、会话、上传、取消、恢复 | 任务/API 层 | API、会话编排、分析执行耦合较多 |
| `langgraph_agent.py` | ReAct Agent、`_Session`、流式事件、报告兜底 | Agent 编排层 | 文件较大，状态、工具、事件、产物逻辑集中 |
| `tools/registry.py` | 工具工厂、自动发现、会话绑定 | 工具插件层 | 工具协议和执行结果协议还不够统一 |
| `tool_load_data.py` / `tool_eda_profile.py` | 数据读取、基础画像 | 数据分析工具层 | 输入输出缺少统一 DataAsset/Schema 契约 |
| `tool_python_repl.py` | Python 持久命名空间、执行限制 | 执行运行时 | 代码、数据和产物的可复现元数据需要加强 |
| `schemas.py` | Finding、Evidence、Artifact 等模型 | 领域无关数据模型层 | 部分模型偏报告结果，缺少任务/计划/执行步骤模型 |
| `state_machine.py` | 阶段、转移和工具约束 | 工作流控制层 | 阶段与实际 Agent 事件的边界需要统一 |
| `evidence_binding.py` / `lineage.py` | 证据绑定和血缘 | 可审计层 | 需要从“报告末端校验”提前到每次产物生成 |
| `skills_loader.py` / `skills/` | 渐进式技能加载 | 能力扩展层 | 需要区分通用分析技能和领域技能 |
| `workspace_manager.py` / `session_persistence.py` | 工作区和恢复 | 持久化层 | 需要形成统一任务/运行/产物目录规范 |
| `webui/app.py` | 上传、流式结果、文件下载 | 交互层 | 当前偏聊天展示，缺少分析过程工作台 |

当前应保留的优势：会话隔离、流式事件、工具注册、Python guardrails、证据模型、血缘追踪、失败恢复和 Skills 机制。优化重点是建立稳定契约和模块边界。

## 3. 前沿模式的借鉴原则

行业前沿系统值得借鉴的不是某个产品的页面，而是以下通用模式：

### 3.1 语义层与分析层分离

Looker、Power BI、Snowflake 等系统把指标和实体关系交给语义层管理。DeepAnalyze 不重复实现这部分，而要能消费外部语义上下文：

```text
语义层回答：这个字段/指标是什么意思，应该如何取数？
分析平台回答：拿到数据后，如何设计和执行分析？
```

### 3.2 Plan → Execute → Inspect → Revise

先进分析不是一次生成答案，而是可观察的循环：

```text
目标理解 → 计划 → 执行一步 → 检查结果 → 调整计划 → 继续执行 → 形成产物
```

计划必须是结构化对象，而不只是写在 prompt 中的一段文字。

### 3.3 Artifact-first，而不是 text-first

分析过程中最重要的对象应是数据表、统计结果、图表、代码、查询、数据质量报告和发现卡片。自然语言报告只是这些产物的组织和解释层。

### 3.4 渐进式自动化

系统应在高风险或歧义位置请求用户确认，在低风险的重复分析位置自动执行。自动化程度应由数据和任务状态决定，而不是永远追求全自动。

### 3.5 可恢复的长任务

企业分析往往不是几秒钟的问答。任务需要暂停、取消、恢复、重跑某一步、替换一个参数并比较两次运行结果。

### 3.6 模型可替换

模型是推理组件，不应成为平台协议。平台应允许切换 DeepSeek、OpenAI-compatible、内网模型和未来其他模型，而不改变分析产物和任务协议。

## 4. 目标能力分层

### L0：运行基础设施

- 统一配置、日志、追踪、任务 ID 和错误模型
- 并发、超时、取消、恢复、资源限制
- workspace 生命周期与安全路径
- 模型网关与请求重试

### L1：数据资产与执行环境

- 单文件、多文件、多表统一表示
- Schema、数据粒度、主键候选、时间字段和数据质量摘要
- Python/SQL/统计工具统一执行协议
- 代码、输入、输出、环境和耗时记录
- 大文件采样、分块和缓存策略

### L2：通用分析能力

- 指标计算、过滤、聚合、分组、排序
- 时间趋势、同比、环比和基线比较
- Top-N、集中度、贡献度和分解
- 异常、分布、相关性和基本统计检验
- 多表 Join、粒度检查和重复膨胀检测
- 图表和可下载分析产物

### L3：分析工作流

- 任务目标、约束和成功标准
- 结构化分析计划
- 阶段、步骤、工具调用和检查点
- 失败后的局部重试和计划调整
- 中间发现、待验证问题和用户确认
- 最终报告与完整分析包

### L4：可审计与可扩展平台

- 结论到证据、计算、字段、数据片段和产物的血缘
- 可重跑的 AnalysisRun
- 外部语义上下文适配器
- 通用 Skill 与领域 Skill 分离
- 数据源、模型、执行器和输出格式插件化
- API、前端工作台和后续 SDK

### L5：企业内网集成准备

- WrenAI 语义查询结果接入
- 企业数据库和对象存储适配
- 权限上下文透传
- 内网模型和网关适配
- 多用户审计与运行策略

当前重点是从 L0 做到 L4，L5 只建设接口和最小适配，不提前建设完整企业治理平台。

## 5. 分阶段优化路线

建议按 5 个阶段实施。每阶段都要有可运行产物，不进行大规模一次性重构。

### 阶段 0：边界冻结与基线整理（1 周）

目标：先明确哪些能力属于 DeepAnalyze，哪些交给 WrenAI 或未来领域插件。

任务：

1. 新增架构说明和模块责任表，标记 `langgraph_agent.py` 中状态、工具、事件、报告和持久化代码的边界。
2. 列出现有工具的输入、输出、异常和副作用。
3. 统一术语：`DataAsset`、`AnalysisTask`、`AnalysisRun`、`PlanStep`、`Artifact`、`Finding`、`Evidence`。
4. 记录当前代表性数据集的运行时间、失败率、产物数量和人工问题。
5. 把领域 R&D 模块标记为插件样例，不让领域代码继续侵入通用 Agent。

交付物：

- 架构边界文档
- 工具契约清单
- 运行基线记录
- 未来 WrenAI 接口草案

验收：新开发者可以根据责任表判断一个新功能应该放在平台、工具、Skill 还是领域插件中。

### 阶段 1：统一数据资产和执行协议（2-3 周）

目标：让不同数据来源和工具使用同一种输入输出语言。

建议新增模块：

```text
langgraph_langchain/data/
  assets.py       # DataAsset、DataTable、ColumnSpec
  schema.py       # SchemaSnapshot、粒度和主键候选
  quality.py      # QualityReport、质量规则和摘要
  adapters.py     # 文件、内存、查询结果适配器

langgraph_langchain/runtime/
  execution.py    # ExecutionRequest、ExecutionResult
  artifacts.py    # 产物注册和元数据
  cache.py        # 可选的结果缓存
```

核心对象建议：

```python
class DataAsset:
    asset_id: str
    name: str
    source_type: str          # csv, excel, sql_result, parquet...
    location: str             # 不记录敏感内容
    schema_snapshot_id: str
    row_count: int | None
    grain: str | None

class ExecutionResult:
    execution_id: str
    status: str                # succeeded, failed, cancelled
    code_or_query: str
    input_assets: list[str]
    output_artifacts: list[str]
    stdout_preview: str
    error: dict | None
    duration_ms: int
```

实施原则：

- 先兼容现有 `_Session.ns`，不要一次性删除持久命名空间。
- `load_data`、`eda_profile`、`python_repl` 先增加适配层，再逐步迁移内部实现。
- 所有工具返回结构化结果，同时保留当前字符串结果以兼容模型。
- 产物生成时立即注册，而不是报告结束时再猜测文件。

验收：同一份数据通过 CSV 和内存对象进入分析时，EDA、代码执行和产物协议一致；一次执行可以定位到输入资产、代码、输出文件和耗时。

### 阶段 2：把 Agent 流程升级为分析任务工作流（3-4 周）

目标：从“模型自由调用工具”演进为“模型在结构化任务协议内工作”。

建议新增模型：

```python
class AnalysisTask:
    task_id: str
    session_id: str
    question: str
    constraints: dict
    input_assets: list[str]
    external_context: dict | None

class PlanStep:
    step_id: str
    objective: str
    method: str
    required_inputs: list[str]
    expected_outputs: list[str]
    status: str
    depends_on: list[str]

class AnalysisRun:
    run_id: str
    task_id: str
    plan_version: int
    status: str
    current_step_id: str | None
    steps: list[PlanStep]
```

工作流变化：

1. 请求进入时创建 `AnalysisTask` 和初始 `AnalysisRun`。
2. Agent 输出结构化计划，不直接把所有步骤埋在自然语言中。
3. 每次工具执行前检查输入和依赖，每次执行后写入 `ExecutionResult`。
4. 允许工具结果触发计划追加、跳过或重试，而不是只依赖 recursion limit。
5. 用户可以查看计划、暂停、取消、重跑单个步骤或继续任务。
6. `finish_report` 只负责组织已有产物和发现，不再承担所有结尾逻辑。

对现有代码的处理：

- `run_analysis_stream` 先保留，增加 `AnalysisRun` 事件包装。
- `state_machine.py` 从“工具是否允许调用”逐步扩展为“任务步骤是否满足依赖”。
- `langgraph_agent.py` 拆出 `task_runtime.py`、`event_mapper.py`、`report_orchestrator.py`。
- 保留当前 SSE 事件格式，同时增加 `task_id`、`run_id`、`step_id`、`artifact_ids`。

验收：一个任务中途重启后可以恢复到最近成功步骤；重跑某一步不会破坏此前产物；前端能区分计划事件、工具事件、产物事件和最终文本。

### 阶段 3：建设通用分析方法和产物工作台（3-4 周）

目标：减少 Agent 临时编写长 Python 脚本，把常见分析沉淀为可组合的方法工具。

建议把通用能力分成四组：

#### 数据理解

- `inspect_schema`
- `profile_quality`
- `infer_grain`
- `detect_candidate_keys`
- `summarize_time_range`

#### 指标和比较

- `compute_metric`
- `compare_periods`
- `compare_groups`
- `decompose_change`
- `rank_and_concentration`

#### 统计与诊断

- `detect_anomalies`
- `distribution_summary`
- `correlation_scan`
- `segment_profile`
- `join_validate`

#### 产物

- `create_table_artifact`
- `create_chart_artifact`
- `create_code_artifact`
- `create_analysis_snapshot`

每个方法工具必须声明：输入资产、需要的字段、数据粒度假设、输出 schema、失败条件和产物类型。方法工具不是为了替代 Python，而是把常见操作变成可验证、可复用的积木；复杂问题仍允许进入 Python/SQL 执行器。

前端工作台建议增加四个区域：

```text
左侧：数据资产与字段摘要
中部：分析计划、步骤状态和实时过程
右侧：当前产物、图表、表格和发现卡
底部：可展开的代码、查询、日志和错误
```

第一版不需要完整重做 Streamlit 页面，可以先将当前流式文本解析成结构化步骤和产物卡片，再逐步替换展示组件。

验收：常见趋势、分组、异常和贡献分析至少有一条结构化工具路径；用户可以下载“分析包”，其中包含报告、图表、代码、执行元数据和运行摘要。

### 阶段 4：证据、血缘和可复现分析包（2-3 周）

目标：把已有的 evidence/lineage 从结果校验能力升级为平台原生能力。

统一血缘图：

```text
DataAsset
   ↓
Execution / Query / PythonCode
   ↓
TableArtifact / ChartArtifact / QualityReport
   ↓
Finding / ReportSection
```

每个 Finding 应能反查：

- 使用了哪些 DataAsset 和字段
- 来自哪次 Execution
- 代码或查询是什么
- 使用了哪个时间范围和过滤条件
- 关联哪些表格、图表和数据片段
- 该结果的运行版本、模型版本和时间

建议扩展现有 `ArtifactRef` 和 `ConclusionTrace`，增加 `execution_id`、`input_asset_ids`、`query_hash`、`code_hash`、`data_snapshot_hash` 和 `reproducibility_status`。

“可复现”分为三档：

1. **可解释**：能看到文字和图表来源。
2. **可重跑**：代码、输入和参数齐全，可以重新执行。
3. **可比对**：不同运行的结果、数据版本和计划差异可以比较。

当前阶段先做到前两档，第三档作为后续平台能力。

验收：随机打开一条 Finding，可以通过 API 或工作台跳转到执行步骤、代码、数据字段和产物；删除报告文本后，仍能从运行记录重建报告主要内容。

### 阶段 5：WrenAI / 语义层集成契约（2-3 周）

目标：只建设集成边界和最小适配，不重复做本体和语义治理。

建议建立 `SemanticContextProvider` 接口：

```python
class SemanticContextProvider(Protocol):
    async def resolve_question(self, question: str) -> SemanticResolution:
        ...

class SemanticResolution:
    query: str | None
    metric_definitions: list[dict]
    entities: list[dict]
    dimensions: list[dict]
    source_assets: list[dict]
    assumptions: list[str]
    permissions: dict
```

DeepAnalyze 不需要知道 WrenAI 内部如何建模，只消费稳定的 `SemanticResolution`。同时保留本地文件模式：没有语义提供者时，系统使用自动 schema 理解和用户确认。

集成边界必须明确：

- WrenAI 负责自然语言到受治理数据查询、字段和指标语义。
- DeepAnalyze 负责查询结果后的多步分析、统计方法、图表、产物、证据和工作流。
- 权限上下文由上游透传，DeepAnalyze 不绕过数据源权限。
- 每次分析保存语义上下文版本，保证未来可以解释当时使用了什么定义。

验收：同一分析任务可以使用本地文件适配器或 WrenAI 适配器运行；Agent 核心和通用工具不需要为某个 WrenAI API 写分支逻辑。

## 6. 工程重构顺序

不要直接拆分 `langgraph_agent.py` 的全部内容。推荐按依赖从外到内重构：

```text
1. 数据模型和事件协议
2. 产物注册和执行结果
3. 工具工厂适配层
4. AnalysisRun 任务运行时
5. Agent 编排拆分
6. API 事件映射
7. Streamlit 工作台展示
```

每次重构都要求旧接口可运行。建议使用 facade：

- `get_tools_for_session(session)` 保持兼容，内部改为新 Runtime Context。
- `run_analysis_stream(...)` 保持兼容，内部创建 `AnalysisTask` 和 `AnalysisRun`。
- 旧字符串 SSE 事件继续发送，新字段逐步增加。
- `_Session.ns` 继续作为短期兼容层，新的工具优先通过 `DataAssetStore` 访问数据。

## 7. API 和事件协议建议

当前 `/v1/chat/completions` 继续作为兼容入口，新增内部/平台 API：

```text
POST /analysis/tasks                 创建分析任务
GET  /analysis/tasks/{task_id}       查询任务
POST /analysis/tasks/{task_id}/runs  启动或重跑
GET  /analysis/runs/{run_id}         查询运行状态
POST /analysis/runs/{run_id}/cancel 取消运行
POST /analysis/runs/{run_id}/resume 恢复运行
GET  /analysis/runs/{run_id}/events 获取事件
GET  /analysis/runs/{run_id}/artifacts 获取产物
GET  /analysis/runs/{run_id}/lineage 获取血缘
```

统一事件类型：

```json
{
  "event": "step_completed",
  "task_id": "task_x",
  "run_id": "run_x",
  "step_id": "step_03",
  "status": "succeeded",
  "summary": "按区域完成销售变化分解",
  "artifact_ids": ["table_x", "chart_x"],
  "execution_id": "exec_x",
  "timestamp": "..."
}
```

事件层与文本层分离后，未来可以同时支持 Streamlit、前端 SPA、CLI、审计系统和自动化调用。

## 8. 非功能性要求

### 可复现性

- 记录输入文件 hash、Schema snapshot、代码 hash、配置版本和模型 ID。
- 生成图表和报告时记录创建步骤与运行 ID。
- 同一运行可以导出一个独立分析包。

### 安全性

- 所有数据路径继续经过安全校验。
- Python 执行环境禁止访问任务工作区之外的路径，除非由数据源适配器授权。
- 日志和事件默认脱敏，不记录 API Key 和敏感数据全文。
- 未来接入内网权限时，执行器必须继承上游权限上下文。

### 性能

- 小文件优先低延迟交互。
- 大文件先生成 schema/quality 摘要，再按需采样和聚合。
- 计算结果和中间产物可缓存，缓存 key 必须包含数据版本和参数。
- 模型调用、Python 执行和数据读取分别计时。

### 可观测性

- 任务、运行、步骤、工具、执行和产物都有 ID。
- 记录成功率、平均耗时、重试、取消、恢复和失败阶段。
- 将 Agent 模型错误与数据执行错误分开统计。

## 9. 测试和验收策略

当前不把 benchmark 当成产品卖点，但必须作为内核回归护栏。

### 单元测试

- DataAsset、SchemaSnapshot、ExecutionResult 序列化
- 工具输入校验和输出 schema
- 事件顺序、重复事件和断线恢复
- 产物注册、路径安全和 hash
- 任务状态转移和局部重跑

### 集成测试

- CSV/Excel → DataAsset → EDA → 图表 → 报告
- 多文件 → Join 校验 → 指标计算 → 产物
- 取消后恢复
- 运行中后端重启后的状态恢复
- 本地文件适配器与语义查询结果适配器使用同一分析流程

### 回归数据集

保留现有 `grouped_sales`、`time_series_anomaly`、`quality_issues`，再逐步加入：

- 多文件 Join
- 不同数据粒度
- 大文件采样
- 缺失和重复数据
- 没有时间字段的横截面数据

评分重点是平台行为：是否完成、是否可恢复、是否生成完整产物、是否能重跑；结论专业性仍作为基础回归项，不作为当前产品战略主线。

### 发布门槛

每个阶段完成前至少满足：

1. 现有测试不回退。
2. 代表性 E2E 流程可运行。
3. 旧 API 和 SSE 客户端仍兼容。
4. 新对象可以持久化和恢复。
5. 发生失败时能定位到 task/run/step/execution。

## 10. 暂缓事项

在平台内核稳定前，暂缓以下投入：

- 深度因果推断和自动实验平台
- 多 Agent 大规模并行编排
- 自建完整企业本体和指标治理平台
- 复杂推荐和自动决策闭环
- 多语言、多行业知识包
- 重做完整前端视觉系统
- 追求无人工确认的全自动分析

这些能力不是没有价值，而是依赖稳定的数据资产、任务协议、语义上下文和运行追踪。现在提前建设会增加复杂度，却无法证明平台基础已经成熟。

## 11. 前 4 周具体执行清单

### 第 1 周：建模和边界

- 建立 `DataAsset`、`AnalysisTask`、`AnalysisRun`、`PlanStep`、`ExecutionResult` 草案。
- 画出当前 `_Session` 字段到新对象的映射。
- 列出所有工具的输入、输出和副作用。
- 建立 task/run/step/execution/artifact ID 生成规则。

### 第 2 周：执行和产物

- 给 `python_repl`、`load_data`、`eda_profile` 增加统一执行结果包装。
- 产物生成时立即注册执行来源和输入资产。
- 将代码、图表、表格和报告加入同一 ArtifactStore。
- 保持旧 SSE 输出不变，新增结构化事件字段。

### 第 3 周：任务工作流

- 在 `run_analysis_stream` 外层创建 `AnalysisRun`。
- 将当前状态机阶段映射到 PlanStep。
- 实现步骤状态持久化、取消、恢复和最近成功步骤定位。
- 支持失败步骤单独重试。

### 第 4 周：工作台和适配器

- 前端将步骤、产物和报告分区展示。
- 允许展开代码、查询和执行摘要。
- 实现 `LocalFileAdapter`。
- 定义 `SemanticContextProvider`，先提供 mock adapter，暂不依赖 WrenAI 在线服务。

## 12. 成功标准

当以下条件同时满足时，可以认为通用平台内核进入可用阶段：

- 一个任务可以从文件或语义查询结果进入同一分析流程。
- 用户可以看到计划、步骤、执行状态和中间产物。
- 任务失败后可以恢复或重跑局部步骤。
- 每个产物可以定位到执行步骤和输入资产。
- 分析包可以独立下载、重跑和审查。
- 新增一个分析工具不需要修改 Agent 核心循环。
- 新接入一个语义层不需要复制一套 Agent。
- 领域规则可以作为插件注入，而不是写死在通用 prompt 中。
- 现有 API、前端和测试保持兼容。

## 13. 最终判断

DeepAnalyze 当前最重要的任务不是证明自己已经是一个成熟的行业分析师，而是成为一个可靠的分析平台内核：

```text
数据源可替换
分析工具可组合
工作流可观察
任务可以恢复
产物可以追溯
领域知识可注入
模型可以替换
```

当这些基础能力稳定后，WrenAI 的语义层、本体和企业业务知识才能真正发挥作用；否则，领域知识只是被注入一个不可观察、难恢复、难复用的 Agent 流程中，后续仍然需要返工。


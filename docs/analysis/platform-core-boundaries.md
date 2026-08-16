# DeepAnalyze 平台内核边界与实施记录

> 本文是《DeepAnalyze 通用数据分析平台内核优化方案》的实施配套文档，记录第一批代码边界、兼容策略和后续迁移规则。

## 1. 责任边界

```text
外部语义层 / WrenAI
  负责：业务术语、指标定义、实体关系、受治理查询和权限上下文
                         ↓
DeepAnalyze 平台内核
  data/      数据资产和物理 Schema
  runtime/   分析任务、运行、执行结果和产物元数据
  tools/     通用分析操作与执行器
  agent      计划、工具选择、过程调整和结果组织
  API/WebUI  任务控制、事件传输和分析工作台
```

平台内核不定义某个行业的正确业务指标，也不在当前阶段复制 WrenAI 的本体和语义模型。

## 2. 第一批新增对象

| 对象 | 所属模块 | 责任 |
|---|---|---|
| `ColumnSpec` | `data/assets.py` | 记录字段物理类型、空值和唯一值信息 |
| `SchemaSnapshot` | `data/assets.py` | 在数据加载时冻结行列数、候选键和时间字段 |
| `DataAsset` | `data/assets.py` | 表示一个可被分析运行引用的数据集 |
| `AnalysisTask` | `runtime/models.py` | 表示用户问题、输入资产和外部上下文 |
| `AnalysisRun` | `runtime/models.py` | 表示任务的一次执行实例 |
| `RuntimePlanStep` | `runtime/models.py` | 表示可持久化的分析步骤，区别于旧代码生成步骤 |
| `ExecutionResult` | `runtime/models.py` | 记录工具状态、代码/查询、耗时、输入和输出 |
| `RuntimeArtifact` | `runtime/models.py` | 记录文件产物及其执行来源 |
| `AnalysisRuntime` | `runtime/context.py` | 持久化任务、资产、执行和产物元数据 |

## 3. 兼容策略

第一批实施不删除 `_Session`、`_Session.ns`、`new_artifacts` 和当前 SSE 字符串协议。

- `_Session.analysis_runtime` 是新平台元数据入口。
- DataFrame 仍存放在 `_Session.ns["df"]`，新 runtime 不持有大对象。
- 新产物同时进入 `AnalysisRuntime.artifacts` 和旧 `new_artifacts`。
- 新产物模型保留 `name/path/relative_path/url`，现有前端无需修改。
- 新执行记录写入 workspace 下的 `.analysis_runtime.json`。
- 相同 session 和 question 重新创建 runtime 时恢复已有元数据。
- 测试或旧调用没有 `analysis_runtime` 时，工具退回旧行为。

## 4. 已接入工具

### `load_data`

- 成功加载后创建 `DataAsset` 和 `SchemaSnapshot`。
- 记录源文件 SHA-256、文件类型、Excel sheet、行列数和字段信息。
- 记录成功或失败的 `ExecutionResult`。

### `python_repl`

- 记录实际执行代码、成功/失败、输出预览和耗时。
- 新生成文件登记为 `RuntimeArtifact`，并关联 execution ID。
- 保持现有 Python namespace 和字符串返回结果。

### `eda_profile`

- 自动生成的图表登记为 `RuntimeArtifact`。
- 图表与本次 EDA execution 关联。
- 记录 EDA 输出摘要和执行耗时。

## 5. 工具迁移规则

后续工具接入新 runtime 时遵守以下顺序：

1. 不改变现有 LangChain tool 参数和字符串返回值。
2. 为一次调用分配 execution ID。
3. 记录输入 asset ID 和代码/查询。
4. 产物生成时立即注册，并记录 execution ID。
5. 调用结束时写入 `ExecutionResult`。
6. 错误必须记录类型和摘要，但不得写入密钥或敏感数据全文。
7. 新字段只能增量加入旧 artifact 字典，不能删除前端依赖字段。

## 6. 分批实施状态

第二批已完成：

- `record_finding` 的成功、证据失败和领域校验结果进入 execution 记录。
- `delegate_analysis` 的输入失败、超时、Python 异常和图表产物进入 execution 记录。
- `finish_report` 的报告门禁、报告文件、findings、lineage 和 trace 文件进入 execution/artifact 记录。
- SSE 顶层新增 `analysis_event`，保留原 `choices` 和 `generated_files`。
- `analysis_event` 当前包含 task/run ID；产物事件额外包含 execution/artifact ID。
- 新增 `requirements-dev.txt`，声明完整测试需要的 pytest 和 pytest-asyncio。

服务器隔离验证方式：使用 `deepanalyze:latest` 启动一次性容器，挂载不包含 `.env`、workspace 和上传数据的测试副本。第二批完整结果为 `440 passed, 1 skipped`，没有修改或重启正式容器。

第三批已完成：

- LangGraph 的每次工具调用创建一个 `RuntimePlanStep`，记录目标、方法、依赖、预期输出、开始/结束时间和状态。
- 工具返回 `[ERROR]`、`ERROR:` 或 `REPORT REJECTED` 时步骤标记为失败；步骤失败不自动终止 run，Agent 可以修复后继续。
- 取消、最大步数、递归上限、连续 Python 错误、静默结束和未处理异常都会关闭悬挂步骤，并持久化 run 终态。
- `ExecutionResult` 与 `RuntimeArtifact` 新增可选 `step_id`，形成 `run -> step -> execution -> artifact` 追踪链。
- SSE `analysis_event` 新增当前 `step_id` 和产物 `step_ids`，旧字段和 OpenAI 兼容内容保持不变。
- SSE 使用状态文件签名缓存；每次发送只执行轻量 `stat`，仅在运行时状态变化后重新解析 JSON。

第四批已完成：

- SSE `analysis_event` 增加 `run_status` 和当前/最近步骤摘要，恢复分析流使用相同事件协议。
- 新增只读 `/sessions/{session_id}/runtime` 接口，返回 task、run、asset、execution 和 artifact 快照。
- Streamlit 从单一文本结果区升级为“分析过程 / 最终报告 / 产物与执行”工作台。
- 左侧数据区显示 DataAsset 的行列规模和时间字段；过程区实时合并步骤状态。
- 产物区集中提供文件入口，执行区可展开查看工具、状态、耗时、代码、输出摘要和错误。
- 前端仅在步骤生命周期变化时刷新运行快照，不随每个模型 token 请求后端。
- 工作台渲染对运行时文本进行 HTML 转义，并提供窄屏布局规则。

第五批已完成：

- 定义 `SemanticResolution`、`SemanticContextProvider` 和语义权限安全模型。
- 提供 `MockSemanticContextProvider` 与 JSON/YAML `LocalFileSemanticContextProvider`，不依赖 WrenAI 在线服务。
- `/v1/chat/completions` 支持可选 `semantic_context`，本地文件模式保持原行为。
- 语义上下文随 task 持久化，恢复与自动重试保持相同版本；版本变化会创建新 run。
- Agent prompt 只接收限长、字段收敛和指令注入过滤后的语义投影。
- SSE 与工作台显示 provider/context version，完整上下文通过 runtime 快照审计。
- WrenAI 字段映射和安全边界见 `semantic-context-contract.md`。

后续顺序：

第六批已完成：

- 每次 runtime 持久化除更新 `.analysis_runtime.json` 外，还原子更新 `.analysis_runs/<run_id>.json`；新任务覆盖当前快照时不会覆盖旧运行。
- `AnalysisRun` 增加 `parent_run_id`、`retry_of_step_id` 和 `attempt`，显式步骤重试形成可审计的新运行，而不是修改原运行。
- 步骤重试只接受失败步骤，重新加载源数据并恢复原任务、语义上下文版本与约束；原执行和产物不复制到新运行。
- 新增运行列表、详情、产物、步骤重试及平台指标 API。
- 平台指标从持久化历史实时聚合运行终态、重试成功率、步骤失败、工具失败和平均运行时长，不改变旧稳定性指标口径。

```text
GET  /analysis/runs?session_id=<id>&limit=50
GET  /analysis/runs/{run_id}
GET  /analysis/runs/{run_id}/artifacts
POST /analysis/runs/{run_id}/steps/{step_id}/retry
GET  /analysis/metrics
```

后续顺序：

1. 生成包含报告、产物、执行元数据和运行摘要的可下载分析包。
2. 使用真实 WrenAI HTTP/SDK adapter 做受治理查询结果联调。

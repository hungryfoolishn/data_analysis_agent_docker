# DeepAnalyze Data Analysis API

当前推荐后端为 `langgraph_langchain/api_server_langgraph.py`。它提供与前端兼容的 OpenAI 风格接口，并把 LangGraph ReAct 分析流程通过 SSE 持续推送给 WebUI。

## Features

- **OpenAI-compatible chat API** - `/v1/chat/completions` 支持流式与非流式
- **SSE streaming** - 实时返回分析步骤、工具输出预览和最终结果
- **Session-aware workspace** - 上传文件、日志、图表、报告按 `session_id` 隔离
- **Artifact tracking** - 自动收集生成文件并返回下载信息
- **Cancellation support** - 支持取消运行中的分析任务
- **Concurrency control** - 通过 `MAX_CONCURRENT_AGENTS` 控制并发分析数

## Recommended startup

```bash
python -m langgraph_langchain.api_server_langgraph
```

默认监听：`http://localhost:8888`

## Environment variables

```bash
DEEPSEEK_API_KEY=your_api_key_here
DEEPSEEK_MODEL_ID=deepseek-chat
DEEPSEEK_API_BASE=https://api.deepseek.com/v1
MAX_CONCURRENT_AGENTS=3
```

## API Endpoints

### 1. Chat Completions

```bash
POST /v1/chat/completions
Content-Type: application/json

{
  "model": "deepseek-chat",
  "messages": [
    {"role": "user", "content": "分析这个数据文件"}
  ],
  "temperature": 0.4,
  "stream": true,
  "session_id": "optional-session-id",
  "file_path": "/optional/absolute/path/to/file"
}
```

说明：
- `session_id` 可选；不传时后端自动创建
- `file_path` 可选；不传时默认使用该 session 最近上传的文件
- 若既没有上传文件也没有传 `file_path`，接口会返回 400

#### Streaming response

SSE 数据格式示例：

```text
data: {"choices":[{"delta":{"content":"..."}}]}

data: {"choices":[{"delta":{"content":"..."}}],"generated_files":[...]}

data: [DONE]
```

其中：
- `choices[0].delta.content`：流式文本增量
- `generated_files`：本轮新增产物列表
- 结束标记为 `data: [DONE]`

#### Non-streaming response

返回标准 JSON，包含：
- `choices[0].message.content`
- `session_id`
- `generated_files`

### 2. File Upload

```bash
POST /v1/files
Content-Type: multipart/form-data

file: <file data>
purpose: "file-extract"
```

说明：该接口提供 OpenAI 风格文件上传；文件会被保存到 `workspace/` 下。

### 3. Session Workspace Upload

```bash
POST /workspace/upload
Content-Type: multipart/form-data

file: <file data>
session_id: <optional>
```

返回字段示例：

```json
{
  "success": true,
  "filename": "data.xlsx",
  "session_id": "session_xxx",
  "file_path": "workspace/session_xxx/data.xlsx",
  "size": 12345
}
```

### 4. List Workspace Files

```bash
GET /workspace/files?session_id=<optional>
```

- 传 `session_id`：返回该会话的 `files` 和 `artifacts`
- 不传：返回 `workspace/` 根目录普通文件列表

### 5. Download Workspace File

```bash
GET /workspace/files/{filename:path}
```

可用于下载会话内图表、日志、报告等文件。

### 6. Delete Session

```bash
DELETE /sessions/{session_id}
```

可选参数：
- `purge=true`：删除会话记录的同时清理对应 workspace 目录

### 7. Cancel Session

```bash
POST /sessions/{session_id}/cancel
```

用于请求中断该 session 当前正在运行的分析任务。

### 8. Health Check

```bash
GET /health
```

返回示例：

```json
{
  "status": "ok",
  "sessions": 1,
  "concurrent_agents": 0,
  "max_concurrent_agents": 3
}
```

### 9. Analysis Run Package

```bash
GET /analysis/runs/{run_id}/package
```

返回 `application/zip` 可复现分析包，包含：

- `manifest.json`：包版本、Run 状态、文件大小、SHA-256 和缺失文件声明
- `metadata/run_snapshot.json`：Task、Run、Step、Execution、Asset、Artifact 元数据
- `inputs/`：该 Run 使用的工作区内输入数据
- `artifacts/`：报告、图表、表格、Finding 和血缘文件
- `replay.py`：按原顺序重放成功的 `python_repl` 步骤，运行前应人工检查代码
- `README.md`：离线包使用说明

打包器只读取当前 `workspace/` 下的普通文件；越界路径、失效路径和符号链接逃逸不会写入 ZIP，而会记录在 `manifest.json.missing_files` 中。

### 10. Finding Lineage

```bash
GET /analysis/runs/{run_id}/findings
GET /analysis/runs/{run_id}/findings/{finding_id}
GET /analysis/runs/{run_id}/lineage
```

- Finding 列表返回证据等级、置信度和血缘完整性。
- Finding 详情展开到输入资产及 SHA-256、Schema 字段、过滤条件、计算方法、Runtime Step、Execution 代码/输出和 Artifact。
- Run 血缘图返回 `data_source -> field/step -> execution -> artifact/evidence -> finding -> report` 节点与关系。
- 旧 Run 会尽量从 `analysis_findings.json` 和历史执行记录恢复；缺失的 Step 等信息通过 `warnings` 明确披露，不会伪造完整链路。

### 11. Deterministic Report Rebuild

```bash
GET /analysis/runs/{run_id}/report/rebuild
```

不调用 LLM，也不读取原报告正文，仅使用 Run 快照中的 Asset、Execution、Artifact、Finding、MetricDefinition 和 Assumption 重建 Markdown。

- 响应头 `X-Report-Rebuild-Mode: deterministic`。
- 响应头 `X-Content-SHA256` 可用于验证内容一致性。
- 没有结构化 Finding 时返回 `422`，不会根据执行日志编造结论。
- 时间边界未持久化时明确披露，不从字段名或叙述中猜测。
- 分析包自动包含 `artifacts/rebuilt_report.md`。

### 12. Plan Control

```bash
GET  /analysis/runs/{run_id}/plan
POST /analysis/runs/{run_id}/plan/propose
POST /analysis/runs/{run_id}/plan/pause
POST /analysis/runs/{run_id}/plan/revise
POST /analysis/runs/{run_id}/plan/confirm

Plan proposal is validated against registered tools, assets, fields, DAG dependencies, and the step budget before persistence.
```

暂停活动 Run 会在当前工具安全结束后生效：

```json
{"reason":"先检查当前结果","require_confirmation":false}
```

修订只接受剩余步骤，并生成必须确认的新版本：

```json
{
  "reason":"缩小分析范围",
  "revised_by":"analyst",
  "steps":[
    {"method":"load_data","objective":"重新加载数据"},
    {"method":"compare_groups","objective":"比较关键业务分组"},
    {"method":"finish_report","objective":"生成最终报告"}
  ]
}
```

确认请求示例：

```json
{"confirmed_by":"reviewer","note":"同意按 v2 执行"}
```

`awaiting_confirmation` 状态不能调用会话恢复接口；确认后 Run 保持 paused，调用 `/sessions/{session_id}/resume` 才会继续执行。

## Execution flow

```text
Client / Streamlit WebUI
        ↓
POST /workspace/upload
        ↓
POST /v1/chat/completions
        ↓
run_analysis_stream(...)
        ↓
LangGraph Agent tools:
  load_data → eda_profile → formal analysis tools / python_repl → finish_report
        ↓
workspace/<session_id>/ 产物收集 + SSE 回传
```

正式通用分析工具：

- `compare_groups(dimension, metric, aggregation, max_groups)`：按显式维度粒度比较分组。
- `analyze_time_trend(date_field, metric, frequency, aggregation)`：按日/周/月/季/年聚合趋势。
- `decompose_contribution(date_field, dimension, metric, frequency, top_k, max_groups)`：对最近两个有效周期做算术贡献拆解，不表示因果。
- `detect_anomalies(metric)`：使用 1.5 IQR 规则识别源行异常值。

四个工具都返回结构化 JSON，并生成已登记到 Runtime Execution 的 CSV 表格产物。

## Current behavior highlights

- 默认使用 DeepSeek OpenAI-compatible 接口
- 服务启动时会加载 `workspace/.sessions.json` 恢复仍然存在的 session
- 流式模式下每个 session 会注册取消事件，结束后自动清理
- 生成文件会写入 session 的 `artifacts` 列表并持久化
- SSE 响应头包含 `Cache-Control: no-cache` 和 `X-Accel-Buffering: no`

## Testing examples

```bash
# Health check
curl http://localhost:8888/health

# Upload file
curl -X POST http://localhost:8888/workspace/upload \
  -F "file=@test.xlsx"

# Streaming chat
curl -X POST http://localhost:8888/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "deepseek-chat",
    "messages": [{"role": "user", "content": "请分析数据质量并总结关键问题"}],
    "stream": true
  }'
```

## Notes

当前推荐后端为 `langgraph_langchain/api_server_langgraph.py`。

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
  load_data → eda_profile → python_repl(多步) → finish_report
        ↓
workspace/<session_id>/ 产物收集 + SSE 回传
```

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

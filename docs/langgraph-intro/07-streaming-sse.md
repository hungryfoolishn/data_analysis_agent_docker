# 07 - 流式输出 SSE 详解

> 本文深入解析从 LangGraph Agent 到用户浏览器的完整流式输出链路：事件处理、SSE 协议、前端渲染。

---

## 7.1 为什么需要流式输出？

### 没有流式输出的体验

```
用户: "分析这份销售数据"
[等待 3 分钟，屏幕空白]
[突然显示完整报告]
```

### 有流式输出的体验

```
用户: "分析这份销售数据"

> Step 1 load_data(sales.csv)
  文件加载完成: 200行 × 8列

> Step 2 eda_profile - 运行自动 EDA...
  缺失值: revenue 1.5%, discount 6%
  相关性: revenue ~ quantity r=0.85
  ...

> Step 3 python_repl
  ```python
  region_summary = df.groupby('region')['revenue'].sum()
  print(region_summary)
  ```
  out: 华东 420000, 华南 280000...

分析完成。华东区是 revenue 的主要贡献者...
```

用户可以实时看到 Agent 的每一步，而不是等待一个黑盒。

---

## 7.2 流式输出的三层架构

```
Layer 1: LangGraph Agent (astream_events)
    │  产生异步事件流
    ▼
Layer 2: run_analysis_stream (AsyncGenerator)
    │  过滤和转换事件
    ▼
Layer 3: FastAPI SSE (StreamingResponse)
    │  编码为 SSE 格式
    ▼
Layer 4: Streamlit 前端
    │  解析 SSE 并渲染
    ▼
用户看到实时输出
```

---

## 7.3 Layer 1：LangGraph 事件流

### `astream_events(version="v2")` 产生的事件

```python
async for event in agent.astream_events(
    {"messages": [user_msg]},
    config=config,
    version="v2"
):
    print(event["event"], event.get("name"))
```

输出示例：

```
on_chain_start  Agent
on_chat_model_start  ChatOpenAI
on_chat_model_stream  ChatOpenAI    ← LLM 输出一个 token
on_chat_model_stream  ChatOpenAI    ← 又一个 token
on_chat_model_stream  ChatOpenAI
on_chat_model_end  ChatOpenAI
on_tool_start  load_data            ← 工具开始执行
on_tool_end  load_data              ← 工具执行结束
on_chat_model_start  ChatOpenAI     ← LLM 再次思考
on_chat_model_stream  ChatOpenAI
on_chat_model_stream  ChatOpenAI
on_chat_model_end  ChatOpenAI
on_tool_start  python_repl          ← 第二个工具
on_tool_end  python_repl
...
on_chain_end  Agent
```

### 事件结构

```python
# on_chat_model_stream 事件结构
{
    "event": "on_chat_model_stream",
    "name": "ChatOpenAI",
    "data": {
        "chunk": AIMessageChunk(
            content="分析",   # 一个 token
            tool_calls=[],    # 工具调用信息（可能为空）
        )
    },
    "metadata": {
        "langgraph_node": "agent",
        "run_id": "abc-123",
    }
}

# on_tool_start 事件结构
{
    "event": "on_tool_start",
    "name": "python_repl",
    "data": {
        "input": {
            "code": "print(df.shape)"
        }
    }
}

# on_tool_end 事件结构
{
    "event": "on_tool_end",
    "name": "python_repl",
    "data": {
        "output": "(200, 8)"
    }
}
```

---

## 7.4 Layer 2：`run_analysis_stream` 事件处理

### 完整事件处理逻辑

```python
# langgraph_agent.py:2764-2920（简化注释版）
async for event in agent.astream_events(...):

    # ── 检查取消 ──
    if session.cancel_event.is_set():
        yield "\n\n**Analysis cancelled.**\n", []
        return

    kind = event["event"]
    name = event.get("name", "")

    # ── 处理 LLM 输出 token ──
    if kind == "on_chat_model_stream":
        chunk = event["data"]["chunk"].content
        if chunk:
            session.process_log.append(chunk)
            yield chunk, []    # ← 直接推送给前端

    # ── 处理工具开始 ──
    elif kind == "on_tool_start":
        step += 1

        # 步数限制检查
        if step > _MAX_AGENT_STEPS:
            yield "Analysis stopped: exceeded max steps.", []
            return

        args = event["data"].get("input", {})

        # 根据不同工具生成进度消息
        if name == "python_repl":
            msg = f"\n\n> **Step {step}** `python_repl`\n\n```python\n{args.get('code', '')}\n```\n\n"
            yield msg, []
        elif name == "load_data":
            session.start_stage("schema_understanding")
            msg = f"\n\n> **Step {step}** `load_data({args.get('file_path', '')})`\n\n"
            yield msg, []
        elif name == "eda_profile":
            session.start_stage("data_quality_check")
            msg = f"\n\n> **Step {step}** `eda_profile` - running auto EDA...\n\n"
            yield msg, []
        elif name == "finish_report":
            yield "\n\n---\n\n", []

    # ── 处理工具结束 ──
    elif kind == "on_tool_end":
        if name == "eda_profile":
            output_str = _extract_event_output_text(event["data"]["output"])
            if not output_str.startswith("ERROR"):
                session.complete_stage("data_quality_check")
            preview = _format_preview_text(output_str)
            if preview:
                msg = f"\n> `eda_profile done`\n\n{preview}\n\n"
                yield msg, []

        elif name == "python_repl":
            output_str = _extract_event_output_text(event["data"]["output"])
            is_error = output_str.startswith("[ERROR]")

            if is_error:
                session.consecutive_python_errors += 1
            else:
                session.consecutive_python_errors = 0
                session.complete_stage("deep_dive")
                session.start_stage("conclusion_synthesis")

            # 连续错误检查
            if session.consecutive_python_errors >= 3:
                yield "Analysis stopped: repeated errors.", []
                return

            # 检测新生成的图片
            for p in sorted(session.workspace_dir.iterdir()):
                if p.suffix in {".png", ".jpg", ".jpeg", ".svg"}:
                    if p not in session.known_image_files:
                        session.known_image_files.add(p)
                        # 构建图片 artifact
                        rel = p.relative_to(session.workspace_dir.parent)
                        image_url = f"/workspace/files/{rel}"
                        yield "", [{
                            "name": p.name,
                            "url": image_url,
                            "type": "image",
                        }]

        elif name == "finish_report":
            raw = event["data"]["output"]
            output_str = _extract_event_output_text(raw)
            if output_str and not output_str.startswith("REPORT REJECTED"):
                session.complete_stage("final_report")
                session.report = output_str
                yield f"\n\n**Report generated.**\n\n", session.new_artifacts
```

### 输出截断函数

工具的原始输出可能很长，需要截断以节省 tokens：

```python
# langgraph_agent.py:85-105
def _extract_event_output_text(raw):
    """从 LangGraph 事件中提取工具输出文本"""
    if raw is None:
        return ""
    content = getattr(raw, "content", raw)
    if isinstance(content, list):
        # 处理多部分内容
        parts = []
        for item in content:
            if isinstance(item, dict):
                piece = item.get("text") or item.get("content") or ""
            else:
                piece = getattr(item, "text", None) or str(item)
            if piece:
                parts.append(str(piece))
        return "\n".join(parts).strip()
    return str(content).strip()
```

---

## 7.5 Layer 3：FastAPI SSE 端点

### SSE 协议格式

SSE（Server-Sent Events）是一种基于 HTTP 的单向推送协议：

```
HTTP/1.1 200 OK
Content-Type: text/event-stream
Cache-Control: no-cache
X-Accel-Buffering: no

data: {"choices":[{"delta":{"content":"分析"}}]}

data: {"choices":[{"delta":{"content":"中..."}}]}

data: {"choices":[{"delta":{"content":"华东区"}}]}

data: [DONE]
```

每条消息格式：`data: {JSON}\n\n`（两个换行符结尾）。

### FastAPI 实现

```python
# api_server_langgraph.py（简化版）
from fastapi.responses import StreamingResponse

@app.post("/v1/chat/completions")
async def chat_completions(request: ChatRequest):
    session_id, session = get_or_create_session(request.session_id)
    file_path = _resolve_data_file(session, request.file_path)

    async def sse_generator():
        async for text, artifacts in run_analysis_stream(
            instruction=request.messages[-1].content,
            source_path=file_path,
            workspace_dir=str(get_session_workspace(session_id)),
            api_key=DEEPSEEK_API_KEY,
            model_id=DEEPSEEK_MODEL_ID,
            api_base=DEEPSEEK_API_BASE,
            session_id=session_id,
        ):
            if text:
                # SSE 格式编码
                chunk = {
                    "choices": [{
                        "delta": {"content": text},
                        "finish_reason": None,
                    }]
                }
                yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"

            if artifacts:
                # 发送文件/图片信息
                artifact_chunk = {
                    "choices": [{
                        "delta": {"artifacts": artifacts},
                    }]
                }
                yield f"data: {json.dumps(artifact_chunk, ensure_ascii=False)}\n\n"

        # 结束标记
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        sse_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )
```

### 为什么需要 `X-Accel-Buffering: no`？

如果使用了 Nginx 反向代理，Nginx 默认会缓冲响应。这会导致 SSE 消息被延迟推送。设置 `X-Accel-Buffering: no` 告诉 Nginx 不要缓冲。

---

## 7.6 并发控制

### Semaphore 限制

```python
# api_server_langgraph.py:57
_MAX_CONCURRENT = int(os.environ.get("MAX_CONCURRENT_AGENTS", "3"))
_agent_semaphore = asyncio.Semaphore(_MAX_CONCURRENT)

# 使用
async with _agent_semaphore:
    async for text, artifacts in run_analysis_stream(...):
        ...
```

### 取消机制

```python
# 创建取消事件
cancel_event = asyncio.Event()
_ACTIVE_CANCELS[session_id] = cancel_event

# 用户取消时
@app.post("/sessions/{session_id}/cancel")
async def cancel_analysis(session_id: str):
    if session_id in _ACTIVE_CANCELS:
        _ACTIVE_CANCELS[session_id].set()  # 触发取消
        return {"status": "cancelled"}

# Agent 内部检查
if session.cancel_event.is_set():
    yield "\n\n**Analysis cancelled.**\n"
    return
```

---

## 7.7 流式输出的用户体验

### 用户看到的实时输出序列

```
[第 0.5 秒]

> Step 1 `load_data(/workspace/abc/sales.csv)`

[第 1.2 秒]
File: sales.csv
Shape: 200 rows × 8 columns
Columns: date, region, product, category, revenue, quantity, unit_price, discount
Preview: (前5行数据)

[第 2.0 秒]

> Step 2 `eda_profile` - running auto EDA...

[第 5.0 秒]
EDA Profile:
- Shape: 200 rows × 8 cols
- Missing: revenue 3(1.5%), discount 12(6%)
- Correlation: revenue ~ quantity r=0.85
- Outliers: revenue 8 个
- Analysis Signals: ...
(4 张图表出现在界面上)

[第 6.0 秒]

> Step 3 `python_repl`
```python
fix_chinese()
region_summary = df.groupby('region').agg(
    total_revenue=('revenue', 'sum'),
    avg_revenue=('revenue', 'mean')
).sort_values('total_revenue', ascending=False)
print(region_summary)
```

out:
         total_revenue  avg_revenue
华东        420000        7000
华南        280000        5600
华北        180000        4500

[第 20 秒]
(图表: revenue_by_region.png 出现在界面上)

...

[第 60 秒]
---
**Report generated.**
(完整报告展示，包含所有图表和分析结论)
```

---

> **下一步**：阅读 [08-state-machine.md](08-state-machine.md) 深入学习状态机机制。

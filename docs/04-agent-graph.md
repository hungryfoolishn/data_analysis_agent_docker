# 04 - Agent 图的构建

> 本文逐行解析 LangGraph ReAct Agent 的构建过程：LLM 创建、工具绑定、图编译、执行配置。

---

## 4.1 构建 LLM 实例

一切从创建 LLM 开始。本项目使用 LangChain 的 `ChatOpenAI` 接入 DeepSeek 模型：

```python
# langgraph_agent.py:2741-2748
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(
    model="deepseek-chat",              # 模型名称
    api_key="sk-xxx",                   # API 密钥（从 .env 读取）
    base_url="https://api.deepseek.com/v1",  # API 基础地址
    temperature=0,                      # 温度 = 0（确定性输出）
    streaming=True,                     # 开启流式输出
    max_retries=3,                      # 网络错误自动重试 3 次
)
```

### 参数详解

| 参数 | 值 | 为什么这样设置 |
|------|-----|-------------|
| `model` | `deepseek-chat` | 兼容 OpenAI API 格式的国产模型 |
| `temperature` | `0` | 数据分析需要确定性，相同输入应得到相同分析策略 |
| `streaming` | `True` | 用户实时看到 Agent 的思考过程和工具调用 |
| `max_retries` | `3` | 网络不稳定时自动重试，提高可靠性 |

> **💡 知识点**：LangChain 的 `ChatOpenAI` 支持 OpenAI API 兼容的任何服务（DeepSeek、Azure OpenAI、本地部署的 vLLM 等），只需修改 `base_url`。

### 为什么用 ChatOpenAI 而不是原生 SDK？

```python
# ❌ 使用原生 OpenAI SDK — 需要手动管理消息格式
import openai
client = openai.Client(api_key="sk-xxx", base_url="...")
response = client.chat.completions.create(
    model="deepseek-chat",
    messages=[{"role": "user", "content": "..."}]
)

# ✅ 使用 LangChain ChatOpenAI — 无缝接入 LangGraph
from langchain_openai import ChatOpenAI
llm = ChatOpenAI(model="deepseek-chat", ...)
# LangGraph 自动处理消息格式转换、工具调用解析等
```

---

## 4.2 创建工具列表

工具通过闭包工厂模式创建：

```python
# langgraph_agent.py:2739
tools = _make_tools(session)
```

### 工厂函数 `_make_tools` 详解

```python
# langgraph_agent.py:1196
def _make_tools(session: _Session) -> list:
    """为每个会话创建一组绑定了该会话状态的工具"""

    # 阶段验证器（所有工具共用）
    def _validate_tool_stage(tool_name: str) -> Optional[str]:
        is_valid, error_msg = ToolStageValidator.validate_tool_call(
            tool_name=tool_name,
            current_stage=session.state_machine.current_stage,
            tools_used=session.state_machine.tools_used,
            findings_count=len(session.findings)
        )
        return error_msg if not is_valid else None

    @tool
    def load_data(file_path: str, sheet_name: str = "") -> str:
        """Load a CSV or Excel file..."""
        error_msg = _validate_tool_stage("load_data")
        if error_msg:
            return f"[ERROR] {error_msg}"
        # ... 具体实现，可访问 session 变量

    @tool
    def python_repl(code: str) -> str:
        """Execute Python code..."""
        error_msg = _validate_tool_stage("python_repl")
        if error_msg:
            return f"[ERROR] {error_msg}"
        # ... 具体实现

    # ... 其他工具

    return [load_data, python_repl, eda_profile,
            record_finding, declare_metric,
            declare_assumption, finish_report]
```

### 闭包工作原理

```python
# 闭包（Closure）示意
def outer(x):
    def inner(y):
        return x + y   # inner 可以访问 outer 的变量 x
    return inner

add_5 = outer(5)       # x=5 被闭包捕获
print(add_5(3))        # 输出 8

# 同理，_make_tools 中：
def _make_tools(session):          # session 被闭包捕获
    @tool
    def load_data(file_path):
        session.ns["df"] = ...     # 工具函数可以访问 session
    return [load_data]
```

**每个会话创建独立的工具列表**，工具通过闭包绑定到各自会话的 `_Session` 实例。

---

## 4.3 构建 ReAct Agent 图

```python
# langgraph_agent.py:2749
from langgraph.prebuilt import create_react_agent

agent = create_react_agent(llm, tools, prompt=_SYSTEM_PROMPT.strip())
```

这一行代码做了什么？

### `create_react_agent` 内部流程（源码简化版）

```python
def create_react_agent(model, tools, prompt=None):
    """创建一个 ReAct Agent"""

    # 1. 绑定工具到 LLM
    #    告诉 LLM 它可以使用哪些工具，以及每个工具的参数格式
    llm_with_tools = model.bind_tools(tools)

    # 2. 定义 agent 节点函数
    def agent_node(state):
        # 如果有系统提示词，插入到消息列表最前面
        messages = state["messages"]
        if prompt:
            messages = [SystemMessage(content=prompt)] + messages

        # 调用 LLM
        response = llm_with_tools.invoke(messages)
        return {"messages": [response]}

    # 3. 定义 tools 节点函数
    def tools_node(state):
        last_message = state["messages"][-1]
        tool_calls = last_message.tool_calls

        tool_messages = []
        for call in tool_calls:
            # 找到对应的工具函数并执行
            tool_func = find_tool(tools, call["name"])
            result = tool_func(**call["args"])
            tool_messages.append(
                ToolMessage(content=str(result), tool_call_id=call["id"])
            )
        return {"messages": tool_messages}

    # 4. 定义条件路由函数
    def should_continue(state):
        last_message = state["messages"][-1]
        if last_message.tool_calls:
            return "tools"   # 需要调用工具
        return END           # 任务完成

    # 5. 构建 StateGraph
    graph = StateGraph(dict)  # 状态是 dict，包含 "messages" 键

    graph.add_node("agent", agent_node)
    graph.add_node("tools", tools_node)

    graph.set_entry_point("agent")                    # 入口：agent 节点
    graph.add_conditional_edges("agent", should_continue, {
        "tools": "tools",
        END: END
    })
    graph.add_edge("tools", "agent")                  # tools → agent 循环

    # 6. 编译图
    return graph.compile()
```

### 等价的手动构建代码

如果你想手动构建同样的图（不使用 `create_react_agent`）：

```python
from langgraph.graph import StateGraph, END
from langchain_core.messages import SystemMessage, ToolMessage

# 定义状态类型
class AgentState(TypedDict):
    messages: Annotated[list, add_messages]

# 创建图
graph = StateGraph(AgentState)

# 添加节点
graph.add_node("agent", agent_function)
graph.add_node("tools", tools_function)

# 设置入口
graph.set_entry_point("agent")

# 添加条件边
graph.add_conditional_edges("agent", route_after_agent, {
    "tools": "tools",
    END: END
})

# 添加普通边
graph.add_edge("tools", "agent")

# 编译
agent = graph.compile()
```

> **💡 学习建议**：先用 `create_react_agent` 快速上手，等你理解了 ReAct 模式后，再尝试手动构建自定义图。

---

## 4.4 构建用户消息

```python
# langgraph_agent.py:2751-2755
from langchain_core.messages import HumanMessage

user_msg = HumanMessage(content=(
    f"Task: {instruction}\n"
    f"Data file: {source_path}\n"
    "Please start by calling load_data to understand the dataset."
))
```

LangChain 消息类型：

| 类型 | 作用 | 在 ReAct 中的位置 |
|------|------|------------------|
| `SystemMessage` | 系统指令（角色定义、规则） | 最前面，由 `prompt` 参数注入 |
| `HumanMessage` | 用户输入 | 第一条用户消息 |
| `AIMessage` | LLM 回复 | Agent 节点产生 |
| `ToolMessage` | 工具执行结果 | Tools 节点产生 |

### 消息流转过程

```
初始消息列表:
  [SystemMessage("你是数据分析专家..."), HumanMessage("分析销售数据")]

第 1 轮 Agent 节点后:
  [SystemMessage(...), HumanMessage(...),
   AIMessage(tool_calls=[{name:"load_data", args:{...}}])]

第 1 轮 Tools 节点后:
  [SystemMessage(...), HumanMessage(...),
   AIMessage(tool_calls=[...]),
   ToolMessage("Shape: 200×8 ...")]

第 2 轮 Agent 节点后:
  [SystemMessage(...), HumanMessage(...),
   AIMessage(tool_calls=[...]),
   ToolMessage("Shape: 200×8 ..."),
   AIMessage(tool_calls=[{name:"eda_profile", args:{...}}])]

...（持续追加）...

最终:
  [SystemMessage(...), HumanMessage(...),
   ... 多轮消息 ...
   AIMessage(content="分析完成。主要发现：...")]  ← 无 tool_calls，触发 END
```

---

## 4.5 执行配置

```python
# langgraph_agent.py:2757
config = {"recursion_limit": 60}
```

### `recursion_limit` 详解

`recursion_limit` 控制图的最大递归深度。在 ReAct 循环中：

```
agent → tools → agent → tools → ... → agent → END
  1       2      3       4           N-1      N
```

每一跳（从一个节点到另一个节点）算一次递归。设为 60 意味着最多 60 次节点跳转，即最多约 30 轮 agent-tools 循环。

### 双重限制机制

本项目用了两层限制：

```python
# LangGraph 层面
config = {"recursion_limit": 60}         # 最多 60 次节点跳转

# 业务层面
_MAX_AGENT_STEPS = 48                    # 最多 48 次工具调用
```

为什么需要两层？

- `recursion_limit` 是 LangGraph 的安全机制，防止图无限循环
- `_MAX_AGENT_STEPS` 是业务层面的控制，在 `on_tool_start` 事件中检查

```python
# langgraph_agent.py:2788-2805
if kind == "on_tool_start":
    step += 1
    if step > _MAX_AGENT_STEPS:
        # 主动停止，给出明确的业务提示
        yield "Analysis stopped: exceeded the maximum tool-step budget."
        return
```

---

## 4.6 运行 Agent 的两种方式

### 同步运行（适合调试）

```python
result = agent.invoke(
    {"messages": [user_msg]},
    config=config
)
print(result["messages"][-1].content)
```

返回完整的最终状态（所有消息）。适合不需要流式输出的场景。

### 异步流式运行（本项目使用）

```python
async for event in agent.astream_events(
    {"messages": [user_msg]},
    config=config,
    version="v2"               # 使用 v2 事件格式
):
    kind = event["event"]
    if kind == "on_chat_model_stream":
        chunk = event["data"]["chunk"].content
        print(chunk, end="")   # 逐 token 输出
```

实时产生事件，不等待全部完成。本项目使用这种方式通过 SSE 推送给前端。

> **⚠️ 注意**：`version="v2"` 是必需的。v1 格式已废弃，v2 事件结构更清晰。

---

## 4.7 完整构建代码一览

以下是 `run_analysis_stream` 中从 0 到运行的完整代码：

```python
# langgraph_agent.py:2720-2764（简化注释版）
async def run_analysis_stream(
    instruction: str,          # 用户分析需求
    source_path: str,          # 数据文件路径
    workspace_dir: str,        # 会话工作目录
    api_key: str,              # LLM API 密钥
    model_id: str,             # 模型名称
    api_base: str,             # API 地址
    session_id: str,           # 会话 ID
    cancel_event: Optional[asyncio.Event] = None,
) -> AsyncGenerator[tuple[str, list], None]:
    """异步生成器，实时 yield (文本片段, 新文件) 对"""

    # ① 创建追踪上下文
    trace_context = create_trace_context(session_id, instruction)

    # ② 创建会话状态
    session = _Session(workspace_dir, source_path, session_id, instruction)

    # ③ 创建工具（绑定到 session）
    tools = _make_tools(session)

    # ④ 创建 LLM
    llm = ChatOpenAI(
        model=model_id,
        api_key=api_key,
        base_url=api_base,
        temperature=0,
        streaming=True,
        max_retries=3,
    )

    # ⑤ 构建 ReAct Agent 图
    agent = create_react_agent(llm, tools, prompt=_SYSTEM_PROMPT.strip())

    # ⑥ 构建用户消息
    user_msg = HumanMessage(content=(
        f"Task: {instruction}\n"
        f"Data file: {source_path}\n"
        "Please start by calling load_data to understand the dataset."
    ))

    # ⑦ 流式运行
    config = {"recursion_limit": 60}
    step = 0
    async for event in agent.astream_events(
        {"messages": [user_msg]},
        config=config,
        version="v2"
    ):
        # 处理事件...
        yield text_chunk, artifacts
```

---

> **下一步**：阅读 [05-tools.md](05-tools.md) 深入学习 7 个工具的实现细节。

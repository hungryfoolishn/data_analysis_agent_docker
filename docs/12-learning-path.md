# 12 - 学习路径与 API 速查

> 本文提供从入门到进阶的学习路径建议、LangGraph/LangChain API 速查表，以及本项目关键代码索引。

---

## 12.1 五阶段学习路径

### 阶段 1：理解基础概念（1-2 天）

**目标**：理解 LangGraph 是什么、ReAct 模式的基本原理。

**阅读顺序**：
1. [01-langgraph-intro.md](01-langgraph-intro.md) — LangGraph 是什么
2. [02-react-agent.md](02-react-agent.md) — ReAct 模式

**练习**：
```python
# 创建一个最简单的 ReAct Agent
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool
from langchain_core.messages import HumanMessage
from langgraph.prebuilt import create_react_agent

@tool
def add(a: int, b: int) -> int:
    """两数相加"""
    return a + b

llm = ChatOpenAI(model="deepseek-chat", base_url="https://api.deepseek.com/v1")
agent = create_react_agent(llm, [add])

result = agent.invoke({"messages": [HumanMessage(content="3+5等于多少？")]})
print(result["messages"][-1].content)
```

### 阶段 2：理解 Agent 图的构建（2-3 天）

**目标**：理解 `create_react_agent` 的内部机制。

**阅读顺序**：
1. [04-agent-graph.md](04-agent-graph.md) — Agent 图构建
2. [07-streaming-sse.md](07-streaming-sse.md) — 流式输出

**练习**：
- 修改系统提示词，改变 Agent 的行为
- 尝试手动构建一个自定义的 StateGraph（不使用 `create_react_agent`）

```python
# 练习：手动构建自定义图
from langgraph.graph import StateGraph, END
from typing import TypedDict, Annotated
from langgraph.graph import add_messages

class MyState(TypedDict):
    messages: Annotated[list, add_messages]
    step_count: int

graph = StateGraph(MyState)
graph.add_node("agent", my_agent_function)
graph.add_node("tools", my_tools_function)
graph.set_entry_point("agent")
graph.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
graph.add_edge("tools", "agent")
app = graph.compile()
```

### 阶段 3：理解工具和状态（3-5 天）

**目标**：掌握工具定义、闭包工厂、状态管理。

**阅读顺序**：
1. [05-tools.md](05-tools.md) — 工具系统
2. [06-state-management.md](06-state-management.md) — 状态管理

**练习**：
- 为 Agent 添加一个自定义工具
- 实现一个带计数器的工具（统计调用次数）

```python
# 练习：带计数器的工具工厂
def make_counted_tools():
    call_counts = {}

    @tool
    def search(query: str) -> str:
        """搜索信息"""
        call_counts["search"] = call_counts.get("search", 0) + 1
        print(f"search 被调用了 {call_counts['search']} 次")
        return f"搜索 '{query}' 的结果..."

    return [search], call_counts
```

### 阶段 4：理解状态机和验证（3-5 天）

**目标**：掌握状态机设计、阶段约束、数据验证。

**阅读顺序**：
1. [08-state-machine.md](08-state-machine.md) — 状态机
2. [09-data-models.md](09-data-models.md) — 数据模型
3. [10-error-recovery.md](10-error-recovery.md) — 错误恢复

**练习**：
- 为自己的 Agent 设计一个简单的状态机（INIT → PROCESSING → DONE）
- 用 Pydantic 定义自己的数据模型并添加验证

```python
# 练习：简单状态机
class SimpleStage(str, Enum):
    INIT = "init"
    LOADING = "loading"
    PROCESSING = "processing"
    DONE = "done"

TRANSITIONS = {
    SimpleStage.INIT: {SimpleStage.LOADING},
    SimpleStage.LOADING: {SimpleStage.PROCESSING},
    SimpleStage.PROCESSING: {SimpleStage.DONE},
}

class MyStateMachine:
    def __init__(self):
        self.stage = SimpleStage.INIT

    def transition(self, target):
        if target in TRANSITIONS.get(self.stage, set()):
            self.stage = target
        else:
            raise ValueError(f"Cannot go from {self.stage} to {target}")
```

### 阶段 5：理解完整系统（5-7 天）

**目标**：理解整个项目的设计和实现。

**阅读顺序**：
1. [03-architecture.md](03-architecture.md) — 架构总览
2. [11-full-flow.md](11-full-flow.md) — 完整流程
3. 对照源码理解每个模块

**练习**：
- 尝试为 Agent 添加一个新工具（如 `sql_query`）
- 修改状态机添加新阶段
- 实现自己的错误恢复策略

---

## 12.2 LangGraph API 速查

### 创建预构建 Agent

```python
from langgraph.prebuilt import create_react_agent

agent = create_react_agent(
    model=llm,                      # ChatOpenAI 实例
    tools=[tool1, tool2],           # 工具列表
    prompt="你是一个...",            # 系统提示词（可选）
)
```

### 运行 Agent

```python
# 同步运行
result = agent.invoke(
    {"messages": [HumanMessage(content="...")]},
    config={"recursion_limit": 50}
)

# 异步流式运行
async for event in agent.astream_events(
    {"messages": [HumanMessage(content="...")]},
    config={"recursion_limit": 50},
    version="v2"
):
    kind = event["event"]
    name = event.get("name", "")
    data = event["data"]
```

### 自定义图

```python
from langgraph.graph import StateGraph, END

# 定义状态
class MyState(TypedDict):
    messages: Annotated[list, add_messages]

# 创建图
graph = StateGraph(MyState)

# 添加节点
graph.add_node("agent", agent_fn)
graph.add_node("tools", tools_fn)

# 添加边
graph.set_entry_point("agent")
graph.add_edge("tools", "agent")
graph.add_conditional_edges("agent", router, {
    "tools": "tools",
    END: END
})

# 编译
app = graph.compile()
```

---

## 12.3 LangChain API 速查

### 消息类型

```python
from langchain_core.messages import (
    SystemMessage,   # 系统指令
    HumanMessage,    # 用户消息
    AIMessage,       # AI 回复
    ToolMessage,     # 工具结果
)
```

### 工具定义

```python
from langchain_core.tools import tool

@tool
def my_tool(param1: str, param2: int = 0) -> str:
    """工具描述（给 LLM 看的）

    Args:
        param1: 参数1说明
        param2: 参数2说明
    """
    return "result"
```

### LLM 模型

```python
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(
    model="deepseek-chat",
    api_key="sk-xxx",
    base_url="https://api.deepseek.com/v1",
    temperature=0,
    streaming=True,
)
```

---

## 12.4 本项目关键代码索引

### 按技术点查找

| 你想学习 | 文件 | 行号 | 核心代码 |
|----------|------|------|---------|
| 创建 ReAct Agent | langgraph_agent.py | 2749 | `create_react_agent(llm, tools, prompt)` |
| 定义 Tool | langgraph_agent.py | 1196 | `def _make_tools(session)` |
| 流式处理事件 | langgraph_agent.py | 2764 | `agent.astream_events(..., version="v2")` |
| 构建 LLM | langgraph_agent.py | 2741 | `ChatOpenAI(streaming=True, ...)` |
| 状态管理 | langgraph_agent.py | 447 | `class _Session` |
| 代码沙箱执行 | langgraph_agent.py | 1160 | `exec() + threading + timeout` |
| 状态机定义 | state_machine.py | 13 | `class AnalysisStage` |
| 状态机转换 | state_machine.py | 110 | `VALID_TRANSITIONS` |
| 工具权限控制 | tool_validators.py | 18 | `TOOL_STAGE_REQUIREMENTS` |
| 数据模型 | schemas.py | 143 | `class Finding` |
| 错误恢复策略 | api_server_langgraph.py | 77 | `_failure_policy()` |
| 系统提示词 | langgraph_agent.py | 203 | `_SYSTEM_PROMPT` |
| 代码格式验证 | langgraph_agent.py | 108 | `_validate_python_repl_step()` |
| 报告验证 | langgraph_agent.py | 2175 | `def finish_report` |
| SSE 端点 | api_server_langgraph.py | — | `StreamingResponse` |
| 会话管理 | api_server_langgraph.py | 308 | `get_session_workspace` |
| 证据等级体系 | langgraph_agent.py | 802 | `_assess_evidence_level` |
| 指标变化分解 | langgraph_agent.py | 709 | `_decompose_metric_change` |
| 驱动因素排名 | langgraph_agent.py | 818 | `_rank_driver_candidates` |

### 按文件查找

| 文件 | 核心内容 |
|------|---------|
| [langgraph_agent.py](../langgraph_langchain/langgraph_agent.py) | Agent 核心：图构建、工具、状态、流式 |
| [api_server_langgraph.py](../langgraph_langchain/api_server_langgraph.py) | FastAPI 服务、会话、SSE |
| [schemas.py](../langgraph_langchain/schemas.py) | Pydantic 数据模型 |
| [state_machine.py](../langgraph_langchain/state_machine.py) | 分析状态机 |
| [tool_validators.py](../langgraph_langchain/tool_validators.py) | 工具阶段验证 |

---

## 12.5 常见问题

### Q: 为什么不用 LangGraph 的自定义 StateGraph？

A: 本项目的业务状态（DataFrame、发现列表、命名空间等）不适合放在 LangGraph 的 State 中传递。LangGraph 的 State 序列化为 JSON，而 DataFrame 和函数对象无法序列化。所以用 `_Session` 闭包管理业务状态。

### Q: 为什么用 DeepSeek 而不是 OpenAI？

A: DeepSeek 的 API 完全兼容 OpenAI 格式，只需改 `base_url`。成本更低，中文能力更强。通过 LangChain 的 `ChatOpenAI` 可以无缝切换。

### Q: `exec()` 安全吗？

A: 在生产环境中 `exec()` 有安全风险。本项目通过以下方式缓解：
- 在子线程中执行，有超时控制
- 限制代码行数和步数
- 限制了可用的模块（只预置了分析相关的函数）

### Q: 如何添加新工具？

1. 在 `_make_tools` 函数中添加新的 `@tool` 函数
2. 在 `tool_validators.py` 中注册工具的阶段权限
3. 在系统提示词中说明工具的用法
4. 测试工具在不同阶段的调用

### Q: 如何切换到其他 LLM？

只需修改环境变量：

```bash
# 切换到 OpenAI
DEEPSEEK_API_KEY=sk-openai-xxx
DEEPSEEK_MODEL_ID=gpt-4
DEEPSEEK_API_BASE=https://api.openai.com/v1

# 切换到本地模型（如 Ollama）
DEEPSEEK_API_KEY=dummy
DEEPSEEK_MODEL_ID=llama3
DEEPSEEK_API_BASE=http://localhost:11434/v1
```

---

> **推荐阅读**：
> - [LangGraph 官方文档](https://langchain-ai.github.io/langgraph/)
> - [LangChain 官方文档](https://python.langchain.com/)
> - [ReAct 论文](https://arxiv.org/abs/2210.03629)
> - [Pydantic 文档](https://docs.pydantic.dev/)

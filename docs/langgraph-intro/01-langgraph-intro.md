# 01 - LangGraph 是什么

> 本文从零讲解 LangGraph 框架的定位、解决的问题、核心概念，帮助你建立对 LangGraph 的基本认知。

---

## 1.1 AI 应用的演进

### 第一代：简单的 LLM 调用

最早期，我们直接调用 LLM API：

```python
import openai
response = openai.ChatCompletion.create(
    model="gpt-4",
    messages=[{"role": "user", "content": "分析这份数据"}]
)
print(response.choices[0].message.content)
```

**局限性**：LLM 无法执行代码、读取文件、画图表——它只能生成文本。

### 第二代：LangChain — 给 LLM 加工具

LangChain 出现后，我们可以给 LLM 配备工具：

```python
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool

@tool
def search_database(query: str) -> str:
    """搜索数据库"""
    return db.execute(query)

llm = ChatOpenAI(model="gpt-4")
llm_with_tools = llm.bind_tools([search_database])
```

**局限**：调用是线性的（Prompt → LLM → 输出），无法表达"如果结果有问题就重试"这类复杂逻辑。

### 第三代：LangGraph — 构建有状态的 AI 工作流

LangGraph 在 LangChain 之上，提供了 **图编排** 能力：

```
用户提问 → Agent 思考 → 调用工具 A → 根据结果决定
    → 如果需要更多信息 → 调用工具 B → 再次思考
    → 如果足够了 → 生成最终回答
```

这就是一个 **有向图**（Directed Graph），节点是处理步骤，边是流转关系。

---

## 1.2 LangGraph 解决的四大问题

### 问题 1：LLM 调用是无状态的

每次调用 LLM 都是独立的，无法自动记住之前的上下文。

**LangGraph 的解法**：通过 **State（状态）** 在图的节点之间传递数据。

```
Node A 产生数据 → 存入 State → Node B 从 State 读取数据
```

### 问题 2：复杂任务需要多步骤

数据分析需要"加载数据 → 探索 → 深入分析 → 生成报告"等多步骤。

**LangGraph 的解法**：通过 **Node（节点）** 和 **Edge（边）** 编排多步骤工作流。

```
[加载数据] → [探索分析] → [深入分析] → [生成报告]
```

### 问题 3：需要条件分支

根据中间结果决定下一步做什么（比如数据质量差就走错误处理路径）。

**LangGraph 的解法**：通过 **Conditional Edge（条件边）** 实现分支。

```
[分析结果] → 数据质量好 → [继续分析]
           → 数据质量差 → [错误处理]
```

### 问题 4：需要循环

Agent 可能需要多轮"思考 → 行动 → 观察"才能完成任务。

**LangGraph 的解法**：图天然支持循环（从后面的节点连回前面的节点）。

```
[Agent 思考] → [执行工具] → [Agent 再思考] → [再执行工具] → ...
                     ↑_________________________↓
```

---

## 1.3 LangGraph vs 纯 LangChain

| 特性 | LangChain | LangGraph |
|------|-----------|-----------|
| 调用模式 | 线性：Prompt → LLM → 输出 | 图：多节点、多边、可循环 |
| 状态管理 | 无内置状态 | 内置 State 管理 |
| 条件分支 | 需手动 if/else | 原生支持 Conditional Edge |
| 循环 | 不支持 | 原生支持 |
| 人工介入 | 无内置支持 | 支持 Human-in-the-loop |
| 流式输出 | 基础支持 | 增强的流式事件系统 |
| 错误处理 | 手动 try/catch | 内置重试和回退机制 |
| 适用场景 | 简单的 LLM 应用 | 复杂的多步骤 AI 工作流 |

**一句话总结**：
- **LangChain** 是 LLM 调用的工具箱（提供模型接口、工具定义等基础设施）
- **LangGraph** 是构建复杂 AI 工作流的编排引擎（在 LangChain 之上）

---

## 1.4 四大核心概念

### 概念 1：State（状态）

状态是在图的节点之间传递的 **共享数据结构**。

```python
# LangGraph 内置的 ReAct Agent 使用 messages 列表作为状态
from typing import Annotated
from typing_extensions import TypedDict
from langgraph.graph import add_messages

class AgentState(TypedDict):
    messages: Annotated[list, add_messages]  # 消息列表，自动追加
```

**本项目扩展**：在 LangGraph 内置状态之外，本项目通过 `_Session` 类管理额外的业务状态：

```python
# langgraph_agent.py:447
class _Session:
    def __init__(self, workspace_dir, source_path, session_id, user_question):
        self.workspace_dir = Path(workspace_dir)     # 工作目录
        self.current_stage = "schema_understanding"  # 当前分析阶段
        self.ns = { ... }                             # 持久化的 Python 命名空间
        self.findings = []                            # 分析发现列表
        self.report = None                            # 最终报告
```

> **设计要点**：LangGraph 的 `create_react_agent` 内部管理 `messages` 列表（对话历史），而本项目的业务状态（分析阶段、发现列表、命名空间等）通过 `_Session` 闭包传递给工具。

### 概念 2：Node（节点）

节点是图中的处理单元。每个节点是一个 Python 函数：

```python
def my_node(state: AgentState) -> AgentState:
    """接收当前状态，返回更新后的状态"""
    # 处理逻辑
    return updated_state
```

在 ReAct Agent 中，LangGraph 预定义了两个核心节点：

```
┌─────────────┐     ┌──────────────┐
│  agent 节点  │────→│  tool 节点    │
│ (LLM 决策)   │←────│ (执行工具)    │
└─────────────┘     └──────────────┘
```

- **agent 节点**：将消息历史发给 LLM，LLM 决定是回复用户还是调用工具
- **tool 节点**：执行 LLM 选择的工具，将结果追加到消息历史

### 概念 3：Edge（边）

边定义节点之间的流转关系。LangGraph 支持三种边：

| 类型 | 说明 | 图示例 |
|------|------|--------|
| **普通边** | A → B（固定流转） | `graph.add_edge("A", "B")` |
| **条件边** | A → B 或 A → C（根据条件选择） | `graph.add_conditional_edges("A", router)` |
| **入口边** | 起点 → 第一个节点 | `graph.set_entry_point("agent")` |

在 ReAct Agent 中，关键的 **条件边** 是：

```python
# 伪代码：agent 节点的条件路由
def should_continue(state):
    last_message = state["messages"][-1]
    if last_message.tool_calls:    # LLM 要求调用工具
        return "tools"
    else:                          # LLM 直接回复，任务完成
        return END

graph.add_conditional_edges("agent", should_continue, {
    "tools": "tools",
    END: END
})
```

### 概念 4：Tool（工具）

工具是 Agent 可以调用的外部函数。LangGraph 使用 LangChain 的 `@tool` 装饰器：

```python
from langchain_core.tools import tool

@tool
def search_web(query: str) -> str:
    """搜索互联网获取信息。
    
    Args:
        query: 搜索关键词
    """
    results = web_search(query)
    return results
```

> **关键**：函数的 **docstring** 和 **类型注解** 非常重要！LLM 通过阅读它们来理解工具的用途和参数格式。

---

## 1.5 一张图理解 LangGraph

```
                    ┌──────────────────────────────────┐
                    │         LangGraph 生态             │
                    │                                   │
  ┌──────────┐     │  ┌─────────────────────────────┐  │
  │ LangChain │     │  │    StateGraph / ReAct Agent  │  │
  │ (基础设施) │────→│  │                             │  │
  │ - LLM 接口│     │  │  ┌───────┐    ┌──────────┐  │  │
  │ - Tool 定义│    │  │  │ Agent │←──→│  Tools   │  │  │
  │ - 消息类型│     │  │  │ (LLM) │    │ (函数)   │  │  │
  └──────────┘     │  │  └───────┘    └──────────┘  │  │
                    │  │       │                      │  │
                    │  │       ▼                      │  │
                    │  │  ┌──────────────────┐       │  │
                    │  │  │  State (状态)     │       │  │
                    │  │  │  - messages      │       │  │
                    │  │  │  - 自定义状态     │       │  │
                    │  │  └──────────────────┘       │  │
                    │  └─────────────────────────────┘  │
                    │           │                        │
                    │           ▼                        │
                    │  ┌──────────────────┐              │
                    │  │  运行时特性       │              │
                    │  │  - 流式输出       │              │
                    │  │  - 人工介入       │              │
                    │  │  - 错误恢复       │              │
                    │  │  - 持久化         │              │
                    │  └──────────────────┘              │
                    └──────────────────────────────────┘
```

---

## 1.6 安装与快速体验

### 安装

```bash
# 安装 LangChain + LangGraph
pip install langchain langgraph langchain-openai

# 或使用项目的 requirements
pip install -r langgraph_langchain/requirements-langgraph.txt
```

### 最简 ReAct Agent 示例

```python
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool
from langchain_core.messages import HumanMessage
from langgraph.prebuilt import create_react_agent

# 1. 定义工具
@tool
def add(a: int, b: int) -> int:
    """两个数字相加"""
    return a + b

@tool
def multiply(a: int, b: int) -> int:
    """两个数字相乘"""
    return a * b

# 2. 创建 LLM
llm = ChatOpenAI(model="gpt-4", temperature=0)

# 3. 创建 ReAct Agent（一行代码！）
agent = create_react_agent(llm, [add, multiply])

# 4. 运行
result = agent.invoke({
    "messages": [HumanMessage(content="3 加 5 再乘以 2 等于多少？")]
})

# 5. 查看结果
print(result["messages"][-1].content)
# 输出: "3 加 5 等于 8，再乘以 2 等于 16。"
```

**发生了什么？**

```
第 1 轮：LLM 思考 → "先计算 3+5" → 调用 add(3, 5) → 返回 8
第 2 轮：LLM 思考 → "再把 8 乘以 2" → 调用 multiply(8, 2) → 返回 16
第 3 轮：LLM 思考 → "已有答案" → 直接回复 "3 加 5 等于 8，再乘以 2 等于 16"
```

---

## 1.7 总结

| 概念 | 一句话解释 |
|------|-----------|
| **LangGraph** | 构建有状态、多步骤 AI 工作流的编排引擎 |
| **State** | 在图的节点间流转的共享数据 |
| **Node** | 图中的处理步骤（LLM 调用、工具执行等） |
| **Edge** | 节点间的流转关系（普通边、条件边） |
| **Tool** | Agent 可调用的外部函数 |
| **ReAct** | Reasoning + Acting 的 Agent 模式 |

---

> **下一步**：阅读 [02-react-agent.md](02-react-agent.md) 深入理解 ReAct 模式。

# 02 - ReAct Agent 模式详解

> 本文深入讲解 ReAct（Reasoning + Acting）模式——LangGraph Agent 的核心运行方式，并结合本项目展示实际运作过程。

---

## 2.1 什么是 ReAct？

**ReAct** 全称 **Re**asoning + **Act**ing，是 2022 年的一篇论文（Yao et al.）提出的 Agent 模式。核心思想：

> 让 LLM 交替进行 **推理**（思考当前情况）和 **行动**（调用工具），通过多轮迭代完成复杂任务。

### 类比：人类分析师的工作方式

想象一个人类数据分析师接到任务"分析这份销售数据"：

```
思考: "我需要先打开文件看看数据长什么样"
行动: 打开 sales.csv，看到 200 行 × 8 列
观察: 有 date, region, product, revenue, quantity 等列

思考: "数据看起来有 region 列，我应该按区域分析 revenue"
行动: 用 Excel 透视表按 region 汇总 revenue
观察: 华东区 revenue 最高（42%），西北区最低（3%）

思考: "华东区为什么高？需要看时间趋势"
行动: 画华东区月度 revenue 折线图
观察: Q3 明显上升，8 月达到峰值

思考: "Q3 上升可能和促销活动有关"
行动: 检查 Q3 是否有促销标记
观察: 发现 8 月有促销活动标记

思考: "分析已经充分，可以写报告了"
行动: 撰写分析报告
```

**ReAct 就是让 LLM 模仿这个过程！**

---

## 2.2 ReAct 循环的三个阶段

每一轮 ReAct 循环包含三个阶段：

```
┌─────────────────────────────────────────────────┐
│                  ReAct 循环                      │
│                                                 │
│   ┌──────────┐     ┌──────────┐     ┌────────┐ │
│   │ Reasoning │────→│  Action  │────→│Observe │ │
│   │ (推理思考) │     │ (执行行动) │     │(观察结果)│ │
│   └──────────┘     └──────────┘     └────────┘ │
│        ↑                                 │       │
│        └─────────────────────────────────┘       │
│                    (继续循环)                     │
└─────────────────────────────────────────────────┘
```

### Reasoning（推理）

LLM 分析当前情况，决定下一步做什么。这一步的输出通常是：
- 一个想法（Thought）：对当前情况的分析
- 一个行动决策：选择调用哪个工具，传什么参数

```python
# LLM 的推理输出（伪代码）
Thought: 数据已加载，共 200 行 8 列。有 region 和 revenue 列。
         我应该按 region 分组统计 revenue，找出最高和最低的区域。
Action: python_repl
Action Input: {"code": "print(df.groupby('region')['revenue'].sum().sort_values(ascending=False))"}
```

### Action（行动）

执行 LLM 选择的工具函数。在本项目中，有 7 个工具可供选择。

### Observation（观察）

获取工具的返回结果，作为下一轮推理的输入。

```python
# 工具返回结果
Observation:
  region
  华东    420000
  华南    280000
  华北    180000
  西南    120000
  西北     30000
```

---

## 2.3 本项目的一次完整 ReAct 过程

以用户请求"分析这份销售数据的区域差异"为例：

```
════════════════════════════════════════════════════════════
用户输入: "分析这份销售数据的区域差异"
数据文件: /workspace/abc123/sales.csv
════════════════════════════════════════════════════════════

第 1 轮 ReAct:
┌─ Reasoning ─────────────────────────────────────────────┐
│ LLM: 我需要先加载数据了解其结构和字段                       │
└──────────────────────────────────────────────────────────┘
┌─ Action ────────────────────────────────────────────────┐
│ 调用 load_data(file_path="/workspace/abc123/sales.csv") │
└──────────────────────────────────────────────────────────┘
┌─ Observation ───────────────────────────────────────────┐
│ Shape: 200 rows × 8 columns                             │
│ Columns: date, region, product, category,               │
│          revenue, quantity, unit_price, discount         │
│ region (object) — 0 nulls                                │
│ revenue (float64) — 3 nulls                              │
│ Preview: (前5行数据)                                      │
└──────────────────────────────────────────────────────────┘
状态机: INIT → SCHEMA_UNDERSTANDING → DATA_QUALITY_CHECK

第 2 轮 ReAct:
┌─ Reasoning ─────────────────────────────────────────────┐
│ LLM: 数据有 region 和 revenue 列，需要先了解整体数据质量    │
└──────────────────────────────────────────────────────────┘
┌─ Action ────────────────────────────────────────────────┐
│ 调用 eda_profile()                                      │
└──────────────────────────────────────────────────────────┘
┌─ Observation ───────────────────────────────────────────┐
│ EDA Profile:                                            │
│ - Missing: revenue 3(1.5%), discount 12(6%)             │
│ - Correlation: revenue ~ quantity r=0.85                │
│ - Outliers: revenue 有 8 个异常值                        │
│ - Business hints: 按 region 分组分析 revenue             │
│ (同时生成了 4 张 EDA 图表)                                 │
└──────────────────────────────────────────────────────────┘
状态机: → BASIC_EDA

第 3 轮 ReAct:
┌─ Reasoning ─────────────────────────────────────────────┐
│ LLM: 需要按 region 深入分析 revenue 差异                   │
└──────────────────────────────────────────────────────────┘
┌─ Action ────────────────────────────────────────────────┐
│ 调用 python_repl(code="""                               │
│ # 步骤目标: 按 region 分析 revenue 差异                    │
│ # 方法: groupby + 描述统计 + 排名                          │
│ fix_chinese()                                            │
│ region_summary = df.groupby('region').agg(               │
│     total_revenue=('revenue', 'sum'),                    │
│     avg_revenue=('revenue', 'mean'),                     │
│     order_count=('revenue', 'count')                     │
│ ).sort_values('total_revenue', ascending=False)          │
│ print(region_summary)                                    │
│ # 关键结果: (待执行后填充)                                  │
│ # 建议下一步: 画区域对比图                                  │
│ """)                                                     │
└──────────────────────────────────────────────────────────┘
┌─ Observation ───────────────────────────────────────────┐
│           total_revenue  avg_revenue  order_count        │
│ 华东        420000        7000         60                │
│ 华南        280000        5600         50                │
│ 华北        180000        4500         40                │
│ 西南        120000        4000         30                │
│ 西北         30000        3000         10                │
└──────────────────────────────────────────────────────────┘
状态机: → DEEP_DIVE

第 4 轮 ReAct:
┌─ Reasoning ─────────────────────────────────────────────┐
│ LLM: 华东区 revenue 占比最高，需要可视化并记录发现           │
└──────────────────────────────────────────────────────────┘
┌─ Action ────────────────────────────────────────────────┐
│ 调用 python_repl(code="画区域 revenue 对比柱状图...")     │
└──────────────────────────────────────────────────────────┘
┌─ Observation ───────────────────────────────────────────┐
│ 图表已保存: revenue_by_region.png                         │
└──────────────────────────────────────────────────────────┘

第 5 轮 ReAct:
┌─ Reasoning ─────────────────────────────────────────────┐
│ LLM: 需要记录这个重要发现                                  │
└──────────────────────────────────────────────────────────┘
┌─ Action ────────────────────────────────────────────────┐
│ 调用 record_finding(                                     │
│   statement="华东区 revenue 占总量 42%，显著高于其他区域", │
│   evidence_text="华东区 total_revenue=420K, 占比 42%",    │
│   confidence_level="high",                               │
│   evidence_level="A",                                    │
│   source_fields=["region", "revenue"],                   │
│   stats={"north_revenue": 420000, "total": 1000000}      │
│ )                                                        │
└──────────────────────────────────────────────────────────┘
┌─ Observation ───────────────────────────────────────────┐
│ Finding F001 recorded.                                   │
└──────────────────────────────────────────────────────────┘

...（更多分析步骤）...

第 N 轮 ReAct:
┌─ Reasoning ─────────────────────────────────────────────┐
│ LLM: 分析完成，现在生成最终报告                             │
└──────────────────────────────────────────────────────────┘
┌─ Action ────────────────────────────────────────────────┐
│ 调用 finish_report(markdown="## 销售数据区域分析报告...") │
└──────────────────────────────────────────────────────────┘
┌─ Observation ───────────────────────────────────────────┐
│ Report accepted. 保存到 data_analysis_report.md          │
└──────────────────────────────────────────────────────────┘
状态机: → REPORT_GENERATION → COMPLETED

════════════════════════════════════════════════════════════
LLM 输出最终总结: "分析完成。华东区是 revenue 的主要贡献者..."
════════════════════════════════════════════════════════════
```

---

## 2.4 `create_react_agent` 内部构建了什么？

`create_react_agent` 是 LangGraph 的 **预构建函数**，一行代码创建完整的 ReAct 图：

```python
from langgraph.prebuilt import create_react_agent

agent = create_react_agent(
    model=llm,
    tools=[load_data, python_repl, eda_profile, ...],
    prompt="你是一个数据分析专家..."
)
```

**它内部自动构建了如下 StateGraph**：

```
              ┌────────────────────────────────────────────────┐
              │                 StateGraph                      │
              │                                                │
              │   State = {"messages": [HumanMessage,           │
              │                        AIMessage,               │
              │                        ToolMessage, ...]}       │
              │                                                │
START ────→  ┌──────────┐    tool_calls 存在    ┌──────────┐   │
             │  agent   │─────────────────────→│  tools   │   │
             │  (LLM)   │                      │ (执行器)  │   │
             └──────────┘                      └──────────┘   │
                  │                                  │         │
                  │ 无 tool_calls                     │ 执行完  │
                  │ (任务完成)                         │ 毕返回  │
                  ▼                                  │         │
                 END  ←──────────────────────────────┘         │
              │                    (循环回去)                    │
              └────────────────────────────────────────────────┘
```

### 内部节点详解

#### agent 节点

```python
# 伪代码 - agent 节点做了什么
def agent_node(state):
    # 1. 将所有消息发给 LLM
    response = llm.invoke(state["messages"])

    # 2. LLM 返回两种情况之一：
    #    a) 直接回复（content 有内容，无 tool_calls）→ 任务完成
    #    b) 要求调用工具（有 tool_calls 字段）→ 进入 tools 节点
    return {"messages": [response]}
```

#### tools 节点

```python
# 伪代码 - tools 节点做了什么
def tools_node(state):
    last_message = state["messages"][-1]  # AI 的最新回复
    tool_calls = last_message.tool_calls  # 要调用的工具列表

    results = []
    for call in tool_calls:
        tool_name = call["name"]       # 如 "python_repl"
        tool_args = call["args"]       # 如 {"code": "print(df.shape)"}
        output = execute_tool(tool_name, tool_args)  # 执行工具
        results.append(ToolMessage(content=output))

    return {"messages": results}
```

#### 条件路由

```python
# 伪代码 - agent 节点之后的条件路由
def should_continue(state):
    last_message = state["messages"][-1]
    if hasattr(last_message, "tool_calls") and last_message.tool_calls:
        return "tools"   # LLM 要求调用工具 → 进入 tools 节点
    return END           # LLM 直接回复 → 结束
```

---

## 2.5 本项目的 ReAct 配置

### LLM 配置

```python
# langgraph_agent.py:2741-2748
llm = ChatOpenAI(
    model="deepseek-chat",              # 使用 DeepSeek 模型
    api_key=DEEPSEEK_API_KEY,
    base_url="https://api.deepseek.com/v1",
    temperature=0,                      # 温度=0，保证分析结果确定性
    streaming=True,                     # 开启流式输出
    max_retries=3,                      # 请求失败自动重试 3 次
)
```

> **为什么 temperature=0？**
> 数据分析需要确定性和可重复性。如果 temperature > 0，相同的数据和分析请求可能每次产生不同结果。

### Agent 创建

```python
# langgraph_agent.py:2749
agent = create_react_agent(llm, tools, prompt=_SYSTEM_PROMPT.strip())
```

参数说明：
- `llm`：上面创建的 ChatOpenAI 实例
- `tools`：通过 `_make_tools(session)` 创建的 7 个工具
- `prompt`：系统提示词（_SYSTEM_PROMPT，约 230 行，定义了 Agent 的行为规范）

### 执行配置

```python
# langgraph_agent.py:2757
config = {"recursion_limit": 60}
```

`recursion_limit` 是 ReAct 循环的最大次数。每次 agent → tools → agent 算一轮。60 意味着最多 60 轮循环。

本项目还额外设置了更严格的限制：

```python
_MAX_AGENT_STEPS = 48                # 最多 48 次工具调用
_MAX_CONSECUTIVE_PYTHON_ERRORS = 3   # 最多连续 3 次 Python 错误
```

---

## 2.6 系统提示词（System Prompt）的作用

系统提示词是 ReAct Agent 的"灵魂"。它告诉 LLM：
1. 你是谁（角色定义）
2. 你应该怎么做（工作流程）
3. 你不能做什么（约束规则）

本项目的系统提示词位于 [langgraph_agent.py:203](../../langgraph_langchain/langgraph_agent.py#L203)，约 230 行，包含：

```python
_SYSTEM_PROMPT = """
You are an expert data analyst specializing in R&D management efficiency analysis.
你是一个专注于研发管理效率分析的数据分析专家。

## Domain expertise: R&D Management Efficiency
（领域知识：研发效率指标体系）

## Core objective
（核心目标：像一个优秀的业务分析师一样工作）

## Required workflow
1. Call load_data first
2. Call eda_profile
3. Use declare_metric
4. Use declare_assumption
5. 使用 python_repl 深入分析
6. 使用 record_finding 记录发现
7. 使用 finish_report 生成报告

## Analysis Stage Requirements (ENFORCED)
（阶段要求：每个阶段可以调用哪些工具）

## python_repl rules
（代码执行规则：每步只解决一个子目标，不超过 50 行等）

## Error recovery
（错误恢复策略）

## finish_report requirements
（报告质量要求）
"""
```

> **设计洞察**：系统提示词越长、越精确，Agent 的行为就越可预测。本项目的提示词包含大量约束（如"不允许在没有 C 级证据时使用因果语言"），这些约束直接影响了 Agent 的输出质量。

---

## 2.7 ReAct vs 其他 Agent 模式

| Agent 模式 | 特点 | 适用场景 |
|-----------|------|---------|
| **ReAct** | 交替推理和行动，灵活 | 复杂的多步骤任务（✅ 本项目） |
| **Plan-and-Execute** | 先制定完整计划，再逐步执行 | 任务步骤明确可预规划 |
| **Reflexion** | 自我反思和改进 | 需要自我纠错的任务 |
| **MRKL** | 模块化推理、知识和语言 | 知识密集型任务 |

本项目选择 ReAct 的原因：
1. 数据分析过程不可完全预规划（取决于数据内容）
2. 需要根据中间结果灵活调整分析方向
3. Agent 需要在多个工具之间自由切换

---

> **下一步**：阅读 [03-architecture.md](03-architecture.md) 了解项目整体架构。

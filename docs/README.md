# LangGraph 技术入门指南 — 基于 DeepAnalyze 项目实战

> 本系列文档以 DeepAnalyze 数据分析系统为实战案例，从零讲解 LangGraph 核心概念、架构设计、代码实现。

## 📚 文档目录

### 入门篇

| # | 文档 | 内容 | 阅读时间 |
|---|------|------|---------|
| 01 | [LangGraph 是什么](01-langgraph-intro.md) | 框架定位、解决的核心问题、四大概念（State/Node/Edge/Tool） | 15 min |
| 02 | [ReAct Agent 模式](02-react-agent.md) | ReAct 原理、循环机制、本项目的实际运作过程 | 20 min |
| 03 | [项目架构总览](03-architecture.md) | 系统架构图、文件职责、数据流、关键设计决策 | 15 min |

### 核心篇

| # | 文档 | 内容 | 阅读时间 |
|---|------|------|---------|
| 04 | [Agent 图的构建](04-agent-graph.md) | LLM 创建、工具绑定、`create_react_agent` 内部机制 | 20 min |
| 05 | [工具系统详解](05-tools.md) | 7 个工具的实现、阶段权限控制、验证机制 | 25 min |
| 06 | [状态管理详解](06-state-management.md) | `_Session` 类、持久化命名空间、`exec()` 机制 | 20 min |
| 07 | [流式输出 SSE 详解](07-streaming-sse.md) | `astream_events`、事件处理、SSE 协议 | 20 min |

### 进阶篇

| # | 文档 | 内容 | 阅读时间 |
|---|------|------|---------|
| 08 | [状态机与阶段控制](08-state-machine.md) | 8 个阶段、流转规则、条件机制、工具权限矩阵 | 20 min |
| 09 | [数据模型与 Schema](09-data-models.md) | Pydantic 模型体系：Finding、Evidence、Lineage | 15 min |
| 10 | [错误恢复与防护栏](10-error-recovery.md) | 四层防护、错误分类、自动重试、报告验证 | 15 min |
| 11 | [完整运行流程](11-full-flow.md) | 一次完整分析的端到端过程详解 | 15 min |
| 12 | [学习路径与速查](12-learning-path.md) | 5 阶段学习计划、API 速查、代码索引、FAQ | 10 min |

## 🗺️ 推荐阅读顺序

```
第一次阅读（建立认知）:
  01 → 02 → 03          （约 50 分钟）

深入理解（掌握核心）:
  04 → 05 → 06 → 07     （约 85 分钟）

进阶学习（理解设计）:
  08 → 09 → 10          （约 50 分钟）

实战参考:
  11 → 12               （约 25 分钟）
```

## 🛠️ 前置知识

- Python 基础（函数、类、装饰器、异步）
- 基本的 LLM 概念（Prompt、Token、Temperature）
- 不需要预先了解 LangChain 或 LangGraph

## 📁 项目代码位置

所有文档引用的代码位于 `langgraph_langchain/` 目录：

```
langgraph_langchain/
├── langgraph_agent.py        ← Agent 核心（~2900 行）
├── api_server_langgraph.py   ← FastAPI 服务（~750 行）
├── schemas.py                ← 数据模型（~230 行）
├── state_machine.py          ← 状态机（~270 行）
├── tool_validators.py        ← 工具验证（~160 行）
├── tracing.py                ← 分布式追踪
├── structured_logging.py     ← 结构化日志
├── recovery.py               ← 错误恢复
├── evidence_binding.py       ← 证据绑定验证
├── evidence_validator.py     ← 证据等级验证
├── recommendation_validator.py ← 建议验证
├── lineage_tracker.py        ← 数据血缘
├── rd_efficiency_domain.py   ← R&D 领域知识
├── rd_metric_library.py      ← R&D 指标库
├── rd_validators.py          ← R&D 验证器
└── rd_templates.py           ← R&D 分析模板
```

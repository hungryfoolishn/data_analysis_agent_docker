# 03 - 项目架构总览

> 本文从全局视角展示 DeepAnalyze 项目的架构设计、文件组织、组件关系，帮助你建立对整个系统的宏观理解。

---

## 3.1 系统架构图

```
┌───────────────────────────────────────────────────────────────────┐
│                     用户浏览器                                      │
│                   http://localhost:8501                            │
└───────────────────────────────┬───────────────────────────────────┘
                                │
                                │ HTTP / SSE
                                ▼
┌───────────────────────────────────────────────────────────────────┐
│                  Streamlit WebUI (port 8501)                       │
│                   webui/app.py                                     │
│  ┌──────────────┐  ┌───────────────┐  ┌────────────────────────┐  │
│  │ 文件上传组件  │  │ 分析对话界面   │  │ 结果展示（图表+报告）   │  │
│  └──────┬───────┘  └──────┬────────┘  └───────────▲────────────┘  │
│         │                 │                       │               │
└─────────┼─────────────────┼───────────────────────┼───────────────┘
          │ 上传文件         │ 发送分析请求           │ SSE 流式响应
          ▼                 ▼                       │
┌───────────────────────────────────────────────────────────────────┐
│                  FastAPI Backend (port 8888)                       │
│             api_server_langgraph.py (~750 行)                      │
│                                                                    │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │ HTTP 端点                                                     │  │
│  │  POST /v1/chat/completions    → 流式/非流式分析               │  │
│  │  POST /workspace/upload       → 文件上传                     │  │
│  │  GET  /workspace/files        → 列出会话文件                  │  │
│  │  GET  /workspace/files/{path} → 下载文件/图表                 │  │
│  │  DELETE /sessions/{id}        → 删除会话                     │  │
│  │  POST /sessions/{id}/cancel   → 取消运行中的分析              │  │
│  │  POST /sessions/{id}/resume   → 恢复中断的分析 (P3)          │  │
│  │  GET  /sessions/{id}/resumable → 检查会话是否可恢复 (P3)     │  │
│  │  GET  /health                 → 健康检查                     │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                    │
│  ┌──────────────┐  ┌───────────────┐  ┌────────────────────────┐  │
│  │ 会话管理      │  │ 并发控制       │  │ 错误恢复策略           │  │
│  │ SESSIONS dict│  │ Semaphore(3)  │  │ RecoveryExecutor       │  │
│  └──────────────┘  └───────────────┘  └────────────────────────┘  │
│                                │                                   │
└────────────────────────────────┼───────────────────────────────────┘
                                 │ 调用 run_analysis_stream()
                                 ▼
┌───────────────────────────────────────────────────────────────────┐
│                LangGraph ReAct Agent (~2900 行)                    │
│                langgraph_agent.py                                  │
│                                                                    │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │ Agent 核心                                                    │  │
│  │  ChatOpenAI ──→ create_react_agent ──→ astream_events(v2)   │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                    │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │ 9 个 Tool 工具 (Tools Registry)                               │  │
│  │  load_data │ eda_profile │ python_repl │ record_finding       │  │
│  │  declare_metric │ declare_assumption │ finish_report          │  │
│  │  delegate_analysis │ skill_view │ skill_reference             │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                    │
│  ┌──────────────────┐  ┌────────────────┐  ┌──────────────────┐  │
│  │ _Session 状态管理 │  │ 状态机控制      │  │ 运行时防护栏      │  │
│  │ (命名空间/发现)   │  │ (6阶段流转)     │  │ (行数/超时/错误)  │  │
│  └──────────────────┘  └────────────────┘  └──────────────────┘  │
│                                                                    │
│  ┌──────────────────┐  ┌────────────────┐  ┌──────────────────┐  │
│  │ Prompt Builder   │  │ Error Classifier│  │ Session Persist.  │  │
│  │ (模块化.md段)     │  │ (P2 智能分类)   │  │ (P3 断点续传)     │  │
│  └──────────────────┘  └────────────────┘  └──────────────────┘  │
└───────────────────────────────────────────────────────────────────┘
                                 │
                                 ▼
┌───────────────────────────────────────────────────────────────────┐
│              workspace/<session_id>/                               │
│  ┌──────────┐ ┌───────────┐ ┌──────────┐ ┌──────────────────┐    │
│  │ 数据文件  │ │ EDA 图表   │ │ 分析报告  │ │ 会话日志/元数据  │    │
│  │ sales.csv│ │ *.png     │ │ report.md│ │ *.log / *.json   │    │
│  └──────────┘ └───────────┘ └──────────┘ └──────────────────┘    │
└───────────────────────────────────────────────────────────────────┘
```

---

## 3.2 文件职责详解

### 核心文件

| 文件 | 行数 | 职责 | LangGraph 关联度 |
|------|------|------|:---:|
| [langgraph_agent.py](../../langgraph_langchain/langgraph_agent.py) | ~2900 | Agent 核心：LLM 配置、图构建、工具定义、流式生成 | ⭐⭐⭐⭐⭐ |
| [api_server_langgraph.py](../../langgraph_langchain/api_server_langgraph.py) | ~750 | FastAPI 服务：HTTP 接口、会话管理、SSE 流式 | ⭐⭐⭐ |
| [schemas.py](../../langgraph_langchain/schemas.py) | ~230 | Pydantic 数据模型：Finding、Evidence、StageResult | ⭐⭐⭐ |
| [state_machine.py](../../langgraph_langchain/state_machine.py) | ~270 | 分析状态机：阶段定义、流转规则、约束检查 | ⭐⭐⭐⭐ |

### 辅助模块

| 文件 | 职责 | 说明 |
|------|------|------|
| [tool_validators.py](../../langgraph_langchain/tool_validators.py) | 工具阶段权限校验 | 每个工具在哪些阶段可以调用 |
| [tracing.py](../../langgraph_langchain/tracing.py) | 分布式追踪 | 追踪每次工具调用的链路 |
| [structured_logging.py](../../langgraph_langchain/structured_logging.py) | 结构化日志 | 机器可读的日志格式 |
| [stability_metrics.py](../../langgraph_langchain/stability_metrics.py) | 稳定性指标 | 追踪成功率、失败率 |
| [recovery.py](../../langgraph_langchain/recovery.py) | 错误恢复 | 自动重试和降级策略 |
| [error_messages.py](../../langgraph_langchain/error_messages.py) | 用户友好错误 | 中文错误消息 |
| [evidence_binding.py](../../langgraph_langchain/evidence_binding.py) | 证据绑定验证 | 确保证据引用有效 |
| [evidence_validator.py](../../langgraph_langchain/evidence_validator.py) | 证据等级验证 | 验证因果语言与证据等级一致 |
| [recommendation_validator.py](../../langgraph_langchain/recommendation_validator.py) | 建议验证 | 确保建议与证据强度匹配 |
| [lineage_tracker.py](../../langgraph_langchain/lineage_tracker.py) | 数据血缘追踪 | 从发现→证据→图表的完整链路 |

### P1–P4 基础设施模块

| 文件 | 职责 | 阶段 |
|------|------|:---:|
| [prompts/prompt_builder.py](../../langgraph_langchain/prompts/prompt_builder.py) | 动态 Prompt 组装引擎 — 从 .md 段落组装系统提示词 | P1 |
| [prompts/sections/](../langgraph_langchain/prompts/sections/) | 8 个模块化 .md 段落 (identity, workflow, rules...) | P1 |
| [error_classifier.py](../../langgraph_langchain/error_classifier.py) | API 错误智能分类 — 11 种错误类型 + 恢复策略 | P2 |
| [retry_utils.py](../../langgraph_langchain/retry_utils.py) | 抖动指数退避 — 防止重试惊群 | P2 |
| [session_persistence.py](../../langgraph_langchain/session_persistence.py) | 会话状态序列化 — 断点续传 + 恢复 | P3 |
| [tools/tool_delegate.py](../../langgraph_langchain/tools/tool_delegate.py) | 子任务委派工具 — 并行化分析维度 | P4 |
| [tools/registry.py](../../langgraph_langchain/tools/registry.py) | 工具注册中心 — 工厂模式 + 自动发现 | 重构 |
| [tools/_shared.py](../langgraph_langchain/tools/_shared.py) | 工具共享辅助 — 路径安全、步骤验证 | 重构 |
| [skills_loader.py](../langgraph_langchain/skills_loader.py) | Skills 渐进式加载器 | 技能框架 |
| [skills/](../langgraph_langchain/skills/) | 4 个内置分析技能 (eda, trend, anomaly, attribution) | 技能框架 |

### R&D 效率领域模块

| 文件 | 职责 |
|------|------|
| [rd_efficiency_domain.py](../langgraph_langchain/rd_efficiency_domain.py) | R&D 指标定义（velocity, cycle time, defect rate） |
| [rd_metric_library.py](../langgraph_langchain/rd_metric_library.py) | 指标计算验证和解读辅助 |
| [rd_validators.py](../langgraph_langchain/rd_validators.py) | R&D 领域特定验证 |
| [rd_templates.py](../langgraph_langchain/rd_templates.py) | 常见 R&D 分析场景模板 |

### 前端

| 文件 | 职责 |
|------|------|
| [webui/app.py](../webui/app.py) | Streamlit 主界面 |
| [webui/config.py](../webui/config.py) | 前端配置（API 地址等） |

### 其他

| 文件 | 职责 |
|------|------|
| [Makefile](../Makefile) | 常用命令快捷方式 |
| [start_all.sh](../start_all.sh) | 同时启动前后端 |
| [.env](../.env) | 环境变量（API Key 等） |
| [requirements.txt](../requirements.txt) | Python 依赖 |

---

## 3.3 数据流详解

### 3.3.1 文件上传流程

```
Streamlit 前端                     FastAPI 后端
    │                                   │
    │  POST /workspace/upload           │
    │  (session_id, file)               │
    │──────────────────────────────────→│
    │                                   │ 1. 获取或创建会话
    │                                   │ 2. 创建 workspace/<session_id>/
    │                                   │ 3. 保存文件到 workspace
    │                                   │ 4. 更新 SESSIONS[session_id].files
    │                                   │ 5. 持久化到 .sessions.json
    │  ← 200 {session_id, file_path}    │
    │                                   │
```

### 3.3.2 分析请求流程

```
Streamlit 前端                     FastAPI 后端                    LangGraph Agent
    │                                   │                               │
    │  POST /v1/chat/completions        │                               │
    │  {session_id, message, stream:true}│                               │
    │──────────────────────────────────→│                               │
    │                                   │                               │
    │                                   │ 1. 验证会话                    │
    │                                   │ 2. 获取数据文件路径            │
    │                                   │ 3. 创建 cancel_event          │
    │                                   │ 4. 获取 Semaphore(3) 并发许可  │
    │                                   │                               │
    │                                   │  run_analysis_stream()         │
    │                                   │──────────────────────────────→│
    │                                   │                               │
    │                                   │                    5. 创建 _Session
    │                                   │                    6. 创建 tools
    │                                   │                    7. 创建 LLM
    │                                   │                    8. create_react_agent
    │                                   │                    9. astream_events
    │                                   │                               │
    │  SSE: data: {"content":"分析"}     │                               │
    │←──────────────────────────────────│  yield token                  │
    │  SSE: data: {"content":"中..."}   │←──────────────────────────────│
    │←──────────────────────────────────│                               │
    │  SSE: data: {"artifacts":[...]}   │                               │
    │←──────────────────────────────────│  yield artifacts              │
    │                                   │←──────────────────────────────│
    │  ...                              │                               │
    │  SSE: data: [DONE]                │                               │
    │←──────────────────────────────────│                               │
    │                                   │                               │
```

### 3.3.3 会话生命周期

```
创建会话                     使用会话                    清理会话
─────────                   ─────────                   ─────────
POST /workspace/upload      POST /v1/chat/completions   DELETE /sessions/{id}
        │                           │                          │
        ▼                           ▼                          ▼
 uuid4() 生成 session_id    验证 session 存在          删除 workspace 目录
 创建 workspace/<id>/      检查是否过期 (24h TTL)      从 SESSIONS 移除
 注册到 SESSIONS dict      获取数据文件               清理 _ACTIVE_CANCELS
 持久化到 .sessions.json   运行 Agent 分析             持久化更新
                            返回 SSE 流
```

---

## 3.4 关键设计决策

### 决策 1：为什么用闭包工厂而不是全局工具？

```python
# ❌ 全局工具 — 无法区分不同会话
@tool
def python_repl(code: str) -> str:
    # 无法访问特定会话的状态
    ...

# ✅ 闭包工厂 — 每个会话有自己的工具集
def _make_tools(session: _Session) -> list:
    @tool
    def python_repl(code: str) -> str:
        output = session.run_code(code)  # 通过闭包访问会话状态
        return output
    return [python_repl, ...]
```

**原因**：多个用户可能同时上传数据、运行分析。全局工具无法区分不同用户的数据和状态。

### 决策 2：为什么用 `_Session.ns` 而不是纯消息历史？

```python
# LangGraph 内置方式：所有数据通过消息传递
# AI: "df_north = df[df['region']=='华北']"  → 存在消息中
# AI: "print(df_north.shape)"                 → 需要重新执行

# 本项目方式：通过命名空间持久化
session.ns["df_north"] = df_north  # 直接存入 exec 命名空间
# 下次 python_repl 可以直接使用 df_north
```

**原因**：数据分析涉及大型 DataFrame 和复杂计算。通过消息传递数据不现实（太大、太慢）。使用 `exec()` 的命名空间让变量在工具调用间持久化。

### 决策 3：为什么限制 48 步而不是更多？

```python
_MAX_AGENT_STEPS = 48
```

**原因**：
- 每步都消耗 LLM tokens（成本）
- 超过 48 步通常意味着 Agent 陷入了循环或分析方向有误
- 用户体验：长时间等待（>5分钟）体验差

### 决策 4：为什么用 Semaphore(3) 限制并发？

```python
_MAX_CONCURRENT = 3
_agent_semaphore = asyncio.Semaphore(_MAX_CONCURRENT)
```

**原因**：
- 每个分析会话占用一个 LLM 连接和大量内存（DataFrame）
- 3 个并发是一个平衡点：既能服务多用户，又不会耗尽资源
- 可通过 `MAX_CONCURRENT_AGENTS` 环境变量调整

### 决策 5：为什么用 Tools Registry 而不是内联定义？

```python
# ❌ 旧方式 — 所有工具在 langgraph_agent.py 中内联定义（~2900 行）
def _make_tools(session):
    @tool
    def load_data(...): ...
    @tool
    def python_repl(...): ...
    # 9 个工具挤在一个函数中

# ✅ 新方式 — 每个工具独立文件 + Registry 自动发现
# tools/tool_load_data.py
registry.register(name="load_data", factory=_factory, ...)

# tools/__init__.py 自动发现所有工具模块
def get_tools_for_session(session):
    return registry.get_tools_for_session(session)
```

**原因**：`langgraph_agent.py` 增长到 2900+ 行难以维护。工具注册模式让每个工具独立成文件，支持 `check_fn` 条件注册，方便扩展新工具。

### 决策 6：为什么用模块化 Prompt Builder？

```python
# ❌ 旧方式 — 230 行硬编码字符串
_SYSTEM_PROMPT = """你是一个数据分析专家...（230行）"""

# ✅ 新方式 — 模块化 .md 段落动态组装
prompt_builder = PromptBuilder("prompts/sections/")
effective_prompt = prompt_builder.build_with_context(
    skills_index=skills_loader.build_skills_prompt(),
    memory_snapshot=memory_store.format_for_system_prompt(),
)
```

**原因**：
- 编辑分析规则只需修改 .md 文件，不需改 Python 代码
- 支持动态注入 Skills 索引和 Memory 快照
- 便于 A/B 测试不同的 Prompt 策略

---

## 3.5 环境变量配置

```bash
# .env 文件
DEEPSEEK_API_KEY=your_api_key_here      # LLM API 密钥
DEEPSEEK_MODEL_ID=deepseek-chat          # 模型名称
DEEPSEEK_API_BASE=https://api.deepseek.com/v1  # API 地址
MAX_CONCURRENT_AGENTS=3                  # 最大并发分析数
SESSION_TTL_HOURS=24                     # 会话过期时间（小时）
```

---

## 3.6 启动命令

```bash
# 启动后端（LangGraph Agent + FastAPI）
python -m langgraph_langchain.api_server_langgraph
# 监听 http://localhost:8888

# 启动前端（新终端）
streamlit run webui/app.py
# 打开 http://localhost:8501

# 或使用 Makefile
make dev  # 同时启动前后端
```

---

> **下一步**：阅读 [04-agent-graph.md](04-agent-graph.md) 深入学习 Agent 图的构建细节。

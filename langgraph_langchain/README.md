# LangGraph/LangChain 后端说明

本目录是当前项目的 **后端实现**。基于 LangGraph + LangChain 构建数据分析执行链路，前端通过 OpenAI 风格接口与 SSE 流式输出对接。

## 目录内核心文件

- `api_server_langgraph.py`：FastAPI 后端入口，提供 `/v1/chat/completions`、`/v1/files`、`/workspace/*`、`/sessions/*`、`/health`
- `langgraph_agent.py`：LangGraph ReAct Agent、工具定义、运行时约束、产物收集、流式事件整理
- `schemas.py`：Pydantic 数据结构
- `requirements-langgraph.txt`：LangGraph / LangChain 相关依赖

## 当前能力

### 分析能力

- 默认以专业中文输出数据分析报告
- 先加载数据、再自动 EDA、再做分步深挖、最后生成结构化报告
- 更强调：
  - 数据质量检查
  - 指标与维度定义
  - 分组对比
  - 时间趋势分析
  - 异常解释
  - 驱动 / 贡献分析
  - 最终综合结论

### 运行时 guardrails

`python_repl` 不是无限制执行，而是带有运行时校验：

- 拒绝空步骤
- 限制单步行数
- 要求每步打印结构化标记
- 中英双语 marker 均支持：
  - `step objective / method / key results / suggested next step`
  - `步骤目标 / 方法 / 关键结果 / 建议下一步`
- `finish_report` 只允许在最后阶段提交，并已加一次性保护

### 内置 helper

执行环境里已预置：

- `save_fig(filename)`
- `fix_chinese()`
- `profile_dimension(...)`
- `compare_segments(...)`
- `time_trend(...)`
- `detect_anomalies(...)`
- `explain_metric_change(...)`

## 启动方式

1. 安装依赖：

```bash
pip install -r requirements.txt
pip install -r langgraph_langchain/requirements-langgraph.txt
```

2. 配置 `.env`：

```bash
DEEPSEEK_API_KEY=your_api_key_here
DEEPSEEK_MODEL_ID=deepseek-chat
DEEPSEEK_API_BASE=https://api.deepseek.com/v1
MAX_CONCURRENT_AGENTS=3
```

3. 启动后端：

```bash
python -m langgraph_langchain.api_server_langgraph
```

默认监听 `0.0.0.0:8888`。

4. 启动前端：

```bash
streamlit run webui/app.py
```

前端默认读取 `webui/config.py` 中的：
- `API_BASE_URL=http://localhost:8888`

## 端到端状态

当前记录中的代表性 E2E 验证结果为：

- `grouped_sales`：通过
- `time_series_anomaly`：通过
- `quality_issues`：通过

关键优化结论：

- 模型经常输出中文步骤标记，因此 validator 已改为支持中英双语
- 结尾综合步骤过长会导致失败，因此单步上限与 prompt 都已针对这个问题调优
- 前端侧已补齐生成文件去重、换行展示保留、固定滚动输出区域和中文图表字体支持

## 与旧实现的关系

旧版 `smolagents/` 实现已移除。当前所有开发与优化基于本目录继续推进。

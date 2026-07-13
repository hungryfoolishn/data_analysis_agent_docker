# DeepAnalyze 数据分析 Agent

DeepAnalyze 是一个基于 **LangGraph + LangChain** 的数据分析 Agent 系统，前端为 `webui/app.py` 的 Streamlit 页面。

## 主要特性

- **LangGraph ReAct 数据分析 Agent**：基于 `create_react_agent` 执行分析流程
- **分析师式数据分析流程**：先计划、再 EDA、再分步深入分析、最后生成结构化报告
- **运行时 guardrails**：`python_repl` 强制小步执行、限制单步体量、要求中英双语步骤标记
- **实时流式输出**：SSE 持续返回分析过程，前端实时展示
- **会话级工作空间**：上传文件、分析产物、日志按 `session_id` 隔离
- **自动产物追踪**：图表、报告等文件自动加入下载列表
- **中文分析与中文图表支持**：报告默认中文输出，matplotlib 已补齐中文字体处理
- **前端体验优化**：生成文件去重、流式文本换行保留、输出区固定滚动

## 当前推荐架构

```text
用户 / WebUI (Streamlit)
        ↓
langgraph_langchain/api_server_langgraph.py
        ↓
langgraph_langchain/langgraph_agent.py
        ↓
工具: load_data / eda_profile / python_repl / finish_report
        ↓
workspace/<session_id>/ 中的图表、日志、报告等产物
```

## 快速开始

### 1. 环境准备

**使用 Conda（推荐）**

```bash
conda env create -f environment.yml
conda activate smolagents
```

**使用 pip**

```bash
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
pip install -r langgraph_langchain/requirements-langgraph.txt
```

### 2. 配置 API Key

```bash
cp .env.example .env
# 编辑 .env，添加 DEEPSEEK_API_KEY
```

### 3. 启动服务

**推荐：启动当前 LangGraph 后端**

```bash
python -m langgraph_langchain.api_server_langgraph
```

默认监听：`http://localhost:8888`

**启动前端（新终端）**

```bash
streamlit run webui/app.py
```

默认访问：`http://localhost:8501`

## 使用流程

1. 打开浏览器访问 `http://localhost:8501`
2. 上传数据文件（CSV、Excel 等）
3. 输入分析需求
4. 前端通过 `/v1/chat/completions` 接收流式结果
5. 从页面下载生成的图表、报告等产物

## 当前后端优化记录

### 分析能力与稳定性

- 强化系统提示词，要求先做分析计划，再推进数据质量、指标定义、分组对比、趋势、异常解释、驱动分析、最终综合
- `eda_profile()` 补充 `Analysis Signals`，更偏业务分析视角
- `_Session.ns` 预置高价值 helper：分组画像、分群对比、时间趋势、异常检测、变化解释
- `finish_report()` 要求结构化章节、证据化结论、图表说明、中文专业表达和不确定性标注
- `finish_report` 已加一次性保护，避免重复调用

### 运行时 guardrails

- `python_repl` 必须按小步执行
- 拒绝空步骤
- 限制单步体量
- 步骤标记支持中英双语：
  - `step objective / method / key results / suggested next step`
  - `步骤目标 / 方法 / 关键结果 / 建议下一步`
- 单步上限已调到 50 行，降低结尾大脚本失败概率

### 前端可见体验优化

- 生成文件下载去重
- 实时输出限制在固定滚动区域
- 流式文本保留换行结构
- matplotlib 图表中文字体渲染修复

### E2E 结论

真实模型配置下，3 类代表性数据集已验证通过：

- `grouped_sales`
- `time_series_anomaly`
- `quality_issues`

## API 示例

```bash
# 上传文件
curl -X POST http://localhost:8888/workspace/upload \
  -F "file=@data.xlsx"

# 流式分析
curl -X POST http://localhost:8888/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "deepseek-chat",
    "messages": [{"role": "user", "content": "分析销售趋势并解释异常波动"}],
    "stream": true
  }'
```

## 项目结构

```text
data_analysis_agent/
├── langgraph_langchain/          # LangGraph 后端实现
│   ├── api_server_langgraph.py   # FastAPI + SSE 后端入口
│   ├── langgraph_agent.py        # LangGraph Agent 与工具实现
│   ├── schemas.py                # Pydantic 数据结构
│   └── README.md
├── webui/                        # Streamlit 前端
│   ├── app.py
│   └── config.py
├── workspace/                    # 会话工作空间与分析产物
├── API_README.md
├── README.md
└── requirements.txt
```

## 常用命令

```bash
# 当前推荐后端
python -m langgraph_langchain.api_server_langgraph

# 前端
streamlit run webui/app.py
```

## 配置

`.env` 常用变量：

```bash
DEEPSEEK_API_KEY=your_api_key_here
DEEPSEEK_MODEL_ID=deepseek-chat
DEEPSEEK_API_BASE=https://api.deepseek.com/v1
MAX_CONCURRENT_AGENTS=3
```

前端相关环境变量见 `webui/config.py`，默认包括：

- `API_BASE_URL=http://localhost:8888`
- `FILE_SERVER_BASE=http://116.148.124.7:8888`
- `DEFAULT_MODEL=deepseek-chat`

## 文档

- [API_README.md](API_README.md) — 当前 API 接口说明
- [langgraph_langchain/README.md](langgraph_langchain/README.md) — LangGraph 后端说明
- [.claude/langgraph_analysis_guardrails_and_e2e_tuning.md](.claude/langgraph_analysis_guardrails_and_e2e_tuning.md) — 本轮优化记录

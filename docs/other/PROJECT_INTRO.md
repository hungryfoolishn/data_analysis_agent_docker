# 项目简介

本仓库实现了一个面向可解释数据分析的智能代理系统（DeepAnalyze）。项目目标是通过编排自动化工具链，为非专业用户和数据工程师提供可重复、可审计的数据分析流程与报告。

核心特点
- 基于 LangGraph + LangChain 的单后端实现，`langgraph_langchain/` 为主实现。
- 前后端分离：
  - FastAPI 后端（OpenAI 兼容 API 接口），负责会话管理与代理调度。
  - Streamlit 前端（`webui/`），提供交互式上传与分析展示。
- 会话隔离：每次分析在独立 `workspace/<session_id>/` 下生成产物（报告、日志、图表）。
- Python 执行护栏（guardrails）：限制单步代码行数、执行步数和错误次数，保证安全与可控性。

关键组件
- `langgraph_langchain/`：主实现，包含 FastAPI 服务、LangGraph 代理逻辑、工具集合与运行时策略。
- `webui/`：Streamlit 前端，用户上传数据并查看 SSE 流式结果。
- `workspace/`：会话工作区，包含示例会话与测试数据。

快速开始（本地开发）

1. 使用 Conda（推荐）：

```bash
conda env create -f environment.yml
conda activate smolagents
```

2. 或者使用 venv + pip：

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pip install -r langgraph_langchain/requirements-langgraph.txt
```

3. 启动后端与前端：

```bash
# 启动后端（LangGraph 实现）
python -m langgraph_langchain.api_server_langgraph

# 在另一个终端启动 Streamlit 前端
streamlit run webui/app.py
```

快捷命令（Makefile）：

```bash
make server    # 启动后端
make frontend  # 启动前端
make dev       # 一键开发（同时启动）
```

测试

```bash
pytest
# 或运行特定测试
pytest tests/test_validator_e2e.py
```

配置与环境变量
- 可在 `.env` 中配置 API 基地址、模型 ID、会话 TTL 等（参见仓库根目录说明）。

贡献与联系
- 请阅读 `README.md` 与 `langgraph_langchain/README.md` 获取更多实现细节与开发指南。
- 如需帮助，可在仓库提交 issue 或联系维护者（见项目 README）。

许可
- 本仓库遵循项目根目录中的许可说明（若有）。

---

如需我把文档翻译成更详细的中文使用手册或生成摘要版 README，告诉我你希望的侧重点（部署/开发/运维/示例）。

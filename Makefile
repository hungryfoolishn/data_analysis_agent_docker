.PHONY: help setup install clean server frontend test evaluation-gate

help:
	@echo "DeepAnalyze 数据分析 Agent - 可用命令"
	@echo ""
	@echo "环境设置:"
	@echo "  make setup      - 创建 conda 环境"
	@echo "  make verify     - 验证环境配置"
	@echo "  make install    - 安装依赖（使用 pip）"
	@echo ""
	@echo "运行服务:"
	@echo "  make server     - 启动 API 服务器"
	@echo "  make frontend   - 启动 Web 前端"
	@echo "  make dev        - 同时启动服务器和前端（一键启动）"
	@echo ""
	@echo "测试:"
	@echo "  make test       - 运行 LangGraph 可靠性测试"
	@echo "  make test-api   - 测试 API 端点"
	@echo ""
	@echo "清理:"
	@echo "  make clean      - 清理临时文件和缓存"
	@echo "  make clean-all  - 清理所有（包括 workspace）"

setup:
	@echo "创建 conda 环境..."
	./setup_conda_env.sh

verify:
	@echo "验证环境配置..."
	./verify_env.sh

install:
	@echo "安装依赖..."
	pip install -r requirements.txt

server:
	@echo "启动 API 服务器..."
	python -m langgraph_langchain.api_server_langgraph

frontend:
	@echo "启动 Web 前端..."
	streamlit run webui/app.py

dev:
	@echo "一键启动所有服务..."
	./start_all.sh

test:
	@echo "运行 LangGraph 可靠性测试..."
	python -m pytest langgraph_langchain/test_reliability.py

evaluation-gate:
	@test -n "$(CURRENT)" || (echo "CURRENT=<evaluation-summary.json> is required" && exit 2)
	python -m langgraph_langchain.evaluation.cli $(CURRENT) $(if $(BASELINE),--baseline $(BASELINE),) $(if $(POLICY),--policy $(POLICY),)

test-api:
	@echo "测试 API 端点..."
	@echo "1. 健康检查:"
	curl -s http://localhost:8888/health | python -m json.tool
	@echo ""
	@echo "2. 列出工作空间文件:"
	curl -s http://localhost:8888/workspace/files | python -m json.tool

clean:
	@echo "清理临时文件..."
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	find . -type f -name "*.pyo" -delete 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	@echo "清理完成"

clean-all: clean
	@echo "清理工作空间..."
	rm -rf workspace/*
	@echo "清理完成"

.DEFAULT_GOAL := help

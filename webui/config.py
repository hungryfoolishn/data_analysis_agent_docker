"""
Gradio前端配置文件
"""

import os

# API配置
API_BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8888")
# 文件服务（用于生成 workspace/files 的可访问链接）
# 可通过环境变量覆盖：FILE_SERVER_BASE
FILE_SERVER_BASE = os.environ.get("FILE_SERVER_BASE", "http://116.148.124.7:8888")
DEFAULT_MODEL = os.environ.get("DEFAULT_MODEL", "deepseek-chat")

# Gradio配置
GRADIO_SERVER_NAME = os.environ.get("GRADIO_SERVER_NAME", "0.0.0.0")
GRADIO_SERVER_PORT = int(os.environ.get("GRADIO_SERVER_PORT", "7860"))
GRADIO_SHARE = os.environ.get("GRADIO_SHARE", "False").lower() == "true"

# 快捷指令
QUICK_INSTRUCTIONS = {
    "数据概览": "请对上传的数据文件进行概览分析，包括数据的基本统计信息、数据质量检查、缺失值分析等。",
    "趋势分析": "请分析数据中的趋势和模式，识别关键变化点和异常值，并提供可视化图表。",
    "生成报告": "请生成一份完整的数据分析报告，包括数据概览、关键发现、可视化图表和结论建议。",
    "对话分析": "请以对话的方式逐步分析数据，展示分析思路和推理过程。",
}

# 允许的文件类型
ALLOWED_FILE_TYPES = [".csv", ".xlsx", ".xls", ".json", ".txt", ".md", ".pdf", ".png", ".jpg", ".jpeg"]

# UI配置
UI_CONFIG = {
    "PAGE_TITLE": "DeepAnalyze 数据分析平台",
    "PAGE_ICON": "📊",
    "LAYOUT": "wide",
}


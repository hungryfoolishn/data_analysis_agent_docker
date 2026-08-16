#!/usr/bin/env python3
"""
DeepAnalyze Streamlit Frontend
基于 Streamlit 构建的数据分析平台界面
实现与 Gradio 版本相同的功能
"""

import re
import streamlit as st
import requests
import json
import os
import time
import uuid
from typing import List, Dict, Any, Optional, Tuple
from pathlib import Path
from config import (
    API_BASE_URL, FILE_SERVER_BASE, DEFAULT_MODEL,
    QUICK_INSTRUCTIONS, ALLOWED_FILE_TYPES,
    GRADIO_SERVER_NAME, GRADIO_SERVER_PORT, GRADIO_SHARE
)
from workbench import (
    apply_analysis_event,
    apply_runtime_snapshot,
    empty_workbench_state,
    render_artifacts_html,
    render_assets_html,
    render_executions_html,
    render_run_summary_html,
    render_steps_html,
)


# API端点
ENDPOINTS = {
    "CHAT_COMPLETIONS": f"{API_BASE_URL}/v1/chat/completions",
    "FILES": f"{API_BASE_URL}/v1/files",
    "WORKSPACE_FILES": f"{API_BASE_URL}/workspace/files",
    "WORKSPACE_UPLOAD": f"{API_BASE_URL}/workspace/upload",
}

# 初始化 session state
if 'uploaded_files' not in st.session_state:
    st.session_state.uploaded_files = []
if 'file_ids' not in st.session_state:
    st.session_state.file_ids = []
if 'session_id' not in st.session_state:
    st.session_state.session_id = f"session_{int(time.time())}_{uuid.uuid4().hex[:8]}"
if 'is_analyzing' not in st.session_state:
    st.session_state.is_analyzing = False
if 'generated_files' not in st.session_state:
    st.session_state.generated_files = []
if 'analysis_result' not in st.session_state:
    st.session_state.analysis_result = ""
if 'instruction_input' not in st.session_state:
    st.session_state.instruction_input = ""
if 'workbench_state' not in st.session_state:
    st.session_state.workbench_state = empty_workbench_state()
if 'workbench_fingerprint' not in st.session_state:
    st.session_state.workbench_fingerprint = None


def format_file_size(size_bytes: int) -> str:
    """格式化文件大小"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.2f} MB"


def upload_file_to_backend(file_path: str) -> Optional[Dict[str, str]]:
    """上传文件到后端，返回文件信息"""
    try:
        with open(file_path, "rb") as f:
            files = {"file": (os.path.basename(file_path), f, "application/octet-stream")}
            data = {"session_id": st.session_state.session_id}
            response = requests.post(ENDPOINTS["WORKSPACE_UPLOAD"], files=files, data=data, timeout=30)

        if response.status_code == 200:
            result = response.json()
            return {
                "filename": result.get("filename"),
                "file_path": result.get("file_path"),
                "session_id": result.get("session_id")
            }
        else:
            st.error(f"文件上传失败: {response.status_code} - {response.text}")
            return None
    except Exception as e:
        st.error(f"上传文件时出错: {str(e)}")
        return None

def _artifact_key(file_info: Dict[str, Any]) -> str:
    """Return a stable dedupe key for generated files."""
    return (
        str(file_info.get("relative_path") or "")
        or str(file_info.get("path") or "")
        or str(file_info.get("url") or "")
        or str(file_info.get("name") or "")
    )


def dedupe_generated_files(files: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Deduplicate generated files while preserving order."""
    seen = set()
    deduped: List[Dict[str, Any]] = []
    for file_info in files:
        key = _artifact_key(file_info)
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(file_info)
    return deduped


def fetch_runtime_snapshot() -> Optional[Dict[str, Any]]:
    """Fetch authoritative execution details for the current workbench."""
    try:
        response = requests.get(
            f"{API_BASE_URL}/sessions/{st.session_state.session_id}/runtime",
            timeout=5,
        )
        if response.status_code == 200:
            return response.json()
    except Exception:
        pass
    return None


def update_workbench_from_chunk(chunk: Dict[str, Any]) -> bool:
    """Apply an SSE event and refresh details only on lifecycle transitions."""
    event = chunk.get("analysis_event") or {}
    artifacts = chunk.get("generated_files") or []
    if not event and not artifacts:
        return False
    step = event.get("step") or {}
    fingerprint = (
        event.get("run_id"),
        event.get("run_status"),
        step.get("step_id"),
        step.get("status"),
        tuple(event.get("artifact_ids") or []),
    )
    st.session_state.workbench_state = apply_analysis_event(
        st.session_state.workbench_state,
        event,
        artifacts,
    )
    changed = fingerprint != st.session_state.workbench_fingerprint
    if changed:
        st.session_state.workbench_fingerprint = fingerprint
        snapshot = fetch_runtime_snapshot()
        if snapshot:
            st.session_state.workbench_state = apply_runtime_snapshot(
                st.session_state.workbench_state, snapshot
            )
    return changed


def render_workbench_views(
    summary_placeholder,
    steps_placeholder,
    artifacts_placeholder,
    executions_placeholder,
    assets_placeholder,
) -> None:
    state = st.session_state.workbench_state
    summary_placeholder.markdown(render_run_summary_html(state), unsafe_allow_html=True)
    steps_placeholder.markdown(render_steps_html(state.get("steps", [])), unsafe_allow_html=True)
    artifacts = state.get("artifacts", []) or st.session_state.generated_files
    artifacts_placeholder.markdown(
        render_artifacts_html(artifacts, FILE_SERVER_BASE), unsafe_allow_html=True
    )
    executions_placeholder.markdown(
        render_executions_html(state.get("executions", [])), unsafe_allow_html=True
    )
    assets_placeholder.markdown(
        render_assets_html(state.get("assets", [])), unsafe_allow_html=True
    )


def start_analysis_stream(instruction: str):
    """开始分析（流式响应）生成器"""
    if not instruction.strip():
        yield "❌ 请输入分析指令"
        return

    if st.session_state.is_analyzing:
        yield "⚠️ 分析正在进行中，请稍候..."
        return

    st.session_state.is_analyzing = True
    st.session_state.analysis_result = ""
    st.session_state.generated_files = []
    st.session_state.workbench_state = empty_workbench_state()
    st.session_state.workbench_fingerprint = None

    accumulated_content = ""
    generated_files: List[Dict[str, Any]] = []

    try:
        # 准备消息
        messages = [{
            "role": "user",
            "content": instruction
        }]

        # 调用API（启用流式响应）
        payload = {
            "model": DEFAULT_MODEL,
            "messages": messages,
            "temperature": 0.4,
            "stream": True,
            "session_id": st.session_state.session_id
        }

        # 如果有上传的文件，添加 file_path 参数
        if st.session_state.uploaded_files:
            latest_file = st.session_state.uploaded_files[-1]
            if 'file_path' in latest_file:
                payload["file_path"] = latest_file['file_path']

        response = requests.post(
            ENDPOINTS["CHAT_COMPLETIONS"],
            json=payload,
            headers={"Content-Type": "application/json"},
            stream=True,
            timeout=600
        )

        if response.status_code != 200:
            error_msg = f"分析请求失败: {response.status_code}"
            error_detail = response.text[:200] if response.text else ""
            yield f"❌ {error_msg}\n\n详情: {error_detail}"
            return

        # 处理流式响应（Server-Sent Events格式）
        event_count = 0
        for line in response.iter_lines(decode_unicode=True):
            # 检查是否被停止
            if not st.session_state.is_analyzing:
                yield accumulated_content + "\n\n⏹️ 分析已停止"
                return

            if not line or not line.strip():
                continue

            # 处理SSE格式：data: {...}
            if line.startswith('data: '):
                data_str = line[6:]
                if data_str.strip() == '[DONE]':
                    break

                # 再次检查是否被停止
                if not st.session_state.is_analyzing:
                    yield accumulated_content + "\n\n⏹️ 分析已停止"
                    return

                try:
                    chunk = json.loads(data_str)
                    workbench_changed = update_workbench_from_chunk(chunk)
                    content_emitted = False

                    # 提取内容增量
                    if 'choices' in chunk and chunk['choices']:
                        delta = chunk['choices'][0].get('delta', {})
                        if 'content' in delta:
                            event_count += 1
                            content_delta = delta['content']
                            accumulated_content += content_delta
                            st.session_state.analysis_result = accumulated_content

                            print(f"[前端] 收到事件 #{event_count}, 内容长度: {len(content_delta)}, 累积长度: {len(accumulated_content)}")
                            yield accumulated_content
                            content_emitted = True

                    # 提取生成的文件
                    if 'generated_files' in chunk:
                        generated_files.extend(chunk['generated_files'] or [])
                        generated_files = dedupe_generated_files(generated_files)
                        st.session_state.generated_files = generated_files
                    elif chunk.get('choices') and len(chunk['choices']) > 0:
                        choice = chunk['choices'][0]
                        if 'delta' in choice and 'files' in choice['delta']:
                            files = choice['delta']['files']
                            if files:
                                generated_files.extend(files)
                                generated_files = dedupe_generated_files(generated_files)
                                st.session_state.generated_files = generated_files

                    # 检查是否完成
                    finish_reason = chunk.get('choices', [{}])[0].get('finish_reason')
                    if finish_reason == 'stop':
                        break
                    if workbench_changed and not content_emitted:
                        yield accumulated_content

                except json.JSONDecodeError:
                    continue
                except Exception as e:
                    print(f"处理chunk时出错: {e}")
                    continue

        # 最终更新
        st.session_state.analysis_result = accumulated_content
        st.session_state.generated_files = dedupe_generated_files(generated_files)

        if not accumulated_content or not accumulated_content.strip():
            accumulated_content = "⚠️ 分析完成，但内容为空。"

        yield accumulated_content

    except requests.exceptions.Timeout:
        yield "❌ 请求超时，请稍后重试"
    except requests.exceptions.ConnectionError:
        yield f"❌ 无法连接到API服务器\n\n请检查:\n1. API服务是否运行在 {API_BASE_URL}\n2. 网络连接是否正常"
    except Exception as e:
        error_msg = f"分析过程中出错: {str(e)}"
        import traceback
        traceback.print_exc()
        yield f"❌ {error_msg}"
    finally:
        st.session_state.is_analyzing = False


def check_resumable() -> Optional[Dict[str, Any]]:
    """Check if current session has resumable state on the backend."""
    try:
        resp = requests.get(
            f"{API_BASE_URL}/sessions/{st.session_state.session_id}/resumable",
            timeout=5,
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("resumable"):
                return data
    except Exception:
        pass
    return None


def start_resume_stream():
    """Resume an interrupted analysis (streaming SSE)."""
    if st.session_state.is_analyzing:
        yield "⚠️ 分析正在进行中，请稍候..."
        return

    st.session_state.is_analyzing = True
    st.session_state.analysis_result = ""
    accumulated_content = ""
    generated_files: List[Dict[str, Any]] = list(st.session_state.generated_files)

    try:
        response = requests.post(
            f"{API_BASE_URL}/sessions/{st.session_state.session_id}/resume",
            headers={"Content-Type": "application/json"},
            stream=True,
            timeout=600,
        )

        if response.status_code == 404:
            yield "❌ 没有可恢复的分析状态。请开始新的分析。"
            return
        if response.status_code != 200:
            error_detail = response.text[:300] if response.text else ""
            yield f"❌ 恢复失败 ({response.status_code})\n\n{error_detail}"
            return

        for line in response.iter_lines(decode_unicode=True):
            if not st.session_state.is_analyzing:
                yield accumulated_content + "\n\n⏹️ 分析已停止"
                return

            if not line or not line.strip():
                continue

            if line.startswith('data: '):
                data_str = line[6:]
                if data_str.strip() == '[DONE]':
                    break

                try:
                    chunk = json.loads(data_str)
                    workbench_changed = update_workbench_from_chunk(chunk)
                    content_emitted = False

                    # Error responses
                    if 'error' in chunk:
                        err = chunk['error']
                        yield f"❌ 恢复分析出错: {err.get('message', str(err))}"
                        return

                    if 'choices' in chunk and chunk['choices']:
                        delta = chunk['choices'][0].get('delta', {})
                        if 'content' in delta:
                            accumulated_content += delta['content']
                            st.session_state.analysis_result = accumulated_content
                            yield accumulated_content
                            content_emitted = True

                    if 'generated_files' in chunk:
                        generated_files.extend(chunk['generated_files'] or [])
                        generated_files = dedupe_generated_files(generated_files)
                        st.session_state.generated_files = generated_files

                    finish_reason = chunk.get('choices', [{}])[0].get('finish_reason')
                    if finish_reason == 'stop':
                        break
                    if workbench_changed and not content_emitted:
                        yield accumulated_content

                except json.JSONDecodeError:
                    continue

        st.session_state.analysis_result = accumulated_content
        st.session_state.generated_files = dedupe_generated_files(generated_files)
        yield accumulated_content or "⚠️ 恢复完成，但无输出。"

    except requests.exceptions.ConnectionError:
        yield f"❌ 无法连接到API服务器 ({API_BASE_URL})"
    except Exception as e:
        yield f"❌ 恢复分析出错: {str(e)}"
    finally:
        st.session_state.is_analyzing = False


def _is_markdown_line(line: str) -> bool:
    stripped = line.lstrip()
    if not stripped:
        return False
    markdown_prefixes = (
        "#", ">", "- ", "* ", "+ ", "```", "---", "***", "___", "|"
    )
    if stripped.startswith(markdown_prefixes):
        return True
    if re.match(r"^\d+\.\s", stripped):
        return True
    return False


def format_result_html(content: str) -> str:
    """Render mixed markdown/plain-text output while preserving plain-text newlines."""
    if not content:
        return ""

    lines = content.splitlines()
    parts: List[str] = []
    buffer: List[str] = []
    in_fence = False

    def flush_buffer() -> None:
        nonlocal buffer
        if not buffer:
            return
        block = "\n".join(buffer).strip("\n")
        if block:
            parts.append(f"```\n{block}\n```")
        buffer = []

    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("```"):
            flush_buffer()
            parts.append(line)
            in_fence = not in_fence
            continue

        if in_fence or _is_markdown_line(line):
            flush_buffer()
            parts.append(line)
        else:
            buffer.append(line)

    flush_buffer()
    return "\n".join(parts)


def generate_download_html(generated_files: List[Dict[str, str]]) -> str:
    """生成文件下载HTML"""
    unique_files = dedupe_generated_files(generated_files)
    if not unique_files:
        return '<div style="color: #9ca3af; font-size: 14px;">暂无生成的文件</div>'

    download_items = []
    for file_info in unique_files:
        file_name = file_info.get("name", "未知文件")
        file_url = file_info.get("url", "")

        if file_url:
            if not file_url.startswith(('http://', 'https://')):
                if not file_url.startswith('/'):
                    file_url = '/' + file_url
                if FILE_SERVER_BASE:
                    file_url = f"{FILE_SERVER_BASE}{file_url}"
            elif FILE_SERVER_BASE:
                from urllib.parse import urlparse
                parsed = urlparse(file_url)
                file_url = f"{FILE_SERVER_BASE}{parsed.path}"
            else:
                from urllib.parse import urlparse
                parsed = urlparse(file_url)
                file_url = parsed.path if parsed.path else file_url

        download_items.append(
            f'<div style="margin-bottom: 8px;">'
            f'<a href="{file_url}" target="_blank" '
            f'class="download-link" '
            f'style="background: #e0f2fe; color: #0369a1; border: 1px solid #bae6fd; '
            f'border-radius: 8px; padding: 10px 16px; font-size: 14px; '
            f'text-decoration: none; display: inline-flex; align-items: center; gap: 6px;">'
            f'📄 {file_name}'
            f'</a>'
            f'</div>'
        )

    return "".join(download_items) if download_items else '<div style="color: #9ca3af; font-size: 14px;">暂无生成的文件</div>'


def set_quick_instruction(text: str):
    """Set quick instruction via callback before widget re-render."""
    st.session_state.instruction_input = text
    st.session_state.instruction = text


def main():
    """主函数"""
    # 页面配置
    st.set_page_config(
        page_title="DeepAnalyze 数据分析平台",
        page_icon="📊",
        layout="wide",
        initial_sidebar_state="collapsed",
        menu_items=None  # 移除默认菜单
    )

    # 自定义CSS样式 - 匹配 Gradio 版本
    st.markdown("""
    <style>
    /* 隐藏 Streamlit 默认元素 */
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}

    /* 全局样式 */
    .stApp {
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
        background: #ffffff;
    }

    /* 减少顶部空白 */
    .stApp > header {
        display: none !important;
    }

    .main .block-container {
        padding-top: 0.5rem !important;
        padding-bottom: 1rem !important;
        max-width: 100%;
    }

    /* 移除标题上方的额外空白 */
    h1 {
        margin-top: 0 !important;
        padding-top: 0 !important;
        margin-bottom: 1rem !important;
    }

    /* 区域标题样式 */
    h3 {
        font-size: 16px;
        font-weight: 600;
        color: #1f2937;
        margin-bottom: 12px;
        margin-top: 1rem;
    }

    /* 按钮样式 */
    .stButton>button {
        width: 100%;
        border-radius: 6px;
        font-weight: 500;
        transition: all 0.2s;
    }

    /* 主操作 */
    .stButton>button[kind="primary"] {
        background: #087f5b !important;
        color: white !important;
        border: none !important;
    }

    .stButton>button[kind="primary"]:hover {
        background: #066b4d !important;
    }

    /* 快捷指令按钮样式 */
    .stButton>button:not([kind="primary"]) {
        background: #f3f4f6;
        color: #374151;
        border: 1px solid #d1d5db;
    }

    .stButton>button:not([kind="primary"]):hover {
        background: #e5e7eb;
        border-color: #9ca3af;
    }

    /* 结果区域样式 */
    .result-container {
        background: #ffffff;
        border: 1px solid #e5e7eb;
        border-radius: 8px;
        padding: 20px;
        min-height: 300px;
        max-height: 500px;
        overflow-y: auto;
        white-space: pre-wrap;
        font-family: monospace;
        font-size: 14px;
        color: #1f2937;
    }

    .analysis-output {
        background: #ffffff;
        border: 1px solid #e5e7eb;
        border-radius: 8px;
        padding: 16px;
        min-height: 468px;
        max-height: 468px;
        overflow-y: auto;
        white-space: pre-wrap;
        overflow-wrap: anywhere;
        word-break: break-word;
        font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', monospace;
        font-size: 14px;
        line-height: 1.6;
        color: #1f2937;
        box-sizing: border-box;
    }

    .file-item {
        display: flex;
        align-items: center;
        gap: 8px;
        padding: 8px 12px;
        background: #ffffff;
        border: 1px solid #e5e7eb;
        border-radius: 6px;
        margin-bottom: 8px;
    }

    /* 文件上传区域 */
    .uploadedFile {
        background: #ffffff;
        border: 1px solid #e5e7eb;
        border-radius: 6px;
        padding: 8px 12px;
        margin-bottom: 8px;
    }

    /* 下载链接样式 */
    .download-link {
        background: #e0f2fe;
        color: #0369a1;
        border: 1px solid #bae6fd;
        border-radius: 8px;
        padding: 10px 16px;
        font-size: 14px;
        text-decoration: none;
        display: inline-flex;
        align-items: center;
        gap: 6px;
        margin-bottom: 8px;
    }

    .download-link:hover {
        background: #bae6fd;
    }

    /* 页脚 - 固定定位在右下角 */
    .streamlit-footer {
        position: fixed;
        bottom: 0;
        right: 0;
        padding: 12px 20px;
        background: transparent;
        font-size: 12px;
        color: #9ca3af;
        z-index: 999;
    }

    /* 滚动条样式 */
    ::-webkit-scrollbar {
        width: 8px;
        height: 8px;
    }

    ::-webkit-scrollbar-track {
        background: #f1f1f1;
    }

    ::-webkit-scrollbar-thumb {
        background: #cbd5e1;
        border-radius: 4px;
    }

    ::-webkit-scrollbar-thumb:hover {
        background: #94a3b8;
    }

    /* 确保页面内容不被底部遮挡 */
    .main .block-container {
        padding-bottom: 60px;
    }

    /* 清空按钮居中样式 */
    .clear-button-container {
        display: flex;
        justify-content: center;
        margin-top: 12px;
        margin-bottom: 12px;
    }

    .clear-button-container .stButton {
        width: auto;
        min-width: 120px;
    }

    /* 分析工作台 */
    .run-summary {
        display: grid;
        grid-template-columns: repeat(5, minmax(76px, 0.65fr)) minmax(180px, 1.9fr);
        border-top: 1px solid #dfe3e8;
        border-bottom: 1px solid #dfe3e8;
        background: #f7f9fa;
        margin: 4px 0 14px;
    }

    .run-summary > div {
        min-width: 0;
        padding: 10px 12px;
        border-right: 1px solid #e5e7eb;
    }

    .run-summary > div:last-child { border-right: 0; }
    .summary-label { display: block; color: #667085; font-size: 11px; margin-bottom: 3px; }
    .run-summary strong { color: #17202a; font-size: 14px; }
    .run-id code { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

    .step-list, .artifact-list, .execution-list, .asset-list { width: 100%; }
    .step-row {
        display: grid;
        grid-template-columns: 28px minmax(0, 1fr);
        gap: 10px;
        padding: 11px 4px;
        border-bottom: 1px solid #e8ebee;
    }
    .step-index {
        width: 24px; height: 24px; display: grid; place-items: center;
        border-radius: 50%; background: #e9ecef; color: #495057; font-size: 12px;
    }
    .step-running .step-index { background: #dbeafe; color: #1d4ed8; }
    .step-succeeded .step-index { background: #d3f9d8; color: #087f5b; }
    .step-needs_revision .step-index { background: #fff3bf; color: #946200; }
    .step-failed .step-index { background: #ffe3e3; color: #c92a2a; }
    .step-title { color: #20262d; font-size: 14px; font-weight: 600; overflow-wrap: anywhere; }
    .step-meta { display: flex; gap: 10px; align-items: center; color: #667085; font-size: 12px; margin-top: 3px; }
    .step-error, .execution-error { color: #b42318; font-size: 12px; margin-top: 7px; overflow-wrap: anywhere; }
    .step-needs_revision .step-error, .execution-feedback { color: #946200; font-size: 12px; margin-top: 7px; overflow-wrap: anywhere; }

    .artifact-row {
        display: flex; align-items: center; justify-content: space-between; gap: 12px;
        padding: 10px 4px; border-bottom: 1px solid #e8ebee;
    }
    .artifact-name, .asset-name { color: #20262d; font-size: 14px; font-weight: 600; overflow-wrap: anywhere; }
    .artifact-meta, .asset-meta, .asset-fields { color: #667085; font-size: 12px; margin-top: 2px; }
    .artifact-row a { color: #0369a1; font-size: 13px; text-decoration: none; white-space: nowrap; }
    .asset-row { padding: 9px 0; border-bottom: 1px solid #e8ebee; }

    .execution-row { border-bottom: 1px solid #e8ebee; padding: 5px 0; }
    .execution-row summary { display: grid; grid-template-columns: 1fr auto auto; gap: 12px; align-items: center; cursor: pointer; padding: 7px 4px; font-size: 12px; color: #667085; }
    .execution-detail { padding: 0 4px 10px; }
    .execution-detail pre { max-height: 240px; overflow: auto; background: #f6f8fa; border: 1px solid #e5e7eb; border-radius: 4px; padding: 10px; white-space: pre-wrap; overflow-wrap: anywhere; font-size: 12px; }
    .execution-output { color: #475467; font-size: 12px; font-weight: 600; margin-top: 8px; }
    .empty-state { color: #7b8490; font-size: 13px; padding: 18px 4px; border-bottom: 1px solid #e8ebee; }
    .empty-state.compact { padding: 8px 0; border-bottom: 0; }

    @media (max-width: 900px) {
        .main .block-container { padding-left: 0.8rem; padding-right: 0.8rem; }
        .run-summary { grid-template-columns: repeat(2, 1fr); }
        .run-summary .run-id { grid-column: 1 / -1; border-top: 1px solid #e5e7eb; }
        .analysis-output { min-height: 360px; max-height: 360px; }
    }
    </style>
    """, unsafe_allow_html=True)

    # 标题
    st.title("DeepAnalyze")

    col_left, col_right = st.columns([0.85, 2.15], gap="large")

    with col_left:
        # 文件上传区域
        st.markdown("### 数据与任务")

        uploaded_files = st.file_uploader(
            "上传分析文件 (可选)",
            type=[ext.replace('.', '') for ext in ALLOWED_FILE_TYPES],
            accept_multiple_files=True
        )

        # 处理文件上传
        if uploaded_files:
            for uploaded_file in uploaded_files:
                if uploaded_file.name not in [f['name'] for f in st.session_state.uploaded_files]:
                    # 保存临时文件
                    temp_dir = Path("temp_uploads")
                    temp_dir.mkdir(exist_ok=True)
                    temp_path = temp_dir / uploaded_file.name

                    with open(temp_path, "wb") as f:
                        f.write(uploaded_file.getbuffer())

                    # 上传到后端
                    upload_result = upload_file_to_backend(str(temp_path))
                    if upload_result:
                        st.session_state.uploaded_files.append({
                            "name": uploaded_file.name,
                            "size": uploaded_file.size,
                            "path": str(temp_path),
                            "file_path": upload_result.get("file_path"),
                            "session_id": upload_result.get("session_id")
                        })

        # 显示已上传的文件列表
        if st.session_state.uploaded_files:
            st.markdown("**已上传文件：**")
            for file_info in st.session_state.uploaded_files:
                file_size_str = format_file_size(file_info['size'])
                st.markdown(
                    f"""
                    <div class="file-item">
                        <div style="width: 6px; height: 6px; background: #10b981; border-radius: 50%; flex-shrink: 0;"></div>
                        <div style="flex: 1; font-size: 14px; color: #1f2937;">{file_info['name']} ({file_size_str})</div>
                    </div>
                    """,
                    unsafe_allow_html=True
                )

        st.markdown("#### 数据资产")
        assets_placeholder = st.empty()
        assets_placeholder.markdown(
            render_assets_html(st.session_state.workbench_state.get("assets", [])),
            unsafe_allow_html=True,
        )

        # 分析指令区域
        st.markdown("#### 分析指令")

        instruction = st.text_area(
            "分析指令输入",
            placeholder="请输入分析指令",
            height=150,
            label_visibility="collapsed",
            key="instruction_input",
        )
        # 保存到 session_state
        st.session_state.instruction = instruction

        # 快捷指令按钮（2x2布局）
        col1, col2 = st.columns(2)
        with col1:
            st.button(
                "数据概览",
                use_container_width=True,
                key="quick_data_overview",
                on_click=set_quick_instruction,
                args=(QUICK_INSTRUCTIONS["数据概览"],),
            )
            st.button(
                "趋势分析",
                use_container_width=True,
                key="quick_trend",
                on_click=set_quick_instruction,
                args=(QUICK_INSTRUCTIONS["趋势分析"],),
            )
        with col2:
            st.button(
                "生成报告",
                use_container_width=True,
                key="quick_report",
                on_click=set_quick_instruction,
                args=(QUICK_INSTRUCTIONS["生成报告"],),
            )
            st.button(
                "对话分析",
                use_container_width=True,
                key="quick_conversation",
                on_click=set_quick_instruction,
                args=(QUICK_INSTRUCTIONS["对话分析"],),
            )

        # 操作按钮：开始分析、恢复分析、停止分析
        col_start, col_resume, col_stop = st.columns(3)
        with col_start:
            start_btn = st.button("▶️ 开始分析", type="primary", use_container_width=True)
        with col_resume:
            resume_btn = st.button("🔄 恢复分析", use_container_width=True)
        with col_stop:
            stop_btn = st.button("⏹️ 停止分析", use_container_width=True)

        # 清空按钮（单独居中显示）
        clear_btn = st.button("清空", use_container_width=True, key="clear_btn")

    with col_right:
        st.markdown("### 分析工作台")
        summary_placeholder = st.empty()
        tab_process, tab_report, tab_outputs = st.tabs(["分析过程", "最终报告", "产物与执行"])

        with tab_process:
            steps_placeholder = st.empty()

        with tab_report:
            result_container = st.container(height=520)
            with result_container:
                result_placeholder = st.empty()

        with tab_outputs:
            st.markdown("#### 产物")
            artifacts_placeholder = st.empty()
            st.markdown("#### 执行记录")
            executions_placeholder = st.empty()

        render_workbench_views(
            summary_placeholder,
            steps_placeholder,
            artifacts_placeholder,
            executions_placeholder,
            assets_placeholder,
        )

        if stop_btn:
            st.session_state.is_analyzing = False
            result_text = st.session_state.analysis_result or "分析尚未产生文本结果。"
            result_placeholder.markdown(
                format_result_html(result_text + "\n\n分析已停止。"),
                unsafe_allow_html=True,
            )

        elif resume_btn:
            resumable = check_resumable()
            if not resumable:
                result_placeholder.warning("当前会话没有可恢复的分析状态。")
            else:
                stage = resumable.get("current_stage", "?")
                steps = resumable.get("total_steps", 0)
                findings = resumable.get("findings_count", 0)
                resume_header = f"**恢复分析** · {stage} · {steps} 步 · {findings} 个发现\n\n"
                for chunk in start_resume_stream():
                    result_placeholder.markdown(
                        format_result_html(resume_header + chunk), unsafe_allow_html=True
                    )
                    render_workbench_views(
                        summary_placeholder,
                        steps_placeholder,
                        artifacts_placeholder,
                        executions_placeholder,
                        assets_placeholder,
                    )

        elif start_btn:
            current_instruction = st.session_state.get('instruction_input', '') or st.session_state.get('instruction', '')
            if not current_instruction or not current_instruction.strip():
                result_placeholder.error("请输入分析指令。")
            else:
                for chunk in start_analysis_stream(current_instruction):
                    result_placeholder.markdown(
                        format_result_html(chunk), unsafe_allow_html=True
                    )
                    render_workbench_views(
                        summary_placeholder,
                        steps_placeholder,
                        artifacts_placeholder,
                        executions_placeholder,
                        assets_placeholder,
                    )

        elif st.session_state.analysis_result:
            result_placeholder.markdown(
                format_result_html(st.session_state.analysis_result), unsafe_allow_html=True
            )
        else:
            result_placeholder.markdown("分析报告将在完成后显示。")

        # 如果点击清空按钮
        if clear_btn:
            st.session_state.uploaded_files = []
            st.session_state.file_ids = []
            st.session_state.generated_files = []
            st.session_state.analysis_result = ""
            st.session_state.instruction = ""
            st.session_state.is_analyzing = False
            st.session_state.workbench_state = empty_workbench_state()
            st.session_state.workbench_fingerprint = None
            st.rerun()

    # 页脚 - 固定定位在右下角（匹配 Gradio 版本）
    st.markdown(
        """
        <div class="streamlit-footer">
            <span>通过API 使用</span>
            <span> | </span>
            <span>使用 Streamlit 构建</span>
            <span> | </span>
            <span>⚙️ 设置</span>
        </div>
        """,
        unsafe_allow_html=True
    )


if __name__ == "__main__":
    main()

"""迷你 Coding Agent 的工具层。

设计要点（对应 docs/design-decisions.md 决策 2/3/4）：
- 每个工具 = 一个普通 Python 函数 + JSON Schema 声明，注册表驱动，不依赖任何框架。
- 所有文件操作限制在 workdir 内（路径逃逸防护）。
- 工具输出统一 JSON 字符串返回，超长结果截断，防止上下文被工具输出撑爆。
- 危险操作（shell 执行、文件写入）通过注入的 confirm 回调请求人工确认。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Callable, Optional

# ---------------------------------------------------------------- 常量

MAX_OUTPUT_CHARS = 6_000        # 单个工具结果的最大字符数
MAX_GREP_HITS = 40              # grep 最多返回的命中行数
SHELL_TIMEOUT_SECONDS = 60      # shell 命令超时
IGNORED_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build"}
BINARY_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".pdf", ".zip", ".gz", ".exe", ".dll", ".so", ".class", ".jar"}


def _clip(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    """截断超长输出，标注被截断，让模型知道信息不完整。"""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [输出已截断，原始长度 {len(text)} 字符。可缩小搜索范围或分段读取]"


# ---------------------------------------------------------------- 工具实现

def make_tool_impls(workdir: Path, confirm: Optional[Callable[[str], bool]] = None):
    """创建绑定到指定工作目录的工具实现集合。

    confirm: 危险操作确认回调。传入命令描述，返回 True 放行。
             None 表示自动放行（仅用于测试）。
    """
    root = workdir.resolve()

    def _safe_path(rel: str) -> Path:
        """解析相对路径并校验不逃逸出工作目录（决策 3：路径安全）。"""
        p = (root / rel).resolve()
        if not str(p).startswith(str(root)):
            raise PermissionError(f"路径越界: {rel} -> {p}")
        return p

    # ---- list_dir -------------------------------------------------
    def list_dir(path: str = ".") -> str:
        p = _safe_path(path)
        if not p.is_dir():
            return json.dumps({"error": f"不是目录: {path}"}, ensure_ascii=False)
        entries = []
        for child in sorted(p.iterdir()):
            if child.name in IGNORED_DIRS:
                continue
            kind = "dir" if child.is_dir() else "file"
            size = child.stat().st_size if child.is_file() else ""
            entries.append({"name": child.name, "type": kind, "size": size})
        return _clip(json.dumps({"path": path, "entries": entries}, ensure_ascii=False))

    # ---- read_file ------------------------------------------------
    def read_file(path: str, start_line: int = 1, end_line: int = 200) -> str:
        p = _safe_path(path)
        if p.suffix.lower() in BINARY_SUFFIXES:
            return json.dumps({"error": f"疑似二进制文件: {path}"}, ensure_ascii=False)
        if not p.is_file():
            return json.dumps({"error": f"文件不存在: {path}"}, ensure_ascii=False)
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        start_line = max(1, start_line)
        end_line = min(len(lines), end_line)
        chunk = "".join(f"{i:>5} | {lines[i-1]}\n" for i in range(start_line, end_line + 1))
        more = end_line < len(lines)
        return _clip(json.dumps({
            "file": path,
            "total_lines": len(lines),
            "range": [start_line, end_line],
            "content": chunk,
            "has_more": more,
        }, ensure_ascii=False))

    # ---- write_file -----------------------------------------------
    def write_file(path: str, content: str) -> str:
        p = _safe_path(path)
        if confirm is not None:
            ok = confirm(f"写入文件 {path}（{len(content)} 字符）")
            if not ok:
                return json.dumps({"status": "rejected", "note": "用户拒绝了写入"}, ensure_ascii=False)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return json.dumps({"status": "ok", "file": path, "bytes": len(content)}, ensure_ascii=False)

    # ---- edit_file（精确旧文本替换，对齐 Claude Code 的 Edit 设计）----
    def edit_file(path: str, old_text: str, new_text: str) -> str:
        p = _safe_path(path)
        if not p.is_file():
            return json.dumps({"error": f"文件不存在: {path}"}, ensure_ascii=False)
        src = p.read_text(encoding="utf-8")
        count = src.count(old_text)
        if count == 0:
            return json.dumps({"error": "old_text 未找到。请先用 read_file 获取精确原文"}, ensure_ascii=False)
        if count > 1:
            return json.dumps({"error": f"old_text 出现 {count} 次，需要包含更多上下文使其唯一"}, ensure_ascii=False)
        if confirm is not None:
            ok = confirm(f"编辑文件 {path}: 用 {len(new_text)} 字符替换 {len(old_text)} 字符")
            if not ok:
                return json.dumps({"status": "rejected"}, ensure_ascii=False)
        p.write_text(src.replace(old_text, new_text, 1), encoding="utf-8")
        return json.dumps({"status": "ok", "file": path}, ensure_ascii=False)

    # ---- run_shell ------------------------------------------------
    def run_shell(command: str) -> str:
        if confirm is not None:
            ok = confirm(f"执行命令: {command}")
            if not ok:
                return json.dumps({"status": "rejected"}, ensure_ascii=False)
        try:
            proc = subprocess.run(
                command, shell=True, cwd=str(root), capture_output=True,
                text=True, timeout=SHELL_TIMEOUT_SECONDS, encoding="utf-8", errors="replace",
            )
        except subprocess.TimeoutExpired:
            return json.dumps({"error": f"命令超时（>{SHELL_TIMEOUT_SECONDS}s）"}, ensure_ascii=False)
        out = (proc.stdout or "") + (("\n[stderr]\n" + proc.stderr) if proc.stderr else "")
        return _clip(json.dumps({
            "command": command, "exit_code": proc.returncode, "output": out.strip() or "(无输出)",
        }, ensure_ascii=False))

    # ---- grep（代码搜索，Agent 检索的主力）--------------------------
    def grep(pattern: str, glob: str = "**/*.py", regex: bool = False) -> str:
        try:
            if regex:
                rx = re.compile(pattern)
            else:
                rx = re.compile(re.escape(pattern))
        except re.error as e:
            return json.dumps({"error": f"正则错误: {e}"}, ensure_ascii=False)
        hits, files_scanned = [], 0
        for f in root.glob(glob):
            if not f.is_file() or f.suffix.lower() in BINARY_SUFFIXES:
                continue
            if any(part in IGNORED_DIRS for part in f.relative_to(root).parts):
                continue
            files_scanned += 1
            try:
                for lineno, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                    if rx.search(line):
                        hits.append({"file": str(f.relative_to(root)), "line": lineno, "text": line.strip()[:200]})
                        if len(hits) >= MAX_GREP_HITS:
                            break
            except OSError:
                continue
            if len(hits) >= MAX_GREP_HITS:
                break
        return _clip(json.dumps({
            "pattern": pattern, "files_scanned": files_scanned,
            "hit_count": len(hits), "truncated": len(hits) >= MAX_GREP_HITS,
            "hits": hits,
        }, ensure_ascii=False))

    return {
        "list_dir": list_dir,
        "read_file": read_file,
        "write_file": write_file,
        "edit_file": edit_file,
        "run_shell": run_shell,
        "grep": grep,
    }


# ---------------------------------------------------------------- Schema（供 LLM 用的工具声明）

def tool_schemas() -> list[dict]:
    return [
        {"type": "function", "function": {
            "name": "list_dir",
            "description": "列出目录内容。先用它了解项目结构，再决定读哪个文件。",
            "parameters": {"type": "object", "properties": {
                "path": {"type": "string", "description": "相对路径，默认当前目录"}},
                "required": []}}},
        {"type": "function", "function": {
            "name": "read_file",
            "description": "按行号范围读取文件内容（每次最多约 200 行）。大文件用 start_line/end_line 分段读。",
            "parameters": {"type": "object", "properties": {
                "path": {"type": "string"},
                "start_line": {"type": "integer", "description": "起始行，从 1 开始"},
                "end_line": {"type": "integer"}},
                "required": ["path"]}}},
        {"type": "function", "function": {
            "name": "write_file",
            "description": "创建或整文件覆写。修改已有文件优先用 edit_file。",
            "parameters": {"type": "object", "properties": {
                "path": {"type": "string"}, "content": {"type": "string"}},
                "required": ["path", "content"]}}},
        {"type": "function", "function": {
            "name": "edit_file",
            "description": "精确替换：old_text 必须与文件原文完全一致且唯一。修改前先 read_file 确认原文。",
            "parameters": {"type": "object", "properties": {
                "path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}},
                "required": ["path", "old_text", "new_text"]}}},
        {"type": "function", "function": {
            "name": "run_shell",
            "description": "在项目根目录执行 shell 命令（如运行测试、git diff）。修改代码后应运行相关测试验证。",
            "parameters": {"type": "object", "properties": {
                "command": {"type": "string"}}, "required": ["command"]}}},
        {"type": "function", "function": {
            "name": "grep",
            "description": "在代码库中搜索文本/正则。了解'某功能在哪实现'时先用它，再 read_file 细读。",
            "parameters": {"type": "object", "properties": {
                "pattern": {"type": "string"},
                "glob": {"type": "string", "description": "文件过滤模式，默认 **/*.py"},
                "regex": {"type": "boolean", "description": "pattern 是否为正则，默认 false"}},
                "required": ["pattern"]}}},
    ]

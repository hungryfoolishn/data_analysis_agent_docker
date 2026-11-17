"""迷你 Coding Agent 的 CLI 入口。

用法：
    export OPENAI_API_KEY=sk-xxx            # 或 DEEPSEEK_API_KEY 等
    export OPENAI_BASE_URL=https://api.deepseek.com/v1   # 兼容 OpenAI 协议的任意端点
    python main.py --task "在 sandbox 里给 greet.py 增加一个 say_goodbye 函数" --workdir ./sandbox

交互确认：写文件 / 执行命令时会在终端请求 y/n 确认（安全设计的一部分）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from openai import OpenAI

from agent import AgentLoop


def terminal_confirm(description: str) -> bool:
    print(f"\n  ⚠ 请求确认: {description}")
    return input("  允许执行? [y/N] ").strip().lower() in ("y", "yes")


def on_step(step: int, msg, tokens: int) -> None:
    text = (msg.content or "").strip()
    n_tools = len(getattr(msg, "tool_calls", None) or [])
    tag = f"工具调用 x{n_tools}" if n_tools else "思考中"
    print(f"\n[step {step:02d} | ~{tokens} tok | {tag}]")
    if text:
        print(text[:600])


def on_tool(name: str, args: dict | None, result: str) -> None:
    pretty = json.dumps(args, ensure_ascii=False)[:200] if args else ""
    try:
        payload = json.loads(result)
        brief = payload.get("error") or payload.get("status") or payload.get("output") or result
    except json.JSONDecodeError:
        brief = result
    print(f"  → {name}({pretty})")
    print(f"  ← {str(brief)[:300]}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Mini coding agent (hand-written loop)")
    parser.add_argument("--task", required=True, help="给 Agent 的任务描述")
    parser.add_argument("--workdir", default=".", help="项目工作目录")
    parser.add_argument("--model", default=os.environ.get("AGENT_MODEL", "deepseek-chat"))
    parser.add_argument("--max-steps", type=int, default=30)
    parser.add_argument("--yes", action="store_true", help="跳过人工确认（仅测试用）")
    args = parser.parse_args()

    api_key = os.environ.get("OPENAI_API_KEY")
    base_url = os.environ.get("OPENAI_BASE_URL")
    if not api_key:
        sys.exit("请设置 OPENAI_API_KEY 环境变量（DeepSeek/GLM/Qwen 等兼容端点均可）")

    client = OpenAI(api_key=api_key, base_url=base_url)
    agent = AgentLoop(
        client, model=args.model, workdir=Path(args.workdir),
        confirm=None if args.yes else terminal_confirm,
        max_steps=args.max_steps,
    )
    agent.on("on_step", on_step)
    agent.on("on_tool", on_tool)

    print(f"任务: {args.task}\n工作目录: {Path(args.workdir).resolve()}")
    final = agent.run(args.task)
    print("\n" + "=" * 60)
    print("最终回答:")
    print(final)


if __name__ == "__main__":
    main()

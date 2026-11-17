"""手写 Agent Loop（决策 1：不用框架，证明原理掌握）。

循环逻辑：
  1. 把消息发给 LLM（附带工具 schema）
  2. 模型返回 tool_calls -> 逐个执行 -> 结果以 role=tool 回传 -> 回到 1
  3. 模型不再调工具 -> 视为任务完成，返回最终回答

工程细节：
- 支持一次响应中的多个并行 tool_calls
- max_steps 防失控（死循环烧 token 是真实事故）
- 工具执行异常不会中断循环，而是作为错误信息回传给模型，
  让它有机会自我修正（这是 Agent 健壮性的关键设计）
"""

from __future__ import annotations

import json
from typing import Callable, Optional

from context_manager import ContextManager
from tools import make_tool_impls, tool_schemas

SYSTEM_PROMPT = """你是一个在项目代码库中工作的编码助手。你的目标是完成用户交给你的任务。

工作守则：
1. 动手前先了解现状：用 list_dir 看结构，用 grep 定位相关代码，再 read_file 细读。
2. 每次只做一类小改动，改完立即用 run_shell 运行相关测试或脚本验证。
3. 修改已有文件用 edit_file（精确替换），不要整文件覆写，除非新建文件。
4. 工具报错时先读取错误信息修正参数重试，不要盲目重试同一调用。
5. 任务完成或确认无法完成时，用简洁中文总结：做了什么、验证结果、遗留风险。不要编造没做过的操作。
"""


class AgentLoop:
    def __init__(self, client, model: str, workdir,
                 confirm: Optional[Callable[[str], bool]] = None,
                 max_steps: int = 30, budget_tokens: int = 24_000):
        self.client = client
        self.model = model
        self.max_steps = max_steps
        self.tools = make_tool_impls(workdir, confirm)
        self.schemas = tool_schemas()
        self.ctx = ContextManager(SYSTEM_PROMPT, budget_tokens=budget_tokens)
        self._hooks = {}   # 步骤回调，main.py 用它打印过程

    def on(self, event: str, fn: Callable) -> None:
        """注册事件回调: on_step(消息) / on_tool(名称, 参数, 结果)。"""
        self._hooks[event] = fn

    def _emit(self, event: str, *args) -> None:
        if event in self._hooks:
            self._hooks[event](*args)

    def run(self, task: str) -> str:
        self.ctx.start_task(task)
        schemas = [s["function"]["name"] for s in self.schemas]

        for step in range(1, self.max_steps + 1):
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=self.ctx.snapshot(),
                tools=self.schemas,
                temperature=0.2,           # 编码任务低温求稳
            )
            msg = resp.choices[0].message
            self._emit("on_step", step, msg, self.ctx.total_tokens())

            tool_calls = getattr(msg, "tool_calls", None)
            if not tool_calls:
                # 模型给出最终回答
                self.ctx.add({"role": "assistant", "content": msg.content})
                return msg.content or "(空回答)"

            # 保存 assistant 消息（含 tool_calls，API 要求原样回传）
            self.ctx.add({"role": "assistant", "content": msg.content,
                          "tool_calls": [tc.model_dump() for tc in tool_calls]})

            for tc in tool_calls:
                name = tc.function.name
                raw_args = tc.function.arguments or "{}"
                try:
                    args = json.loads(raw_args)
                except json.JSONDecodeError:
                    args = None
                if args is None or name not in self.tools:
                    result = json.dumps({"error": f"无效的工具调用: name={name!r}, args={raw_args[:200]}"},
                                        ensure_ascii=False)
                else:
                    try:
                        result = self.tools[name](**args)
                    except TypeError as e:
                        result = json.dumps({"error": f"参数错误: {e}"}, ensure_ascii=False)
                    except Exception as e:   # noqa: BLE001 —— 工具异常转回给模型自愈
                        result = json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False)

                self._emit("on_tool", name, args, result)
                self.ctx.add({"role": "tool", "tool_call_id": tc.id, "content": result})

            _ = schemas  # schemas 仅用于调试展示
        return f"已达最大步数上限（{self.max_steps} 步），任务未完成。已完成的部分保留在对话中。"

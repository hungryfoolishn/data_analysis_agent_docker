"""上下文管理器（决策 5：多级压缩策略）。

问题：Coding Agent 一次任务几十次工具调用，上下文很快超出预算。
策略（从便宜到贵）：
1. 工具结果入库前先截断（tools.py 的 _clip 已做第一道）。
2. 超预算时，先截断最旧的工具结果（保留头尾各 300 字符 + 提示语）。
3. 还不够时，把最早的任务描述摘要化（可选接入 LLM 摘要，此处用规则版演示）。
4. 永远保留：system 提示词、第一条用户任务、最近 N 条消息。

这是"工程上够用的压缩"，生产版会把第 3 步换成 LLM 摘要并外化 todo 文件。
"""

from __future__ import annotations

from typing import Callable, Optional


def estimate_tokens(text: str) -> int:
    """粗略 token 估算：CJK 字符约 1 字/1 token，其他约 4 字符/1 token。

    精确 tokenizer（tiktoken）依赖较大且与具体模型绑定，估算值
    用于"决定何时压缩"足够了 —— 压缩阈值本身就有安全余量。
    """
    cjk = sum(1 for ch in text if ord(ch) > 0x2E80)
    return cjk + (len(text) - cjk) // 4


class ContextManager:
    KEEP_RECENT = 6          # 压缩时保留最近的消息条数
    TRUNCATE_TOOL_TO = 600   # 旧工具结果截断后保留的字符数

    def __init__(self, system_prompt: str, budget_tokens: int = 24_000,
                 summarizer: Optional[Callable[[str], str]] = None):
        self.system = {"role": "system", "content": system_prompt}
        self.messages: list[dict] = []
        self.budget = budget_tokens
        self.summarizer = summarizer   # 可选：用 LLM 做摘要（demo 未接入）

    # ------------------------------------------------ 对外接口

    def start_task(self, task: str) -> None:
        self.messages = [{"role": "user", "content": task}]

    def add(self, message: dict) -> None:
        self.messages.append(message)
        self.compress_if_needed()

    def snapshot(self) -> list[dict]:
        """返回发给 API 的消息列表（system + 压缩后的对话）。"""
        return [self.system] + self.messages

    def total_tokens(self) -> int:
        return estimate_tokens(self.system["content"]) + sum(
            estimate_tokens(self._content_of(m)) for m in self.messages)

    # ------------------------------------------------ 压缩逻辑

    @staticmethod
    def _content_of(message: dict) -> str:
        c = message.get("content")
        if isinstance(c, str):
            return c
        if c is None:
            # assistant 消息可能只有 tool_calls
            return str(message.get("tool_calls", ""))
        return str(c)

    def compress_if_needed(self) -> None:
        if self.total_tokens() < self.budget:
            return
        # 第一级：截断最旧的 tool 结果（跳过最近 KEEP_RECENT 条）
        cut = len(self.messages) - self.KEEP_RECENT
        for m in self.messages[:cut]:
            if m.get("role") == "tool" and len(self._content_of(m)) > self.TRUNCATE_TOOL_TO:
                c = self._content_of(m)
                m["content"] = (c[:300] + "\n...[旧工具结果已压缩以节省上下文]..." + c[-300:])
        if self.total_tokens() < self.budget:
            return
        # 第二级：把最早的用户任务描述压缩（规则版摘要：保留原文前后各 500 字）
        if len(self.messages) > self.KEEP_RECENT and self.messages[0].get("role") == "user":
            c = self._content_of(self.messages[0])
            if len(c) > 1000:
                digest = c[:500] + "\n...[任务描述已压缩]...\n" + c[-500:]
                self.messages[0]["content"] = digest
        if self.total_tokens() < self.budget:
            return
        # 第三级：无 LLM 摘要可用时，粗暴丢弃中间旧消息（保底策略）
        while self.total_tokens() >= self.budget and len(self.messages) > self.KEEP_RECENT + 2:
            del self.messages[1]

"""ReAct executor adapter for Runtime V2 tasks."""

from __future__ import annotations

import time
from typing import Any, Callable, Optional

from langgraph_langchain.execution.task_models import (
    TaskExecutionRequest,
    build_execution_result,
    extract_artifact_ids,
)


def _message_text(message: Any) -> str:
    content = getattr(message, "content", None)
    if content is None:
        return str(message)
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text", "")))
            else:
                parts.append(str(item))
        return "\n".join(part for part in parts if part)
    return str(content)


def extract_agent_output(result: Any) -> str:
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        messages = result.get("messages")
        if isinstance(messages, list) and messages:
            return _message_text(messages[-1])
        for key in ("output", "output_text", "content"):
            if key in result:
                return str(result[key])
    content = getattr(result, "content", None)
    if content is not None:
        return _message_text(result)
    return str(result)


class ReactTaskExecutor:
    """Wrap an existing prebuilt ReAct agent behind the V2 task interface."""

    def __init__(
        self,
        *,
        agent: Any = None,
        agent_runner: Optional[Callable[[TaskExecutionRequest], Any]] = None,
        prompt_builder: Optional[Callable[[TaskExecutionRequest], str]] = None,
        token_callback: Optional[Callable[[str], Any]] = None,
    ) -> None:
        if agent is None and agent_runner is None:
            raise ValueError("agent or agent_runner is required")
        self._agent = agent
        self._agent_runner = agent_runner
        self._prompt_builder = prompt_builder
        self._token_callback = token_callback
        # True when the most recent execute streamed model tokens to the
        # callback, so callers can suppress the duplicate end-of-task preview.
        self.streamed_output = False

    def _stream_text(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            parts = []
            for item in value:
                if isinstance(item, dict):
                    parts.append(str(item.get("text", "")))
                else:
                    parts.append(str(item))
            return "".join(part for part in parts if part)
        return str(value)

    async def _astream_agent_events(
        self,
        invoke_kwargs: dict[str, Any],
        config: Any,
    ) -> Any:
        """Stream the agent graph, forwarding token deltas and keeping the
        final assistant message so the task is executed exactly once."""
        final_message: Any = None
        stream_kwargs: dict[str, Any] = {"version": "v2"}
        if config:
            stream_kwargs["config"] = config
        async for event in self._agent.astream_events(invoke_kwargs, **stream_kwargs):
            kind = event.get("event")
            if kind == "on_chat_model_stream":
                chunk = event.get("data", {}).get("chunk")
                text = self._stream_text(getattr(chunk, "content", None))
                if text and self._token_callback is not None:
                    self.streamed_output = True
                    self._token_callback(text)
            elif kind == "on_chat_model_end":
                message = event.get("data", {}).get("chunk")
                if message is not None and getattr(message, "content", None):
                    final_message = message
        return {"messages": [final_message]} if final_message is not None else {"messages": []}

    def execute(self, request: TaskExecutionRequest) -> Any:
        started_at = time.perf_counter()
        task = request.task
        tool_name = task.method or "react_task"
        self.streamed_output = False
        try:
            if self._agent_runner is not None:
                result = self._agent_runner(request)
            elif self._token_callback is not None and hasattr(self._agent, "astream_events"):
                import asyncio

                from langchain_core.messages import HumanMessage

                prompt = (
                    self._prompt_builder(request)
                    if self._prompt_builder is not None
                    else task.question
                )
                invoke_kwargs: dict[str, Any] = {
                    "messages": [HumanMessage(content=prompt)]
                }
                # The graph node runs this executor in a worker thread, so the
                # streaming loop needs its own event loop.  The agent graph is
                # streamed exactly once; tokens are forwarded incrementally.
                result = asyncio.run(
                    self._astream_agent_events(invoke_kwargs, request.config)
                )
            else:
                from langchain_core.messages import HumanMessage

                prompt = (
                    self._prompt_builder(request)
                    if self._prompt_builder is not None
                    else task.question
                )
                invoke_kwargs: dict[str, Any] = {
                    "messages": [HumanMessage(content=prompt)]
                }
                result = (
                    self._agent.invoke(invoke_kwargs, request.config)
                    if request.config
                    else self._agent.invoke(invoke_kwargs)
                )

            output = extract_agent_output(result)
            duration_ms = (time.perf_counter() - started_at) * 1000
            return build_execution_result(
                request,
                status="succeeded",
                tool_name=tool_name,
                output_artifact_ids=extract_artifact_ids(result),
                stdout_preview=output,
                duration_ms=duration_ms,
            )
        except Exception as exc:
            duration_ms = (time.perf_counter() - started_at) * 1000
            return build_execution_result(
                request,
                status="failed",
                tool_name=tool_name,
                stdout_preview=str(exc),
                error={"type": type(exc).__name__, "message": str(exc)},
                duration_ms=duration_ms,
            )

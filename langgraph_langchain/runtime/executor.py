"""Executor dispatcher for Runtime V2 tasks."""

from __future__ import annotations

import time
from typing import Optional

from langgraph_langchain.execution.react_executor import ReactTaskExecutor
from langgraph_langchain.execution.structured_executor import StructuredTaskExecutor
from langgraph_langchain.execution.task_models import (
    TaskExecutionRequest,
    build_execution_result,
)
from langgraph_langchain.runtime.models import ExecutionResult


class TaskExecutor:
    """Dispatch a scheduled task to the configured V2 executor."""

    def __init__(
        self,
        *,
        structured: Optional[StructuredTaskExecutor] = None,
        react: Optional[ReactTaskExecutor] = None,
        python: Optional[object] = None,
    ) -> None:
        self._structured = structured
        self._react = react
        self._python = python

    def execute(self, request: TaskExecutionRequest) -> ExecutionResult:
        started_at = time.perf_counter()
        task = request.task
        tool_name = task.method or f"{task.executor_type}_task"
        try:
            if task.executor_type == "structured":
                if self._structured is None:
                    raise ValueError("Structured executor is not configured")
                return self._structured.execute(request)
            if task.executor_type == "react":
                if self._react is None:
                    raise ValueError("React executor is not configured")
                return self._react.execute(request)
            if task.executor_type == "python":
                if self._python is None:
                    raise ValueError("Python executor is not configured")
                return self._python.execute(request)
            raise ValueError(f"Unknown executor type: {task.executor_type}")
        except Exception as exc:
            return build_execution_result(
                request,
                status="failed",
                tool_name=tool_name,
                stdout_preview=str(exc),
                error={"type": type(exc).__name__, "message": str(exc)},
                duration_ms=(time.perf_counter() - started_at) * 1000,
            )

    # A small alias keeps call sites readable in the graph integration.
    execute_task = execute

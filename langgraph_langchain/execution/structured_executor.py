"""Structured tool executor for Runtime V2 tasks."""

from __future__ import annotations

import time
from typing import Any, Callable, Mapping, Optional

from langgraph_langchain.execution.task_models import (
    TaskExecutionRequest,
    build_execution_result,
    extract_artifact_ids,
)
from langgraph_langchain.schemas import AnalysisTask


class StructuredTaskExecutor:
    """Invoke a deterministic analysis tool bound to a session."""

    def __init__(
        self,
        *,
        tools: Optional[Mapping[str, Any]] = None,
        tool_resolver: Optional[Callable[[str], Any]] = None,
    ) -> None:
        if tools is None and tool_resolver is None:
            raise ValueError("tools or tool_resolver is required")
        self._tools = dict(tools or {})
        self._tool_resolver = tool_resolver

    def _resolve_tool(self, name: str) -> Any:
        if self._tool_resolver is not None:
            return self._tool_resolver(name)
        return self._tools.get(name)

    @staticmethod
    def _invoke_tool(tool: Any, arguments: dict[str, Any]) -> Any:
        if hasattr(tool, "invoke"):
            return tool.invoke(arguments)
        return tool(**arguments)

    def execute(self, request: TaskExecutionRequest) -> Any:
        started_at = time.perf_counter()
        task: AnalysisTask = request.task
        tool_name = task.method or task.task_type
        try:
            tool = self._resolve_tool(tool_name)
            if tool is None:
                raise ValueError(f"Unknown structured tool: {tool_name}")
            output = self._invoke_tool(tool, dict(request.arguments))
            artifact_ids = extract_artifact_ids(output)
            duration_ms = (time.perf_counter() - started_at) * 1000
            status = "succeeded"
            error = None
            output_text = output if isinstance(output, str) else str(output)
            normalized_output = output_text.lstrip()
            if isinstance(output, dict) and output.get("status") == "needs_revision":
                status = "needs_revision"
                error = {
                    "type": "NeedsRevision",
                    "message": str(output.get("message", "Tool requested revision")),
                }
            elif normalized_output.startswith("[ERROR]"):
                status = "failed"
                error = {
                    "type": "ToolExecutionError",
                    "message": normalized_output,
                }
            elif normalized_output.startswith("REPORT REJECTED"):
                status = "needs_revision"
                error = {
                    "type": "ReportValidationFeedback",
                    "message": normalized_output,
                }
            return build_execution_result(
                request,
                status=status,
                tool_name=tool_name,
                output_artifact_ids=artifact_ids,
                stdout_preview=str(output),
                error=error,
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

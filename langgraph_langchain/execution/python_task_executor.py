"""Task-level adapter for the existing isolated Python executor."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable, Optional

from langgraph_langchain.execution.models import PythonExecutionRequest
from langgraph_langchain.execution.python_executor import IsolatedPythonExecutor
from langgraph_langchain.execution.task_models import (
    TaskExecutionRequest,
    build_execution_result,
)


class PythonTaskExecutor:
    """Run task code in the existing isolated Python subprocess backend."""

    def __init__(
        self,
        *,
        executor: Optional[IsolatedPythonExecutor] = None,
        workspace_dir: Optional[str | Path] = None,
        source_path: Optional[str | Path] = None,
        timeout_seconds: float = 60.0,
        artifact_recorder: Optional[Callable[[dict[str, Any]], Any]] = None,
    ) -> None:
        self._executor = executor or IsolatedPythonExecutor()
        self._workspace_dir = workspace_dir
        self._source_path = source_path
        self._timeout_seconds = timeout_seconds
        self._artifact_recorder = artifact_recorder

    @staticmethod
    def _artifact_ids(records: list[Any]) -> list[str]:
        ids: list[str] = []
        for record in records:
            if isinstance(record, str) and record:
                ids.append(record)
            elif isinstance(record, dict):
                artifact_id = record.get("artifact_id")
                if isinstance(artifact_id, str) and artifact_id:
                    ids.append(artifact_id)
        return list(dict.fromkeys(ids))

    def execute(self, request: TaskExecutionRequest) -> Any:
        started_at = time.perf_counter()
        task = request.task
        tool_name = task.method or "python_task"
        try:
            code = request.code
            if code is None:
                code = task.constraints.get("code")
            if code is None and isinstance(task.external_context, dict):
                code = task.external_context.get("code")
            if not code:
                raise ValueError("Python task has no code")

            workspace_dir = (
                request.workspace_dir
                or task.constraints.get("workspace_dir")
                or self._workspace_dir
            )
            if workspace_dir is None:
                raise ValueError("Python task has no workspace_dir")

            source_path = (
                request.source_path
                or task.constraints.get("source_path")
                or self._source_path
            )
            namespace = request.namespace or task.constraints.get("namespace") or {}
            execution_request = PythonExecutionRequest(
                code=str(code),
                workspace_dir=str(workspace_dir),
                source_path=str(source_path) if source_path else "",
                namespace=dict(namespace),
                timeout_seconds=request.timeout_seconds,
                max_output_chars=request.max_output_chars,
                input_asset_ids=request.input_asset_ids,
            )
            raw = self._executor.execute(execution_request)

            artifact_ids: list[str] = []
            if self._artifact_recorder is not None:
                metadata = []
                for artifact in raw.artifacts:
                    metadata.append(self._artifact_recorder(artifact))
                artifact_ids = self._artifact_ids(metadata)
            else:
                artifact_ids = self._artifact_ids(raw.artifacts)

            status = "succeeded" if raw.status == "succeeded" else "failed"
            if raw.status == "cancelled":
                status = "cancelled"

            error = None
            if status != "succeeded":
                error = {
                    "type": raw.error_type or raw.status,
                    "message": raw.error_message or raw.status,
                }

            return build_execution_result(
                request,
                status=status,
                tool_name=tool_name,
                output_artifact_ids=artifact_ids,
                stdout_preview=raw.output,
                error=error,
                duration_ms=raw.duration_ms or (time.perf_counter() - started_at) * 1000,
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

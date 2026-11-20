"""Task-level adapter for the worker dispatcher and process backend."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

from langgraph_langchain.execution.job_contract import build_process_worker_job
from langgraph_langchain.execution.models import PythonExecutionRequest
from langgraph_langchain.execution.python_executor import IsolatedPythonExecutor
from langgraph_langchain.execution.task_models import (
    TaskExecutionRequest,
    build_execution_result,
)
from langgraph_langchain.execution.worker_backend import (
    ProcessWorkerBackend,
    ProcessWorkerResult,
)
from langgraph_langchain.execution.worker_dispatcher import WorkerDispatcher


class PythonTaskExecutor:
    """Run task code through the worker dispatcher by default.

    A caller that explicitly injects the legacy raw executor gets the previous
    compatibility path. Production and normal Runtime V2 usage use the
    dispatcher-backed process backend.
    """

    def __init__(
        self,
        *,
        executor: Optional[IsolatedPythonExecutor] = None,
        workspace_dir: Optional[str | Path] = None,
        source_path: Optional[str | Path] = None,
        timeout_seconds: float = 60.0,
        artifact_recorder: Optional[Callable[[dict[str, Any]], Any]] = None,
        cancel_event_provider: Optional[Callable[[], Any]] = None,
    ) -> None:
        self._legacy_executor = executor
        self._backend = None if executor is not None else ProcessWorkerBackend()
        self._workspace_dir = workspace_dir
        self._source_path = source_path
        self._timeout_seconds = timeout_seconds
        self._artifact_recorder = artifact_recorder
        self._cancel_event_provider = cancel_event_provider

    @staticmethod
    def _artifact_ids(records: list[Any]) -> list[str]:
        ids: list[str] = []
        for record in records:
            if isinstance(record, str) and record:
                ids.append(record)
            elif isinstance(record, dict):
                artifact_id = record.get("artifact_id")
                if isinstance(record, dict) and isinstance(artifact_id, str) and artifact_id:
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
            if code is None:
                arguments = task.constraints.get("arguments")
                if isinstance(arguments, dict):
                    code = arguments.get("code")
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
            cancel_event = None
            if self._cancel_event_provider is not None:
                cancel_event = self._cancel_event_provider()

            if self._legacy_executor is not None:
                if cancel_event is not None:
                    raw = self._legacy_executor.execute(execution_request, cancel_event=cancel_event)
                else:
                    raw = self._legacy_executor.execute(execution_request)
                status = raw.status
                output = raw.output
                namespace_updates = raw.namespace_updates if raw.status == "succeeded" else {}
                artifact_metadata = list(raw.artifacts)
                error_type = raw.error_type
                error_message = raw.error_message
                duration_ms = raw.duration_ms
            else:
                job = build_process_worker_job(
                    execution_request,
                    session_id=task.session_id,
                    task_id=task.task_id,
                    execution_id=request.execution_id,
                )
                cancellation = cancel_event if cancel_event is not None else threading.Event()
                result = WorkerDispatcher(self._backend, backend_name="process").execute(
                    job,
                    cancellation,
                )
                status = result.status.value
                output = result.stdout + ("\n" + result.stderr if result.stderr else "")
                namespace_updates = (
                    result.namespace_updates
                    if isinstance(result, ProcessWorkerResult) and result.status.value == "succeeded"
                    else {}
                )
                artifact_metadata = getattr(result, "raw_artifacts", [])
                error_type = result.error.error_type if result.error is not None else None
                error_message = result.error.safe_message if result.error is not None else None
                duration_ms = float(result.duration_ms)

            if status == "succeeded" and namespace_updates:
                # Process namespace updates are a compatibility feature. The
                # portable WorkerJob does not carry arbitrary runtime objects.
                namespace.update(namespace_updates)

            artifact_ids: list[str] = []
            if self._artifact_recorder is not None:
                metadata = []
                for artifact in artifact_metadata:
                    metadata.append(self._artifact_recorder(artifact))
                artifact_ids = self._artifact_ids(metadata)
            else:
                artifact_ids = self._artifact_ids(artifact_metadata)

            public_status = "succeeded" if status == "succeeded" else "failed"
            if status == "cancelled":
                public_status = "cancelled"

            error = None
            if public_status != "succeeded":
                error = {
                    "type": error_type or status,
                    "message": error_message or status,
                }

            return build_execution_result(
                request,
                status=public_status,
                tool_name=tool_name,
                output_artifact_ids=artifact_ids,
                stdout_preview=output,
                error=error,
                duration_ms=duration_ms or (time.perf_counter() - started_at) * 1000,
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

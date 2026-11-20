"""Worker backend protocol, result contract and process adapter.

The process backend deliberately wraps the existing IsolatedPythonExecutor. It
does not create a second execution engine and does not turn process execution
into a container sandbox. Docker is represented only by a fail-closed stub.
"""

from __future__ import annotations

import hashlib
import os
import time
from enum import Enum
from pathlib import Path
from typing import Literal, Optional, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from .job_contract import ArtifactReference, WorkerJob
from .models import PythonExecutionRequest, PythonExecutionResult
from .python_executor import IsolatedPythonExecutor


class WorkerStatus(str, Enum):
    CREATED = "created"
    VALIDATING = "validating"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class WorkerErrorCode(str, Enum):
    NONE = "none"
    PYTHON_ERROR = "python_error"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    BACKEND_UNSUPPORTED = "backend_unsupported"
    BACKEND_UNAVAILABLE = "backend_unavailable"
    CONTRACT_INVALID = "contract_invalid"
    DUPLICATE_JOB = "duplicate_job"
    ARTIFACT_VALIDATION = "artifact_validation"
    INTERNAL = "internal"


class WorkerError(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    error_code: WorkerErrorCode = WorkerErrorCode.NONE
    error_type: str = ""
    safe_message: str = ""
    retryable: bool = False
    details: dict[str, str] = Field(default_factory=dict)


class WorkerResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    job_id: str = Field(min_length=1, max_length=128)
    status: WorkerStatus
    stdout: str = Field(default="", max_length=100_000)
    stderr: str = Field(default="", max_length=100_000)
    exit_code: Optional[int] = None
    duration_ms: int = Field(default=0, ge=0)
    artifacts: tuple[ArtifactReference, ...] = ()
    error: Optional[WorkerError] = None

    def model_dump_safe(self) -> dict:
        return self.model_dump(mode="json", exclude_none=True)


class BackendHealth(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state: Literal["ready", "unavailable", "not_implemented"]
    detail: str = ""

    @staticmethod
    def ready(detail: str = "") -> "BackendHealth":
        return BackendHealth(state="ready", detail=detail)

    @staticmethod
    def unavailable(detail: str = "") -> "BackendHealth":
        return BackendHealth(state="unavailable", detail=detail)

    @staticmethod
    def not_implemented(detail: str = "") -> "BackendHealth":
        return BackendHealth(state="not_implemented", detail=detail)


class BackendCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    supports_cancellation: bool = False
    supports_timeout: bool = False
    supports_network_isolation: bool = False
    supports_filesystem_isolation: bool = False
    supports_resource_limits: bool = False
    supports_container_isolation: bool = False


class UnsupportedBackendError(RuntimeError):
    """Raised when a selected backend cannot execute the requested job."""


class BackendUnavailableError(RuntimeError):
    """Raised when an existing backend cannot currently accept work."""


@runtime_checkable
class CancellationHandle(Protocol):
    def set(self) -> None:
        ...

    def is_set(self) -> bool:
        ...


@runtime_checkable
class WorkerBackend(Protocol):
    """Synchronous backend protocol matching current execution semantics."""

    capabilities: BackendCapabilities

    def execute(self, job: WorkerJob, cancellation: CancellationHandle) -> WorkerResult:
        ...

    def cancel(self, job_id: str) -> None:
        ...

    def health_check(self) -> BackendHealth:
        ...


class ArtifactValidationError(RuntimeError):
    """Raised when a worker artifact fails parent-side path/type validation."""


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


class ProcessWorkerBackend:
    """Adapter around the existing process-based IsolatedPythonExecutor."""

    def __init__(self, executor: Optional[IsolatedPythonExecutor] = None) -> None:
        self._executor = executor or IsolatedPythonExecutor()
        self._active_cancellations: dict[str, CancellationHandle] = {}
        self.capabilities = BackendCapabilities(
            supports_cancellation=True,
            supports_timeout=True,
            supports_network_isolation=False,
            supports_filesystem_isolation=False,
            supports_resource_limits=False,
            supports_container_isolation=False,
        )

    def execute(self, job: WorkerJob, cancellation: CancellationHandle) -> WorkerResult:
        started = time.monotonic()
        if cancellation.is_set():
            return WorkerResult(
                job_id=job.job_id,
                status=WorkerStatus.CANCELLED,
                error=WorkerError(
                    error_code=WorkerErrorCode.CANCELLED,
                    error_type="CancelledError",
                    safe_message="Job was cancelled before the process started",
                ),
            )
        self._active_cancellations[job.job_id] = cancellation
        try:
            request = PythonExecutionRequest(
                code=job.code,
                workspace_dir=job.workspace_dir,
                source_path=job.source_path,
                namespace=job.namespace,
                timeout_seconds=job.execution_options.timeout_seconds,
                max_output_chars=job.execution_options.max_output_chars,
                environment=job.execution_options.environment,
                input_asset_ids=[artifact.artifact_id for artifact in job.input_artifacts],
                allowed_directories=[job.workspace_dir],
                max_memory_mb=job.execution_options.max_memory_mb,
                max_artifact_bytes=job.execution_options.max_artifact_bytes,
            )
            raw = self._executor.execute(request, cancel_event=cancellation)
            artifacts = self._validate_artifacts(job, raw.artifacts) if raw.status == WorkerStatus.SUCCEEDED.value else ()
            return self._map_result(job, raw, artifacts, started)
        except Exception as exc:
            return WorkerResult(
                job_id=job.job_id,
                status=WorkerStatus.FAILED,
                duration_ms=int(max(0, (time.monotonic() - started) * 1000)),
                error=WorkerError(
                    error_code=WorkerErrorCode.INTERNAL,
                    error_type=type(exc).__name__,
                    safe_message=str(exc)[:1000],
                    retryable=False,
                ),
            )
        finally:
            self._active_cancellations.pop(job.job_id, None)

    def cancel(self, job_id: str) -> None:
        """Idempotently cancel an active process job.

        Completed or unknown job ids are intentionally no-ops.
        """
        cancellation = self._active_cancellations.get(job_id)
        if cancellation is not None:
            cancellation.set()

    def health_check(self) -> BackendHealth:
        return BackendHealth.ready("process backend adapted from IsolatedPythonExecutor")

    def _map_result(
        self,
        job: WorkerJob,
        raw: PythonExecutionResult,
        artifacts: tuple[ArtifactReference, ...],
        started: float,
    ) -> WorkerResult:
        duration_ms = int(max(0, round(raw.duration_ms or (time.monotonic() - started) * 1000)))
        if raw.status == WorkerStatus.SUCCEEDED.value:
            status = WorkerStatus.SUCCEEDED
            error = None
        elif raw.status == WorkerStatus.TIMED_OUT.value:
            status = WorkerStatus.TIMED_OUT
            error = WorkerError(
                error_code=WorkerErrorCode.TIMEOUT,
                error_type=raw.error_type or "TimeoutError",
                safe_message=raw.error_message or "Execution timed out",
                retryable=True,
                details={"timeout_seconds": str(job.execution_options.timeout_seconds)},
            )
        elif raw.status == WorkerStatus.CANCELLED.value:
            status = WorkerStatus.CANCELLED
            error = WorkerError(
                error_code=WorkerErrorCode.CANCELLED,
                error_type=raw.error_type or "CancelledError",
                safe_message=raw.error_message or "Execution was cancelled",
            )
        elif raw.status == WorkerStatus.FAILED.value:
            status = WorkerStatus.FAILED
            error = WorkerError(
                error_code=WorkerErrorCode.PYTHON_ERROR,
                error_type=raw.error_type or "PythonExecutionError",
                safe_message=raw.error_message or "Python execution failed",
                retryable=False,
            )
        else:
            status = WorkerStatus.FAILED
            error = WorkerError(
                error_code=WorkerErrorCode.INTERNAL,
                error_type=raw.error_type or "WorkerStatusError",
                safe_message=raw.error_message or f"Unexpected worker status: {raw.status}",
                retryable=False,
            )
        return WorkerResult(
            job_id=job.job_id,
            status=status,
            stdout=raw.stdout[: job.execution_options.max_output_chars],
            stderr=raw.stderr[: job.execution_options.max_output_chars],
            exit_code=raw.exit_code,
            duration_ms=duration_ms,
            artifacts=artifacts,
            error=error,
        )

    def _validate_artifacts(
        self,
        job: WorkerJob,
        raw_artifacts: list[dict],
    ) -> tuple[ArtifactReference, ...]:
        if len(raw_artifacts) > job.execution_options.max_artifacts:
            raise ArtifactValidationError("worker returned too many artifacts")
        workspace = Path(job.workspace_dir).resolve()
        approved: list[ArtifactReference] = []
        for index, entry in enumerate(raw_artifacts):
            if not isinstance(entry, dict) or not isinstance(entry.get("relative_path"), str):
                raise ArtifactValidationError("worker artifact is missing relative_path")
            try:
                reference = ArtifactReference(
                    artifact_id=f"{job.job_id}:output:{index}",
                    artifact_type=Path(entry["relative_path"]).suffix.lstrip(".").lower() or "file",
                    relative_path=entry["relative_path"],
                    size_bytes=entry.get("size_bytes"),
                )
            except ValueError as exc:
                raise ArtifactValidationError(str(exc)) from exc
            candidate = workspace / reference.relative_path
            if candidate.is_symlink() or not candidate.is_file():
                raise ArtifactValidationError("artifact is not a regular file")
            resolved = candidate.resolve()
            try:
                resolved.relative_to(workspace)
            except ValueError as exc:
                raise ArtifactValidationError("artifact escapes workspace") from exc
            actual_size = resolved.stat().st_size
            if reference.size_bytes is not None and reference.size_bytes != actual_size:
                raise ArtifactValidationError("artifact size metadata does not match file")
            if actual_size > job.execution_options.max_artifact_bytes:
                raise ArtifactValidationError("artifact exceeds size limit")
            if resolved.suffix.lower() not in job.output_policy.allowed_extensions:
                raise ArtifactValidationError("artifact type is not allowed")
            approved.append(
                reference.model_copy(
                    update={"size_bytes": actual_size, "sha256": _hash_file(resolved)}
                )
            )
        return tuple(approved)


class DockerWorkerBackend:
    """Fail-closed placeholder. This does not implement Docker isolation."""

    def __init__(self) -> None:
        self.capabilities = BackendCapabilities(
            supports_cancellation=False,
            supports_timeout=False,
            supports_network_isolation=False,
            supports_filesystem_isolation=False,
            supports_resource_limits=False,
            supports_container_isolation=False,
        )

    def execute(self, job: WorkerJob, cancellation: CancellationHandle) -> WorkerResult:
        raise UnsupportedBackendError("Docker worker backend is not implemented in A1.4.2.1")

    def cancel(self, job_id: str) -> None:
        raise UnsupportedBackendError("Docker worker backend is not implemented in A1.4.2.1")

    def health_check(self) -> BackendHealth:
        return BackendHealth.not_implemented("Docker backend is a fail-closed interface stub")

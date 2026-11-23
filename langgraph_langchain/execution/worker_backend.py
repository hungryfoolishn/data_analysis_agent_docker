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
from typing import Any, Literal, Optional, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from .job_contract import ArtifactReference, WorkerJob
from .docker_worker_protocol import PROTOCOL_VERSION, DockerJobEnvelope, DockerResultEnvelope, ProtocolProtocolError
from .container_manager import ContainerManager, ContainerSpec, DockerUnavailableError, ImageMissingError, ContainerLifecycleError, JobWorkspace
import logging

logger = logging.getLogger("deepanalyze.docker_backend")
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


class ProcessWorkerResult(WorkerResult):
    """Process-specific result extension carrying namespace state updates."""

    namespace_updates: dict[str, Any] = Field(default_factory=dict)

    @property
    def output(self) -> str:
        return self.stdout + ("\n" + self.stderr if self.stderr else "")

    @property
    def raw_artifacts(self) -> list[dict[str, Any]]:
        return [
            {"relative_path": artifact.relative_path, "size_bytes": artifact.size_bytes}
            for artifact in self.artifacts
        ]


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

    def execute(self, job: WorkerJob, cancellation: CancellationHandle) -> ProcessWorkerResult:
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
            return ProcessWorkerResult(
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
    ) -> ProcessWorkerResult:
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
        return ProcessWorkerResult(
            job_id=job.job_id,
            status=status,
            stdout=raw.stdout[: job.execution_options.max_output_chars],
            stderr=raw.stderr[: job.execution_options.max_output_chars],
            exit_code=raw.exit_code,
            duration_ms=duration_ms,
            artifacts=artifacts,
            error=error,
            namespace_updates=raw.namespace_updates if status == WorkerStatus.SUCCEEDED else {},
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
    """Real Docker worker backend (A1.4.2.2.1 foundation).

    Executes one base WorkerJob inside one dedicated, manager-controlled
    container using the versioned JSON protocol. Fails closed on every
    engine/protocol/lifecycle failure. There is no process-backend fallback.

    Foundation-level isolation actually applied at container create time:
    network none, cap-drop ALL, no-new-privileges, read-only rootfs, tmpfs /tmp,
    non-root user (10001), cpu/memory/pids limits, read-only input bind and a
    job-specific writable output bind.

    Not yet implemented (later phases): full filesystem hardening review,
    secret/network attack-test matrix, orphan reconciliation scheduling.
    """

    def __init__(
        self,
        manager: Optional["ContainerManager"] = None,
        *,
        image: Optional[str] = None,
    ) -> None:
        # Imported lazily so unit tests without Docker never pay import costs.
        self._manager = manager or ContainerManager(image=image) if image else manager or ContainerManager()
        self.capabilities = BackendCapabilities(
            supports_cancellation=True,
            supports_timeout=True,
            supports_network_isolation=True,
            supports_filesystem_isolation=True,
            supports_resource_limits=True,
            supports_container_isolation=True,
        )

    def execute(self, job: WorkerJob, cancellation: CancellationHandle) -> WorkerResult:
        started = time.monotonic()
        # Process-only jobs must never cross the container boundary.
        if type(job).__name__ == "ProcessWorkerJob":
            return WorkerResult(
                job_id=job.job_id,
                status=WorkerStatus.FAILED,
                error=WorkerError(
                    error_code=WorkerErrorCode.CONTRACT_INVALID,
                    error_type="ProcessWorkerJobRejected",
                    safe_message="process-only jobs cannot be executed in containers",
                    retryable=False,
                ),
            )
        try:
            return self._execute(job, cancellation, started)
        except DockerUnavailableError as exc:
            return self._failed(job.job_id, WorkerErrorCode.BACKEND_UNAVAILABLE, type(exc).__name__, str(exc), started, retryable=True)
        except ImageMissingError as exc:
            return self._failed(job.job_id, WorkerErrorCode.BACKEND_UNAVAILABLE, type(exc).__name__, str(exc), started, retryable=True)
        except ContainerLifecycleError as exc:
            return self._failed(job.job_id, WorkerErrorCode.BACKEND_UNAVAILABLE, type(exc).__name__, str(exc), started)
        except ProtocolProtocolError as exc:
            return self._failed(job.job_id, WorkerErrorCode.CONTRACT_INVALID, exc.code, exc.message, started)
        except ArtifactValidationError as exc:
            return self._failed(job.job_id, WorkerErrorCode.ARTIFACT_VALIDATION, "ArtifactValidation", str(exc), started)
        except Exception as exc:  # pragma: no cover - defensive fail closed
            return self._failed(job.job_id, WorkerErrorCode.INTERNAL, type(exc).__name__, str(exc)[:1000], started)

    def cancel(self, job_id: str) -> None:
        """Cancellation is carried by the CancellationHandle passed to execute()."""
        return None

    def health_check(self) -> BackendHealth:
        try:
            if self._manager.health_check():
                return BackendHealth.ready("docker engine reachable")
            return BackendHealth.unavailable("docker engine ping failed")
        except DockerUnavailableError as exc:
            return BackendHealth.unavailable(str(exc))

    # -- internals ---------------------------------------------------------
    def _execute(self, job: WorkerJob, cancellation: CancellationHandle, started: float) -> WorkerResult:
        if cancellation.is_set():
            return WorkerResult(
                job_id=job.job_id,
                status=WorkerStatus.CANCELLED,
                error=WorkerError(
                    error_code=WorkerErrorCode.CANCELLED,
                    error_type="CancelledError",
                    safe_message="Job was cancelled before dispatch",
                ),
            )
        envelope = DockerJobEnvelope(protocol_version=PROTOCOL_VERSION, job=job)
        # Copy authorized inputs from the session workspace into the isolated
        # job workspace (content copies; originals are never mounted).
        original_files: dict[str, Path] = {}
        host_workspace = Path(job.workspace_dir)
        for artifact in job.input_artifacts:
            from .path_safety import safe_workspace_join

            source = safe_workspace_join(host_workspace, artifact.relative_path)
            original_files[artifact.relative_path] = source
        workspace = self._manager.create_workspace(
            job.job_id,
            envelope.to_json_bytes(),
            original_files=original_files,
        )
        spec = ContainerSpec(
            job_id=job.job_id,
            image=self._manager._image,
            input_dir=str(workspace.root / "input"),
            output_dir=str(workspace.root / "output"),
            tmp_dir=str(workspace.root / "tmp"),
        )
        try:
            handle = self._manager.run_container(spec)
        except BaseException:
            try:
                self._manager.cleanup_workspace(job.job_id)
            except Exception:
                logger.warning("workspace cleanup after pre-container failure failed for job=%s", job.job_id)
            raise
        try:
            outcome = self._manager.wait(
                handle,
                timeout_seconds=job.execution_options.timeout_seconds,
                cancel_event=cancellation,
            )
            if outcome == "cancelled":
                self._manager.terminate(handle)
                self._manager.cleanup(handle)
                return WorkerResult(
                    job_id=job.job_id,
                    status=WorkerStatus.CANCELLED,
                    duration_ms=self._duration(started),
                    error=WorkerError(
                        error_code=WorkerErrorCode.CANCELLED,
                        error_type="CancelledError",
                        safe_message="container stopped and removed before accepting result",
                    ),
                )
            if outcome == "timeout":
                self._manager.terminate(handle)
                self._manager.cleanup(handle)
                return WorkerResult(
                    job_id=job.job_id,
                    status=WorkerStatus.TIMED_OUT,
                    duration_ms=self._duration(started),
                    error=WorkerError(
                        error_code=WorkerErrorCode.TIMEOUT,
                        error_type="TimeoutError",
                        safe_message=f"container execution timed out after {job.execution_options.timeout_seconds:g}s",
                        retryable=True,
                        details={"timeout_seconds": str(job.execution_options.timeout_seconds)},
                    ),
                )
            stdout_bytes = self._manager.collect_stdout(handle)
            exit_code = self._manager.collect_exit_code(handle)
            result = DockerResultEnvelope.parse_output(stdout_bytes, expected_job_id=job.job_id)
            artifacts = self._collect_validate_publish(job, workspace, handle, result)
            self._manager.cleanup(handle)
            duration_ms = self._duration(started)
            if result.status == "success":
                return WorkerResult(
                    job_id=job.job_id,
                    status=WorkerStatus.SUCCEEDED,
                    stdout=result.stdout[: job.execution_options.max_output_chars],
                    stderr=result.stderr[: job.execution_options.max_output_chars],
                    exit_code=exit_code if exit_code is not None else result.exit_code,
                    duration_ms=duration_ms,
                    artifacts=artifacts,
                )
            if result.error is not None:
                worker_code = (
                    WorkerErrorCode.PYTHON_ERROR
                    if result.error.code == "EXECUTION_ERROR"
                    else WorkerErrorCode.CONTRACT_INVALID
                )
                error = WorkerError(
                    error_code=worker_code,
                    error_type=result.error.code,
                    safe_message=result.error.message or "worker reported failure",
                    retryable=False,
                )
            else:
                error = WorkerError(
                    error_code=WorkerErrorCode.PYTHON_ERROR,
                    error_type="PythonExecutionError",
                    safe_message="worker reported failure without details",
                )
            return WorkerResult(
                job_id=job.job_id,
                status=WorkerStatus.FAILED,
                stdout=result.stdout[: job.execution_options.max_output_chars],
                stderr=result.stderr[: job.execution_options.max_output_chars],
                exit_code=exit_code if exit_code is not None else result.exit_code,
                duration_ms=duration_ms,
                artifacts=artifacts,
                error=error,
            )
        except Exception:
            # Any failure path must still clean up the container.
            try:
                self._manager.terminate(handle)
                self._manager.cleanup(handle)
            except Exception:
                logger.warning("cleanup after failure failed for job=%s", job.job_id)
            raise

    def _collect_validate_publish(
        self,
        job: WorkerJob,
        workspace: "JobWorkspace",
        handle: ContainerHandle,
        result: DockerResultEnvelope,
    ) -> tuple[ArtifactReference, ...]:
        """Collect declared outputs, verify input integrity, then publish.

        Order matters: input integrity is checked BEFORE accepting any output
        so an attacker cannot mutate inputs and still publish artifacts."""
        if len(result.artifacts) > job.execution_options.max_artifacts:
            raise ArtifactValidationError("worker returned too many artifacts")
        # 1) Verify staged inputs were not mutated (manager manifest hashes).
        self._manager.verify_input_integrity(workspace)
        if not result.artifacts:
            return ()
        # 2) Fetch outputs (symlink/link members rejected by the manager).
        names = [artifact.relative_path for artifact in result.artifacts]
        files = self._manager.collect_output_files(handle, names)
        validated: list[dict[str, Any]] = []
        contents: dict[str, bytes] = {}
        import hashlib

        for index, artifact in enumerate(result.artifacts):
            content = files.get(artifact.relative_path)
            if content is None:
                raise ArtifactValidationError(f"declared artifact is missing: {artifact.relative_path}")
            actual_size = len(content)
            if artifact.size_bytes != actual_size:
                raise ArtifactValidationError(f"artifact size mismatch: {artifact.relative_path}")
            if actual_size > job.execution_options.max_artifact_bytes:
                raise ArtifactValidationError(f"artifact exceeds size limit: {artifact.relative_path}")
            suffix = Path(artifact.relative_path).suffix.lower()
            if suffix not in job.output_policy.allowed_extensions:
                raise ArtifactValidationError(f"artifact type is not allowed: {artifact.relative_path}")
            digest = hashlib.sha256(content).hexdigest()
            if artifact.sha256 is not None and artifact.sha256 != digest:
                raise ArtifactValidationError(f"artifact hash mismatch: {artifact.relative_path}")
            validated.append(
                {
                    "relative_path": artifact.relative_path,
                    "size_bytes": actual_size,
                    "sha256": digest,
                }
            )
            contents[artifact.relative_path] = content
        # 3) Atomic all-or-nothing publish into the manager-owned collected dir.
        published = self._manager.publish_artifacts(workspace, validated, contents)
        return tuple(
            ArtifactReference(
                artifact_id=item["artifact_id"],
                artifact_type=Path(item["relative_path"]).suffix.lstrip(".").lower() or "file",
                relative_path=item["relative_path"],
                size_bytes=item["size_bytes"],
                sha256=item["sha256"],
            )
            for item in published
        )
    @staticmethod
    def _failed(job_id: str, code: WorkerErrorCode, error_type: str, message: str, started: float, retryable: bool = False) -> WorkerResult:
        return WorkerResult(
            job_id=job_id,
            status=WorkerStatus.FAILED,
            duration_ms=int(max(0, (time.monotonic() - started) * 1000)),
            error=WorkerError(
                error_code=code,
                error_type=error_type,
                safe_message=str(message)[:1000],
                retryable=retryable,
            ),
        )

    @staticmethod
    def _duration(started: float) -> int:
        return int(max(0, (time.monotonic() - started) * 1000))

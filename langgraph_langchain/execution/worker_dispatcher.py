"""Process-safe dispatcher over WorkerBackend implementations.

The dispatcher owns only job lifecycle, contract validation, backend selection
and result normalization. It does not own planning, data assets, evidence or
reporting. The process backend remains the default; Docker is fail-closed.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from pydantic import ValidationError

from .job_contract import WorkerJob
from .worker_backend import (
    BackendHealth,
    BackendUnavailableError,
    CancellationHandle,
    DockerWorkerBackend,
    ProcessWorkerBackend,
    UnsupportedBackendError,
    WorkerBackend,
    WorkerError,
    WorkerErrorCode,
    WorkerResult,
    WorkerStatus,
)

DEFAULT_WORKER_BACKEND = "process"


class WorkerValidationError(ValueError):
    """Raised when a caller submits an invalid WorkerJob."""


class DuplicateJobError(RuntimeError):
    """Raised when the same job id is already active in this dispatcher."""


@dataclass(frozen=True)
class JobState:
    job_id: str
    status: WorkerStatus
    updated_at: str
    backend: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_worker_backend(name: str = DEFAULT_WORKER_BACKEND) -> WorkerBackend:
    """Create an explicit backend; unknown and Docker names do not fall back."""
    if name == DEFAULT_WORKER_BACKEND:
        return ProcessWorkerBackend()
    if name == "docker":
        return DockerWorkerBackend()
    raise ValueError(f"Unknown worker backend: {name}")


def worker_backend_from_env(environ: Optional[dict[str, str]] = None) -> WorkerBackend:
    env = os.environ if environ is None else environ
    name = env.get("WORKER_BACKEND", DEFAULT_WORKER_BACKEND)
    return create_worker_backend(name)


class WorkerDispatcher:
    """Synchronous dispatcher for the current tool/executor call model."""

    def __init__(self, backend: WorkerBackend, *, backend_name: str = DEFAULT_WORKER_BACKEND) -> None:
        self._backend = backend
        self._backend_name = backend_name
        self._states: dict[str, JobState] = {}
        self._active_cancellations: dict[str, CancellationHandle] = {}
        self._lock = threading.RLock()

    def execute(self, job: WorkerJob, cancellation: CancellationHandle) -> WorkerResult:
        try:
            if not isinstance(job, WorkerJob):
                if isinstance(job, dict):
                    job = WorkerJob.model_validate(job)
                else:
                    raise WorkerValidationError("job must be a WorkerJob or WorkerJob payload")
        except ValidationError as exc:
            raise WorkerValidationError(str(exc)) from exc

        with self._lock:
            if job.job_id in self._active_cancellations:
                raise DuplicateJobError(f"job is already active: {job.job_id}")
            self._states[job.job_id] = JobState(
                job_id=job.job_id,
                status=WorkerStatus.VALIDATING,
                updated_at=_now(),
                backend=self._backend_name,
            )
            self._active_cancellations[job.job_id] = cancellation

        try:
            if cancellation.is_set():
                return self._finalize(
                    job.job_id,
                    WorkerResult(
                        job_id=job.job_id,
                        status=WorkerStatus.CANCELLED,
                        error=WorkerError(
                            error_code=WorkerErrorCode.CANCELLED,
                            error_type="CancelledError",
                            safe_message="Job was cancelled before dispatch",
                        ),
                    ),
                )

            with self._lock:
                self._states[job.job_id] = JobState(
                    job_id=job.job_id,
                    status=WorkerStatus.QUEUED,
                    updated_at=_now(),
                    backend=self._backend_name,
                )

            with self._lock:
                self._states[job.job_id] = JobState(
                    job_id=job.job_id,
                    status=WorkerStatus.RUNNING,
                    updated_at=_now(),
                    backend=self._backend_name,
                )

            try:
                result = self._backend.execute(job, cancellation)
            except UnsupportedBackendError as exc:
                result = WorkerResult(
                    job_id=job.job_id,
                    status=WorkerStatus.FAILED,
                    error=WorkerError(
                        error_code=WorkerErrorCode.BACKEND_UNSUPPORTED,
                        error_type=type(exc).__name__,
                        safe_message=str(exc),
                        retryable=False,
                    ),
                )
            except BackendUnavailableError as exc:
                result = WorkerResult(
                    job_id=job.job_id,
                    status=WorkerStatus.FAILED,
                    error=WorkerError(
                        error_code=WorkerErrorCode.BACKEND_UNAVAILABLE,
                        error_type=type(exc).__name__,
                        safe_message=str(exc),
                        retryable=True,
                    ),
                )
            except Exception as exc:
                result = WorkerResult(
                    job_id=job.job_id,
                    status=WorkerStatus.FAILED,
                    error=WorkerError(
                        error_code=WorkerErrorCode.INTERNAL,
                        error_type=type(exc).__name__,
                        safe_message=str(exc)[:1000],
                        retryable=False,
                    ),
                )

            if result.job_id != job.job_id:
                result = WorkerResult(
                    job_id=job.job_id,
                    status=WorkerStatus.FAILED,
                    error=WorkerError(
                        error_code=WorkerErrorCode.INTERNAL,
                        error_type="JobIdMismatch",
                        safe_message="Backend returned a different job_id",
                        retryable=False,
                    ),
                )
            if result.status == WorkerStatus.FAILED and result.error is None:
                result = result.model_copy(
                    update={
                        "error": WorkerError(
                            error_code=WorkerErrorCode.INTERNAL,
                            error_type="MissingWorkerError",
                            safe_message="Backend returned failed without an error",
                            retryable=False,
                        )
                    }
                )
            return self._finalize(job.job_id, result)
        finally:
            with self._lock:
                self._active_cancellations.pop(job.job_id, None)

    def cancel(self, job_id: str) -> None:
        """Idempotently request cancellation for an active job."""
        with self._lock:
            cancellation = self._active_cancellations.get(job_id)
        if cancellation is not None:
            cancellation.set()

    def state(self, job_id: str) -> JobState | None:
        with self._lock:
            return self._states.get(job_id)

    def active_job_ids(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._active_cancellations)

    def health_check(self) -> BackendHealth:
        return self._backend.health_check()

    def _finalize(self, job_id: str, result: WorkerResult) -> WorkerResult:
        with self._lock:
            self._states[job_id] = JobState(
                job_id=job_id,
                status=result.status,
                updated_at=_now(),
                backend=self._backend_name,
            )
        return result

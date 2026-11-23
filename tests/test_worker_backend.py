import threading

import pytest

from langgraph_langchain.execution.job_contract import (
    ArtifactPolicy,
    WorkerExecutionOptions,
    WorkerJob,
)
from langgraph_langchain.execution.worker_backend import (
    BackendCapabilities,
    BackendHealth,
    DockerWorkerBackend,
    ProcessWorkerBackend,
    UnsupportedBackendError,
    WorkerErrorCode,
    WorkerStatus,
)


def make_job(tmp_path, code, **overrides):
    values = {
        "code": code,
        "workspace_dir": str(tmp_path),
        "execution_options": WorkerExecutionOptions(timeout_seconds=5),
    }
    values.update(overrides)
    return WorkerJob(**values)


def test_process_backend_executes_simple_job(tmp_path):
    backend = ProcessWorkerBackend()
    result = backend.execute(make_job(tmp_path, "print('ok')"), threading.Event())
    assert result.status == WorkerStatus.SUCCEEDED
    assert result.stdout.strip() == "ok"
    assert result.error is None
    assert result.exit_code == 0
    assert result.duration_ms >= 0
    assert backend.health_check().state == "ready"


def test_process_backend_maps_python_failure(tmp_path):
    backend = ProcessWorkerBackend()
    result = backend.execute(
        make_job(tmp_path, "raise ValueError('boom')"),
        threading.Event(),
    )
    assert result.status == WorkerStatus.FAILED
    assert result.error is not None
    assert result.error.error_code == WorkerErrorCode.PYTHON_ERROR
    assert result.error.error_type == "ValueError"
    assert "boom" in result.error.safe_message


def test_process_backend_returns_timeout_status(tmp_path):
    backend = ProcessWorkerBackend()
    options = WorkerExecutionOptions(timeout_seconds=0.1)
    result = backend.execute(
        make_job(tmp_path, "while True: pass", execution_options=options),
        threading.Event(),
    )
    assert result.status == WorkerStatus.TIMED_OUT
    assert result.error is not None
    assert result.error.error_code == WorkerErrorCode.TIMEOUT
    assert result.error.retryable is True


def test_process_backend_cancel_before_start_is_idempotent(tmp_path):
    backend = ProcessWorkerBackend()
    cancellation = threading.Event()
    cancellation.set()
    job = make_job(tmp_path, "print('should not run')")
    assert backend.cancel(job.job_id) is None
    result = backend.execute(job, cancellation)
    assert result.status == WorkerStatus.CANCELLED
    assert result.error is not None
    assert result.error.error_code == WorkerErrorCode.CANCELLED
    backend.cancel(job.job_id)


def test_process_backend_validates_and_hashes_output_artifact(tmp_path):
    backend = ProcessWorkerBackend()
    code = "from pathlib import Path; Path('output').mkdir(exist_ok=True); Path('output/result.csv').write_text('abc')"
    job = make_job(tmp_path, code)
    result = backend.execute(job, threading.Event())
    assert result.status == WorkerStatus.SUCCEEDED
    assert len(result.artifacts) == 1
    ref = result.artifacts[0]
    assert ref.relative_path == "output/result.csv"
    assert ref.artifact_type == "csv"
    assert ref.size_bytes == 3
    assert ref.sha256 is not None and len(ref.sha256) == 64


def test_process_backend_rejects_disallowed_artifact_type(tmp_path):
    backend = ProcessWorkerBackend()
    code = "from pathlib import Path; Path('output').mkdir(exist_ok=True); Path('output/result.exe').write_text('x')"
    result = backend.execute(make_job(tmp_path, code), threading.Event())
    assert result.status == WorkerStatus.FAILED
    assert result.error is not None
    assert result.error.error_code == WorkerErrorCode.INTERNAL
    assert isinstance(result.error.safe_message, str)


def test_docker_backend_fails_closed_without_engine(tmp_path):
    """A1.4.2.2.1: the Docker backend is real, but fails closed when the
    engine is unreachable. There is no raise-based stub contract anymore."""
    from langgraph_langchain.execution.container_manager import ContainerManager, DockerEngineClient

    backend = DockerWorkerBackend(ContainerManager(DockerEngineClient(socket_path=str(tmp_path / "missing.sock"))))
    job = make_job(tmp_path, "pass")
    cancellation = threading.Event()
    result = backend.execute(job, cancellation)
    assert result.status == WorkerStatus.FAILED
    assert result.error is not None
    assert result.error.error_code == WorkerErrorCode.BACKEND_UNAVAILABLE
    assert backend.cancel(job.job_id) is None  # idempotent no-op
    health = backend.health_check()
    assert health.state == "unavailable"
    assert backend.capabilities.supports_container_isolation is True
    assert backend.capabilities.supports_network_isolation is True

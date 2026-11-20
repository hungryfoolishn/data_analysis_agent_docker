import threading

import pytest

from langgraph_langchain.execution.job_contract import WorkerExecutionOptions, WorkerJob
from langgraph_langchain.execution.worker_backend import (
    BackendCapabilities,
    DockerWorkerBackend,
    ProcessWorkerBackend,
    UnsupportedBackendError,
    WorkerErrorCode,
    WorkerResult,
    WorkerStatus,
)
from langgraph_langchain.execution.worker_dispatcher import (
    DEFAULT_WORKER_BACKEND,
    DuplicateJobError,
    WorkerDispatcher,
    WorkerValidationError,
    create_worker_backend,
    worker_backend_from_env,
)


def make_job(tmp_path, code="print('ok')", job_id=None):
    values = {
        "code": code,
        "workspace_dir": str(tmp_path),
        "execution_options": WorkerExecutionOptions(timeout_seconds=5),
    }
    if job_id is not None:
        values["job_id"] = job_id
    return WorkerJob(**values)


class FakeBackend:
    def __init__(self, result=None, release_event=None, set_cancel_after=False):
        self.capabilities = BackendCapabilities(supports_cancellation=True, supports_timeout=True)
        self.result = result
        self.release_event = release_event
        self.set_cancel_after = set_cancel_after
        self.cancelled_ids = set()

    def execute(self, job, cancellation):
        if self.release_event is not None:
            self.release_event.wait(timeout=2)
        if self.set_cancel_after:
            cancellation.set()
        if self.result is not None:
            return self.result
        return WorkerResult(job_id=job.job_id, status=WorkerStatus.SUCCEEDED)

    def cancel(self, job_id):
        self.cancelled_ids.add(job_id)

    def health_check(self):
        from langgraph_langchain.execution.worker_backend import BackendHealth
        return BackendHealth.ready("fake")


def test_factory_defaults_to_process_and_does_not_fall_back():
    assert DEFAULT_WORKER_BACKEND == "process"
    assert isinstance(create_worker_backend(), ProcessWorkerBackend)
    assert isinstance(create_worker_backend("docker"), DockerWorkerBackend)
    with pytest.raises(ValueError, match="Unknown worker backend"):
        create_worker_backend("kata")


def test_env_backend_selection_is_explicit():
    assert isinstance(worker_backend_from_env({}), ProcessWorkerBackend)
    assert isinstance(worker_backend_from_env({"WORKER_BACKEND": "docker"}), DockerWorkerBackend)
    with pytest.raises(ValueError):
        worker_backend_from_env({"WORKER_BACKEND": "firecracker"})


def test_dispatcher_executes_and_records_final_state(tmp_path):
    dispatcher = WorkerDispatcher(FakeBackend(), backend_name="process")
    job = make_job(tmp_path)
    result = dispatcher.execute(job, threading.Event())
    assert result.status == WorkerStatus.SUCCEEDED
    state = dispatcher.state(job.job_id)
    assert state is not None
    assert state.status == WorkerStatus.SUCCEEDED
    assert state.backend == "process"
    assert dispatcher.active_job_ids() == ()


def test_dispatcher_validates_contract_fail_closed(tmp_path):
    dispatcher = WorkerDispatcher(FakeBackend())
    with pytest.raises(WorkerValidationError):
        dispatcher.execute({"code": "", "workspace_dir": str(tmp_path)}, threading.Event())
    with pytest.raises(WorkerValidationError):
        dispatcher.execute({"code": "print(1)"}, threading.Event())


def test_docker_backend_failure_does_not_fall_back_to_process(tmp_path):
    dispatcher = WorkerDispatcher(DockerWorkerBackend(), backend_name="docker")
    result = dispatcher.execute(make_job(tmp_path), threading.Event())
    assert result.status == WorkerStatus.FAILED
    assert result.error is not None
    assert result.error.error_code == WorkerErrorCode.BACKEND_UNSUPPORTED
    assert isinstance(dispatcher._backend, DockerWorkerBackend)


def test_dispatcher_rejects_duplicate_active_job_id(tmp_path):
    release = threading.Event()
    dispatcher = WorkerDispatcher(FakeBackend(release_event=release))
    job = make_job(tmp_path, job_id="job_same")
    worker = threading.Thread(target=dispatcher.execute, args=(job, threading.Event()))
    worker.start()
    try:
        for _ in range(100):
            state = dispatcher.state("job_same")
            if state and state.status in {WorkerStatus.QUEUED, WorkerStatus.RUNNING}:
                break
            threading.Event().wait(0.01)
        with pytest.raises(DuplicateJobError):
            dispatcher.execute(job, threading.Event())
    finally:
        release.set()
        worker.join(timeout=3)
    assert not worker.is_alive()
    assert dispatcher.state("job_same").status == WorkerStatus.SUCCEEDED


def test_cancel_arriving_with_completion_is_not_marked_succeeded(tmp_path):
    dispatcher = WorkerDispatcher(FakeBackend(set_cancel_after=True))
    result = dispatcher.execute(make_job(tmp_path), threading.Event())
    assert result.status == WorkerStatus.CANCELLED
    assert result.error is not None
    assert result.error.error_code == WorkerErrorCode.CANCELLED


def test_repeated_cancel_and_unknown_cancel_are_safe(tmp_path):
    dispatcher = WorkerDispatcher(FakeBackend(release_event=threading.Event()))
    job = make_job(tmp_path, job_id="job_cancel")
    cancellation = threading.Event()
    worker = threading.Thread(target=dispatcher.execute, args=(job, cancellation))
    worker.start()
    try:
        for _ in range(100):
            if dispatcher.active_job_ids():
                break
            threading.Event().wait(0.01)
        dispatcher.cancel(job.job_id)
        dispatcher.cancel(job.job_id)
        dispatcher.cancel("job_unknown")
        cancellation.wait(timeout=1)
    finally:
        cancellation.set()
        worker.join(timeout=3)
    assert not worker.is_alive()
    assert dispatcher.state(job.job_id).status == WorkerStatus.CANCELLED

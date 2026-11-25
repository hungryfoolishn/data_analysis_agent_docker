"""Docker worker backend tests (WP10.4). Fail-closed unit tests run without
Docker; integration gate tests use the real engine (marked `docker`)."""

import json

import pytest

from langgraph_langchain.execution.container_manager import (
    ContainerLifecycleError,
    ContainerManager,
    ContainerSpec,
    DockerEngineClient,
    DockerUnavailableError,
    ImageMissingError,
)
from langgraph_langchain.execution.job_contract import WorkerExecutionOptions, WorkerJob
from langgraph_langchain.execution.worker_backend import DockerWorkerBackend, WorkerErrorCode, WorkerStatus
from tests.test_container_manager import FakeClient


def _preclean(manager, job_id: str) -> None:
    """Remove stale workspace/container for a fixed job id from aborted runs."""
    try:
        manager.cleanup_workspace(job_id)
    except ContainerLifecycleError:
        pass
    for item in manager._client.list_managed_containers():
        if item.get("Labels", {}).get("deepanalyze.job_id") == job_id:
            manager._client.remove_container(item["Id"], force=True)

def make_job(**overrides) -> WorkerJob:
    defaults = dict(
        code="result = sum([1, 2, 3, 4, 5])\nprint(result)\n",
        workspace_dir="/tmp/ws",
        execution_options=WorkerExecutionOptions(timeout_seconds=30),
    )
    defaults.update(overrides)
    return WorkerJob(**defaults)


class CancelEvent:
    def __init__(self):
        self._set = False

    def set(self):
        self._set = True

    def is_set(self):
        return self._set


class TestBackendFailClosed:
    def test_process_only_job_rejected(self):
        from langgraph_langchain.execution.job_contract import build_process_worker_job

        backend = DockerWorkerBackend(ContainerManager(FakeClient()))
        from langgraph_langchain.execution.models import PythonExecutionRequest
        request = PythonExecutionRequest(
            code="x=1", workspace_dir="/tmp/ws", source_path="", namespace={},
            timeout_seconds=10, max_output_chars=256, environment={},
        )
        job = build_process_worker_job(request)
        result = backend.execute(job, CancelEvent())
        assert result.status == WorkerStatus.FAILED
        assert result.error.error_code == WorkerErrorCode.CONTRACT_INVALID

    def test_docker_unavailable_no_fallback(self):
        backend = DockerWorkerBackend(ContainerManager(DockerEngineClient(socket_path="/nonexistent/missing.sock")))
        result = backend.execute(make_job(), CancelEvent())
        assert result.status == WorkerStatus.FAILED
        assert result.error.error_code == WorkerErrorCode.BACKEND_UNAVAILABLE
        assert result.status != WorkerStatus.SUCCEEDED

    def test_image_missing_fails_closed(self):
        class NoImageClient(FakeClient):
            def image_exists(self, image):
                return False

        backend = DockerWorkerBackend(ContainerManager(NoImageClient()))
        result = backend.execute(make_job(), CancelEvent())
        assert result.status == WorkerStatus.FAILED
        assert result.error.error_code == WorkerErrorCode.BACKEND_UNAVAILABLE

    def test_create_failure_propagates_as_failure(self):
        class FailCreateClient(FakeClient):
            def create_container(self, config, name):
                raise DockerUnavailableError("create refused")

        backend = DockerWorkerBackend(ContainerManager(FailCreateClient()))
        result = backend.execute(make_job(), CancelEvent())
        assert result.status == WorkerStatus.FAILED
        assert result.error.error_code == WorkerErrorCode.BACKEND_UNAVAILABLE

    def test_health_check_unavailable(self, tmp_path):
        backend = DockerWorkerBackend(ContainerManager(DockerEngineClient(socket_path=str(tmp_path / "no.sock"))))
        health = backend.health_check()
        assert health.state == "unavailable"

    def test_capabilities_declared(self):
        backend = DockerWorkerBackend(ContainerManager(FakeClient()))
        assert backend.capabilities.supports_container_isolation is True
        assert backend.capabilities.supports_network_isolation is True
        assert backend.capabilities.supports_resource_limits is True

    def test_dispatcher_no_silent_fallback(self):
        from langgraph_langchain.execution.worker_dispatcher import WorkerDispatcher

        backend = DockerWorkerBackend(ContainerManager(DockerEngineClient(socket_path="/nonexistent/missing.sock")))
        dispatcher = WorkerDispatcher(backend, backend_name="docker")
        result = dispatcher.execute(make_job(), CancelEvent())
        assert result.status == WorkerStatus.FAILED
        assert result.error.error_code == WorkerErrorCode.BACKEND_UNAVAILABLE


@pytest.mark.docker
class TestDockerIntegrationGate:
    def _manager(self):
        manager = ContainerManager()
        assert manager.health_check(), "real docker engine required"
        manager.ensure_image()
        return manager

    def test_a_hello_worker(self):
        """Test A — real container returns valid JSON result for a valid job."""
        backend = DockerWorkerBackend(self._manager())
        result = backend.execute(make_job(code="print('hello')"), CancelEvent())
        assert result.status == WorkerStatus.SUCCEEDED
        assert result.job_id.startswith("job_")
        assert result.error is None

    def test_b_python_execution(self):
        """Test B — real Python computation inside the container."""
        backend = DockerWorkerBackend(self._manager())
        result = backend.execute(make_job(code="result = sum([1, 2, 3, 4, 5])\nprint(result)\n"), CancelEvent())
        assert result.status == WorkerStatus.SUCCEEDED
        assert "15" in result.stdout

    def test_c_error_propagation_and_cleanup(self):
        """Test C — failing job returns failed result, container cleaned up."""
        manager = self._manager()
        backend = DockerWorkerBackend(manager)
        job = make_job(code="raise ValueError('intended failure')\n")
        result = backend.execute(job, CancelEvent())
        assert result.status == WorkerStatus.FAILED
        assert result.error is not None
        assert result.error.error_code == WorkerErrorCode.PYTHON_ERROR
        leftovers = {c["Labels"].get("deepanalyze.job_id") for c in manager._client.list_managed_containers()}
        assert job.job_id not in leftovers

    def test_d_timeout_no_success(self):
        """Test D — timeout job is terminated, TIMED_OUT returned, no fallback."""
        manager = self._manager()
        backend = DockerWorkerBackend(manager)
        job = make_job(
            code="import time\ntime.sleep(30)\n",
            execution_options=WorkerExecutionOptions(timeout_seconds=3),
        )
        result = backend.execute(job, CancelEvent())
        assert result.status == WorkerStatus.TIMED_OUT
        assert result.error.error_code == WorkerErrorCode.TIMEOUT
        leftovers = {c["Labels"].get("deepanalyze.job_id") for c in manager._client.list_managed_containers()}
        assert job.job_id not in leftovers

    def test_e_invalid_protocol_rejected(self):
        """Test E — worker rejects invalid protocol; backend fails closed."""
        # A job whose code produces multi-line stdout must fail protocol parse
        # (entrypoint still emits one line, so instead test envelope mismatch via
        # a manager-level stub: stdout tampering). Use backend directly against a
        # fake client returning garbage stdout.
        class GarbageStdoutClient(FakeClient):
            def __init__(self):
                super().__init__()
                self.calls = []

            def create_container(self, config, name):
                self.calls.append(config)
                return "fakeid"

            def inspect_container(self, container_id):
                return {
                    "Image": "sha256:" + "a" * 64,
                    "State": {"Running": False, "ExitCode": 0, "OOMKilled": False},
                }

            def container_logs(self, container_id, stdout_only=True):
                return b"garbage not json\n"

        backend = DockerWorkerBackend(ContainerManager(GarbageStdoutClient()))
        result = backend.execute(make_job(), CancelEvent())
        assert result.status == WorkerStatus.FAILED
        assert result.error.error_code == WorkerErrorCode.CONTRACT_INVALID

    def test_f_resource_config_inspect(self):
        """Test F — verify resource limits are applied via Docker inspect."""
        manager = self._manager()
        root = manager.stage_job_dir(
            "job_rescfg1",
            json.dumps({
                "protocol_version": "1", "kind": "job",
                "job": {
                    "job_id": "job_rescfg1",
                    "code": "print('cfg')",
                    "execution_options": {"timeout_seconds": 20},
                },
            }).encode(),
        )
        spec = ContainerSpec(job_id="job_rescfg1", input_dir=str(root / "input"), output_dir=str(root / "output"), tmp_dir=str(root / "tmp"))
        handle = manager.run_container(spec)
        try:
            info = manager._client.inspect_container(handle.container_id)
            host = info["HostConfig"]
            from langgraph_langchain.execution.container_manager import (
                DEFAULT_CPU_NANOCPUS,
                DEFAULT_MEMORY_BYTES,
                DEFAULT_PIDS_LIMIT,
            )
            assert host["Memory"] == DEFAULT_MEMORY_BYTES
            assert host["MemorySwap"] == DEFAULT_MEMORY_BYTES
            assert host["PidsLimit"] == DEFAULT_PIDS_LIMIT
            assert host["NanoCpus"] == DEFAULT_CPU_NANOCPUS
            assert host["NetworkMode"] == "none"
            assert host["ReadonlyRootfs"] is True
            assert host["CapDrop"] == ["ALL"]
            assert info["Config"]["User"] == "10001:10001"
        finally:
            manager.cleanup(handle)
    def test_g_artifact_transfer(self):
        """Test G — input artifact read-only, output artifact collected & verified."""
        import hashlib

        manager = self._manager()
        _preclean(manager, "job_artft01")
        input_name = "input_data.txt"
        input_content = b"input-payload-123"
        envelope = {
            "protocol_version": "1", "kind": "job",
            "job": {
                "job_id": "job_artft01",
                "code": (
                    "import os\n"
                    "with open('/workspace/input/input_data.txt','rb') as f: data = f.read()\n"
                    "assert data == b'input-payload-123'\n"
                    "with open('copy_result.txt','wb') as f: f.write(data)\n"
                    "try:\n"
                    "    with open('/workspace/input/input_data.txt','wb') as f: f.write(b'tamper')\n"
                    "    print('INPUT_MUTATED')\n"
                    "except OSError:\n"
                    "    print('INPUT_PROTECTED')\n"
                ),
                "input_artifacts": [
                    {"artifact_id": "in1", "artifact_type": "txt", "relative_path": input_name, "size_bytes": len(input_content)}
                ],
                "output_policy": {"allowed_extensions": [".txt"], "max_files": 10, "max_artifact_bytes": 1048576},
                "execution_options": {"timeout_seconds": 30},
            },
        }
        root = manager.stage_job_dir("job_artft01", json.dumps(envelope).encode(), input_files={input_name: input_content})
        original_mode = (root / "input" / input_name).stat().st_mode & 0o777
        spec = ContainerSpec(job_id="job_artft01", input_dir=str(root / "input"), output_dir=str(root / "output"), tmp_dir=str(root / "tmp"))
        handle = manager.run_container(spec)
        try:
            outcome = manager.wait(handle, timeout_seconds=60)
            assert outcome == "exited"
            stdout = manager.collect_stdout(handle)
            result = json.loads(stdout.decode().strip().splitlines()[-1])
            assert result["status"] == "success"
            assert "INPUT_PROTECTED" in result["stdout"]
            artifacts = result["artifacts"]
            assert len(artifacts) == 1
            artifact = artifacts[0]
            assert artifact["relative_path"] == "copy_result.txt"
            assert artifact["size_bytes"] == len(input_content)
            assert artifact["sha256"] == hashlib.sha256(input_content).hexdigest()
            # host original input untouched
            assert (root / "input" / input_name).read_bytes() == input_content
        finally:
            manager.cleanup(handle)

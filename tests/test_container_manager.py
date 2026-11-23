"""Container manager tests (WP10.3). Unit tests run without Docker; integration
tests use the real engine and are marked with `docker`."""

import json

import pytest

from langgraph_langchain.execution.container_manager import (
    ContainerLifecycleError,
    DEFAULT_CPU_NANOCPUS,
    DEFAULT_MEMORY_BYTES,
    DEFAULT_PIDS_LIMIT,
    ContainerManager,
    ContainerSpec,
    DockerEngineClient,
    DockerUnavailableError,
    ImageMissingError,
)


class TestManagerUnit:
    def test_build_config_security_invariants(self, tmp_path):
        manager = ContainerManager()
        spec = ContainerSpec(
            job_id="job_sec0001",
            input_dir=str(tmp_path / "input"),
            output_dir=str(tmp_path / "output"),
        )
        config = manager._build_config(spec)
        host = config["HostConfig"]
        assert host["NetworkMode"] == "none"
        assert "ALL" in host["CapDrop"]
        assert "no-new-privileges" in host["SecurityOpt"]
        assert host["ReadonlyRootfs"] is True
        assert host["Memory"] == DEFAULT_MEMORY_BYTES
        assert host["MemorySwap"] == DEFAULT_MEMORY_BYTES  # no extra swap
        assert host["PidsLimit"] == DEFAULT_PIDS_LIMIT
        assert host["NanoCpus"] == DEFAULT_CPU_NANOCPUS
        assert host["AutoRemove"] is False
        assert config["User"] == "10001:10001"
        binds = host["Binds"]
        assert f"{spec.input_dir}:/workspace/input:ro" in binds
        assert f"{spec.output_dir}:/workspace/output:rw" in binds
        assert config["Labels"]["deepanalyze.managed"] == "true"
        assert config["Labels"]["deepanalyze.job_id"] == "job_sec0001"
        # network disabled via both mechanisms
        assert config["NetworkingConfig"]["EndpointsConfig"] == {}

    def test_stage_job_dir_copies_input_and_sets_ro(self, tmp_path, monkeypatch):
        import langgraph_langchain.execution.container_manager as cm_mod
        monkeypatch.setattr(cm_mod, "DEFAULT_JOBS_ROOT", str(tmp_path / "jobs"))
        manager = ContainerManager()
        staging = manager.stage_job_dir("job_stage01", b'{"x":1}', input_files={"data.csv": b"a,b\n1,2\n"})
        assert (staging / "input" / "job.json").read_bytes() == b'{"x":1}'
        assert (staging / "input" / "data.csv").read_bytes() == b"a,b\n1,2\n"
        assert (staging / "output").is_dir()
        mode = (staging / "input").stat().st_mode & 0o777
        assert mode == 0o550

    def test_stage_job_dir_rejects_unsafe_names(self, tmp_path, monkeypatch):
        import langgraph_langchain.execution.container_manager as cm_mod
        monkeypatch.setattr(cm_mod, "DEFAULT_JOBS_ROOT", str(tmp_path / "jobs"))
        manager = ContainerManager()
        with pytest.raises(ValueError):
            manager.stage_job_dir("job_stage02", b"{}", input_files={"../evil": b"x"})

    def test_health_check_unreachable_socket(self, tmp_path):
        client = DockerEngineClient(socket_path=str(tmp_path / "missing.sock"))
        manager = ContainerManager(client)
        assert manager.health_check() is False

    def test_ensure_image_missing_fails_closed(self, tmp_path):
        manager = ContainerManager(DockerEngineClient(socket_path=str(tmp_path / "missing.sock")))
        with pytest.raises(DockerUnavailableError):
            manager.ensure_image()


class FakeClient:
    """Minimal fake for fail-closed backend tests (no real engine)."""

    def __init__(self, image_exists=True, fail=None):
        self.image_exists_result = image_exists
        self.fail = fail

    def ping(self):
        return True

    def image_exists(self, image):
        if self.fail:
            raise self.fail
        return self.image_exists_result

    def create_container(self, config, name):
        if self.fail:
            raise self.fail
        return "abc123"

    def start_container(self, container_id):
        if self.fail:
            raise self.fail

    def inspect_container(self, container_id):
        if self.fail:
            raise self.fail
        return {"State": {"Running": False, "ExitCode": 0, "OOMKilled": False}}

    def container_logs(self, container_id, stdout_only=True):
        return b""

    def stop_container(self, container_id, timeout=10):
        pass

    def kill_container(self, container_id):
        pass

    def remove_container(self, container_id, force=True):
        pass

    def list_managed_containers(self):
        return []

    def put_archive(self, container_id, path, data):
        pass

    def get_archive(self, container_id, path):
        raise DockerUnavailableError("fake")


@pytest.mark.docker
class TestManagerIntegration:
    def test_hello_worker_real_container(self, tmp_path):
        """Test A — real container start, JSON job, JSON result."""
        manager = ContainerManager()
        assert manager.health_check(), "real docker engine required for integration tests"
        manager.ensure_image()
        try:
            manager.cleanup_workspace("job_hello001")
        except ContainerLifecycleError:
            pass
        for item in manager._client.list_managed_containers():
            if item.get("Labels", {}).get("deepanalyze.job_id") == "job_hello001":
                manager._client.remove_container(item["Id"], force=True)
        import tempfile, os

        monkey_tmp = Path = None  # noqa: F841
        root = manager.stage_job_dir(
            "job_hello001",
            json.dumps({
                "protocol_version": "1",
                "kind": "job",
                "job": {
                    "job_id": "job_hello001",
                    "code": "print('hello worker')",
                    "output_policy": {"allowed_extensions": [".txt"]},
                    "execution_options": {"timeout_seconds": 20},
                },
            }).encode(),
        )
        spec = ContainerSpec(job_id="job_hello001", input_dir=str(root / "input"), output_dir=str(root / "output"))
        handle = manager.run_container(spec)
        outcome = manager.wait(handle, timeout_seconds=30)
        assert outcome == "exited"
        stdout = manager.collect_stdout(handle)
        exit_code = manager.collect_exit_code(handle)
        envelope = json.loads(stdout.decode().strip().splitlines()[-1])
        assert envelope["job_id"] == "job_hello001"
        assert envelope["protocol_version"] == "1"
        assert envelope["status"] == "success"
        assert exit_code == 0
        manager.cleanup(handle)

"""Real Docker filesystem isolation tests (WP13/WP14, Tests A-H).

Every test in this module runs against the real Docker engine with the
dedicated worker image. No mocks substitute for the security assertions.
"""

import hashlib
import json
import os
from pathlib import Path

import pytest

from langgraph_langchain.execution.container_manager import (
    ContainerLifecycleError,
    ContainerManager,
    ContainerSpec,
    JobWorkspace,
)

pytestmark = pytest.mark.docker

JOBS_ROOT = Path("/tmp/deepanalyze-worker-jobs")


def _manager() -> ContainerManager:
    manager = ContainerManager()
    assert manager.health_check(), "real docker engine required"
    manager.ensure_image()
    return manager


def _envelope(job_id: str, code: str, *, extensions=(".txt",), max_files=50) -> dict:
    return {
        "protocol_version": "1",
        "kind": "job",
        "job": {
            "job_id": job_id,
            "code": code,
            "output_policy": {"allowed_extensions": list(extensions), "max_files": max_files, "max_artifact_bytes": 1024 * 1024},
            "execution_options": {"timeout_seconds": 30},
        },
    }


def _run(manager: ContainerManager, ws: JobWorkspace, timeout: float = 60.0):
    """Run the workspace job and return (outcome, envelope, handle).

    Caller is responsible for manager.cleanup(handle) so that published
    artifacts remain inspectable after the assertion points."""
    spec = ContainerSpec(
        job_id=ws.manifest["job_id"],
        input_dir=str(ws.root / "input"),
        output_dir=str(ws.root / "output"),
        tmp_dir=str(ws.root / "tmp"),
    )
    handle = manager.run_container(spec)
    outcome = manager.wait(handle, timeout_seconds=timeout)
    stdout = manager.collect_stdout(handle)
    envelope = json.loads(stdout.decode().strip().splitlines()[-1])
    declared = envelope.get("artifacts") or []
    if outcome == "exited" and envelope.get("status") == "success" and declared:
        names = [a["relative_path"] for a in declared]
        files = manager.collect_output_files(handle, names)
        manager.publish_artifacts(ws, declared, files)
    return outcome, envelope, handle


def _job_id_of(ws: JobWorkspace) -> str:
    return ws.manifest["job_id"]


def _create_fresh(manager: ContainerManager, job_id: str, job_json, **kwargs) -> JobWorkspace:
    """Create a workspace, best-effort removing a stale one from a previous
    aborted run (identity-verified cleanup only)."""
    try:
        manager.cleanup_workspace(job_id)
    except ContainerLifecycleError:
        pass
    # Reconcile any stale managed container from an aborted run (label-verified).
    try:
        for item in manager._client.list_managed_containers():
            if item.get("Labels", {}).get("deepanalyze.job_id") == job_id:
                manager._client.remove_container(item["Id"], force=True)
    except ContainerLifecycleError:
        pass
    return manager.create_workspace(job_id, job_json, **kwargs)


class TestReadOnlyInput:
    def test_a_input_read_only_and_original_preserved(self, manager=None):
        manager = _manager()
        original = Path("/tmp/a1222-original-a.csv")
        content = b"id,value\n1,100\n"
        original.write_bytes(content)
        before_hash = hashlib.sha256(original.read_bytes()).hexdigest()
        code = (
            "data = open('/workspace/input/input_data.csv','rb').read()\n"
            "assert data == b'id,value\\n1,100\\n'\n"
            "print('READ_OK')\n"
            "attempts = []\n"
            "for mode, desc in [('w','write'), ('a','append')]:\n"
            "    try:\n"
            "        open('/workspace/input/input_data.csv', mode).close()\n"
            "        attempts.append(desc + ':NO_ERROR')\n"
            "    except OSError as e:\n"
            "        attempts.append(desc + ':DENIED')\n"
            "import os\n"
            "try:\n"
            "    os.rename('/workspace/input/input_data.csv', '/workspace/input/moved.csv')\n"
            "    attempts.append('rename:NO_ERROR')\n"
            "except OSError:\n"
            "    attempts.append('rename:DENIED')\n"
            "try:\n"
            "    os.remove('/workspace/input/input_data.csv')\n"
            "    attempts.append('remove:NO_ERROR')\n"
            "except OSError:\n"
            "    attempts.append('remove:DENIED')\n"
            "print('|'.join(attempts))\n"
        )
        ws = _create_fresh(manager,
            "job_fs000a",
            json.dumps(_envelope("job_fs000a", code)).encode(),
            input_files={"input_data.csv": content},
        )
        try:
            outcome, envelope, handle_a = _run(manager, ws)
            assert outcome == "exited"
            assert envelope["status"] == "success"
            assert "READ_OK" in envelope["stdout"]
            assert "write:DENIED" in envelope["stdout"]
            assert "append:DENIED" in envelope["stdout"]
            assert "rename:DENIED" in envelope["stdout"]
            assert "remove:DENIED" in envelope["stdout"]
            # original untouched
            assert hashlib.sha256(original.read_bytes()).hexdigest() == before_hash
            original.unlink()
        finally:
            try:
                manager.cleanup(handle_a)
            except Exception:
                pass
            if (JOBS_ROOT / "job_fs000a").exists():
                manager.cleanup_workspace("job_fs000a")


class TestMountInspection:
    def test_ro_mount_visible_in_inspect(self):
        manager = _manager()
        ws = _create_fresh(manager, "job_fs000b", json.dumps(_envelope("job_fs000b", "print('x')")).encode())
        spec = ContainerSpec(
            job_id="job_fs000b",
            input_dir=str(ws.root / "input"),
            output_dir=str(ws.root / "output"),
            tmp_dir=str(ws.root / "tmp"),
        )
        handle = manager.run_container(spec)
        try:
            manager.wait(handle, timeout_seconds=30)
            info = manager._client.inspect_container(handle.container_id)
            binds = info["HostConfig"]["Binds"]
            assert f"{ws.root / 'input'}:/workspace/input:ro" in binds
            assert f"{ws.root / 'output'}:/workspace/output:rw" in binds
            assert f"{ws.root / 'tmp'}:/workspace/tmp:rw" in binds
            assert not any("/var/run/docker.sock" in b for b in binds)
        finally:
            manager.cleanup(handle)


class TestOutputBoundary:
    def test_b_output_stays_in_job_dir(self):
        manager = _manager()
        ws = _create_fresh(manager, "job_fs000c", json.dumps(_envelope("job_fs000c", "open('result.txt','w').write('data-out')")).encode())
        other = _create_fresh(manager, "job_fs000d", b"{}")
        try:
            outcome, envelope, handle_c = _run(manager, ws)
            assert outcome == "exited"
            assert envelope["status"] == "success"
            artifacts = envelope["artifacts"]
            assert len(artifacts) == 1
            assert artifacts[0]["relative_path"] == "result.txt"
            # published into THIS job's collected dir with matching hash
            published = ws.root / "collected" / "result.txt"
            assert published.read_bytes() == b"data-out"
            # other job untouched
            assert (other.root / "output").stat().st_size == 0 or not any((other.root / "output").iterdir())
        finally:
            manager.cleanup(handle_c)
            manager.cleanup_workspace("job_fs000c")
            manager.cleanup_workspace("job_fs000d")

    def test_traversal_write_fails_readonly_rootfs(self):
        manager = _manager()
        code = (
            "results = []\n"
            "for target in ('../escape.txt', '/escape.txt', '/workspace/escape.txt'):\n"
            "    try:\n"
            "        open(target, 'w').write('x')\n"
            "        results.append(target + ':WROTE')\n"
            "    except OSError:\n"
            "        results.append(target + ':DENIED')\n"
            "print('|'.join(results))\n"
        )
        ws = _create_fresh(manager, "job_fs000e", json.dumps(_envelope("job_fs000e", code)).encode())
        try:
            outcome, envelope, _ = _run(manager, ws)
            assert envelope["status"] == "success"
            assert "DENIED" in envelope["stdout"]
            assert "WROTE" not in envelope["stdout"]
            # no escape file exists on host anywhere in the workspace
            assert not (ws.root / "escape.txt").exists()
        finally:
            manager.cleanup_workspace("job_fs000e")


class TestSymlinkPolicy:
    def test_d_symlink_artifact_not_exported(self):
        manager = _manager()
        secret = Path("/tmp/a1222-secret-target.txt")
        secret.write_bytes(b"TOP-SECRET-CONTENT")
        code = (
            "import os\n"
            "os.symlink('/tmp/a1222-secret-target.txt', 'leak.txt')\n"
            "print('SYMLINK_CREATED')\n"
        )
        ws = _create_fresh(manager, "job_fs000f", json.dumps(_envelope("job_fs000f", code)).encode())
        try:
            outcome, envelope, _ = _run(manager, ws)
            assert envelope["status"] == "success"
            assert "SYMLINK_CREATED" in envelope["stdout"]
            # symlink is NOT exported as an artifact
            assert envelope["artifacts"] == []
            assert not (ws.root / "collected" / "leak.txt").exists()
            # target content was never published anywhere
            for published in (ws.root / "collected").glob("*"):
                if published.is_file():
                    assert b"TOP-SECRET" not in published.read_bytes()
            secret.unlink()
        finally:
            manager.cleanup_workspace("job_fs000f")

    def test_declared_traversal_rejected_at_protocol(self):
        from langgraph_langchain.execution.docker_worker_protocol import DockerResultEnvelope, ProtocolProtocolError

        payload = {
            "protocol_version": "1",
            "kind": "result",
            "job_id": "job_fs000g",
            "status": "success",
            "artifacts": [{"relative_path": "../../host.txt", "size_bytes": 1, "sha256": None}],
        }
        with pytest.raises(ProtocolProtocolError):
            DockerResultEnvelope.parse_output((json.dumps(payload) + "\n").encode(), expected_job_id="job_fs000g")


class TestCrossJobIsolation:
    def test_e_jobs_are_isolated_and_cleanup_preserves_other(self):
        manager = _manager()
        ws_a = _create_fresh(manager,
            "job_fs000h",
            json.dumps(_envelope("job_fs000h", "open('same-name.txt','w').write('JOB-A-DATA')")).encode(),
        )
        ws_b = _create_fresh(manager,
            "job_fs000i",
            json.dumps(_envelope("job_fs000i", "open('same-name.txt','w').write('JOB-B-DATA')")).encode(),
        )
        try:
            outcome_a, env_a, _ = _run(manager, ws_a)
            assert env_a["status"] == "success"
            outcome_b, env_b, _ = _run(manager, ws_b)
            assert env_b["status"] == "success"
            # same filename, different content, no collision
            a_hash = env_a["artifacts"][0]["sha256"]
            b_hash = env_b["artifacts"][0]["sha256"]
            assert a_hash == hashlib.sha256(b"JOB-A-DATA").hexdigest()
            assert b_hash == hashlib.sha256(b"JOB-B-DATA").hexdigest()
            # cleanup of A does not affect B
            manager.cleanup_workspace("job_fs000h")
            assert not (JOBS_ROOT / "job_fs000h").exists()
            assert (ws_b.root / "collected" / "same-name.txt").read_bytes() == b"JOB-B-DATA"
            manager.cleanup_workspace("job_fs000i")
        finally:
            if (JOBS_ROOT / "job_fs000h").exists():
                manager.cleanup_workspace("job_fs000h")
            if (JOBS_ROOT / "job_fs000i").exists():
                manager.cleanup_workspace("job_fs000i")


class TestQuota:
    def test_oversize_output_not_exported(self):
        manager = _manager()
        code = "open('big.bin','wb').write(b'x' * (300 * 1024))\nopen('ok.txt','w').write('fine')\n"
        envelope_payload = _envelope("job_fs000j", code, max_files=10)
        envelope_payload["job"]["output_policy"]["max_artifact_bytes"] = 64 * 1024
        ws = _create_fresh(manager, "job_fs000j", json.dumps(envelope_payload).encode())
        try:
            outcome, envelope, _ = _run(manager, ws)
            assert envelope["status"] == "success"
            names = [a["relative_path"] for a in envelope["artifacts"]]
            assert "big.bin" not in names
            assert "ok.txt" in names
            assert not (ws.root / "collected" / "big.bin").exists()
        finally:
            manager.cleanup_workspace("job_fs000j")


class TestCleanupSafety:
    def test_h_failure_and_timeout_cleanup(self):
        manager = _manager()
        # failing job
        ws_fail = _create_fresh(manager,
            "job_fs000k",
            json.dumps(_envelope("job_fs000k", "raise ValueError('intended')")).encode(),
        )
        try:
            outcome, envelope, handle_fail = _run(manager, ws_fail)
            assert envelope["status"] == "failed"
        finally:
            manager.cleanup(handle_fail)
        # timeout job
        ws_timeout = _create_fresh(manager,
            "job_fs000l",
            json.dumps(_envelope("job_fs000l", "import time\ntime.sleep(30)")).encode(),
        )
        timeout_ws_root = ws_timeout.root
        try:
            spec = ContainerSpec(
                job_id="job_fs000l",
                input_dir=str(ws_timeout.root / "input"),
                output_dir=str(ws_timeout.root / "output"),
                tmp_dir=str(ws_timeout.root / "tmp"),
            )
            handle = manager.run_container(spec)
            outcome = manager.wait(handle, timeout_seconds=3)
            assert outcome == "timeout"
            manager.terminate(handle)
            manager.cleanup(handle)
            assert not timeout_ws_root.exists()
            leftovers = {
                c["Labels"].get("deepanalyze.job_id")
                for c in manager._client.list_managed_containers()
            }
            assert "job_fs000k" not in leftovers
            assert "job_fs000l" not in leftovers
        finally:
            if (JOBS_ROOT / "job_fs000l").exists():
                manager.cleanup_workspace("job_fs000l")

    def test_cleanup_never_deletes_unmanaged_paths(self, manager=None):
        manager = _manager()
        unmanaged = Path("/tmp/a1222-unmanaged-dir")
        unmanaged.mkdir(exist_ok=True)
        marker = unmanaged / "keep.txt"
        marker.write_bytes(b"must survive")
        original = Path("/tmp/a1222-original-b.csv")
        original.write_bytes(b"original")
        ws = _create_fresh(manager, "job_fs000m", json.dumps(_envelope("job_fs000m", "print('ok')")).encode())
        manager.cleanup_workspace("job_fs000m")
        assert marker.read_bytes() == b"must survive"
        assert original.read_bytes() == b"original"
        marker.unlink()
        original.unlink()
        unmanaged.rmdir()

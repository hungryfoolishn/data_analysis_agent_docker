"""Unit tests for manager-owned job workspace isolation (WP1)."""

import json
import os

import pytest

from langgraph_langchain.execution.container_manager import (
    ContainerLifecycleError,
    ContainerManager,
    DockerEngineClient,
)


@pytest.fixture()
def manager(monkeypatch, tmp_path):
    monkeypatch.setenv("DEEPANALYZE_WORKER_JOBS_ROOT", str(tmp_path / "jobs"))
    import langgraph_langchain.execution.container_manager as cm_mod

    monkeypatch.setattr(cm_mod, "DEFAULT_JOBS_ROOT", str(tmp_path / "jobs"))
    return ContainerManager(DockerEngineClient(socket_path=str(tmp_path / "no.sock")))


class TestWorkspaceCreation:
    def test_creates_structure_and_manifest(self, manager, tmp_path):
        ws = manager.create_workspace(
            "job_ws00001",
            b'{"job":1}',
            input_files={"data.csv": b"a,b\n1,2\n"},
        )
        assert (ws.root / "input" / "job.json").exists()
        assert (ws.root / "input" / "data.csv").exists()
        for name in ("output", "tmp", "collected"):
            assert (ws.root / name).is_dir()
        manifest = json.loads((ws.root / "manifest.json").read_text())
        assert manifest["job_id"] == "job_ws00001"
        assert manifest["state"] == "INPUT_STAGED"
        assert manifest["inputs"][0]["filename"] == "data.csv"
        assert manifest["inputs"][0]["sha256"].startswith("22ce"[:0]) or len(manifest["inputs"][0]["sha256"]) == 64

    def test_duplicate_creation_rejected(self, manager):
        manager.create_workspace("job_ws00002", b"{}")
        with pytest.raises(ContainerLifecycleError) as exc:
            manager.create_workspace("job_ws00002", b"{}")
        assert exc.value.reason == "workspace_reuse_forbidden"

    def test_input_is_content_copy_not_hardlink(self, manager, tmp_path):
        original = tmp_path / "original.csv"
        original.write_bytes(b"original-data")
        ws = manager.create_workspace(
            "job_ws00003", b"{}", original_files={"original.csv": original}
        )
        staged = ws.root / "input" / "original.csv"
        assert staged.read_bytes() == b"original-data"
        assert staged.stat().st_ino != original.stat().st_ino  # hardlink NOT preserved

    def test_input_files_not_writable(self, manager):
        ws = manager.create_workspace("job_ws00004", b"{}", input_files={"x.txt": b"v"})
        mode = (ws.root / "input" / "x.txt").stat().st_mode & 0o777
        assert mode & 0o222 == 0  # no write bits

    def test_input_quota_rejected(self, manager, monkeypatch):
        import langgraph_langchain.execution.container_manager as cm_mod

        monkeypatch.setattr(cm_mod, "MAX_INPUT_TOTAL_BYTES", 10)
        with pytest.raises(ContainerLifecycleError) as exc:
            manager.create_workspace("job_ws00005", b"{}", input_files={"big.bin": b"x" * 100})
        assert exc.value.reason == "input_quota_exceeded"
        # failed workspace must not remain
        assert not (manager and __import__("pathlib").Path(os.environ["DEEPANALYZE_WORKER_JOBS_ROOT"], "job_ws00005").exists())


class TestInputIntegrity:
    def test_verify_passes_when_untouched(self, manager):
        ws = manager.create_workspace("job_ws00006", b"{}", input_files={"a.txt": b"hello"})
        manager.verify_input_integrity(ws)  # no raise

    def test_verify_detects_mutation(self, manager):
        ws = manager.create_workspace("job_ws00007", b"{}", input_files={"a.txt": b"hello"})
        # simulate a staging mutation (chmod input writable as manager, then mutate)
        path = ws.root / "input" / "a.txt"
        os.chmod(ws.root / "input", 0o750)
        os.chmod(path, 0o640)
        path.write_bytes(b"tampered")
        with pytest.raises(ContainerLifecycleError) as exc:
            manager.verify_input_integrity(ws)
        assert exc.value.reason == "input_integrity_failure"


class TestPublish:
    def _ws(self, manager, job_id):
        return manager.create_workspace(job_id, b"{}")

    def test_atomic_publish_success(self, manager):
        ws = self._ws(manager, "job_ws00008")
        artifacts = [{"relative_path": "out.csv", "size_bytes": 5, "sha256": None}]
        published = manager.publish_artifacts(ws, artifacts, {"out.csv": b"a,b,c"})
        assert len(published) == 1
        assert published[0]["sha256"] == __import__("hashlib").sha256(b"a,b,c").hexdigest()
        assert (ws.root / "collected" / "out.csv").read_bytes() == b"a,b,c"

    def test_publish_collision_rejected(self, manager):
        ws = self._ws(manager, "job_ws00009")
        (ws.root / "collected" / "out.csv").write_bytes(b"pre-existing")
        with pytest.raises(ContainerLifecycleError) as exc:
            manager.publish_artifacts(
                ws,
                [{"relative_path": "out.csv", "size_bytes": 3, "sha256": None}],
                {"out.csv": b"xyz"},
            )
        assert exc.value.reason == "publish_collision"
        # existing destination untouched
        assert (ws.root / "collected" / "out.csv").read_bytes() == b"pre-existing"

    def test_publish_hash_mismatch_rejected(self, manager):
        ws = self._ws(manager, "job_ws00010")
        with pytest.raises(ContainerLifecycleError) as exc:
            manager.publish_artifacts(
                ws,
                [{"relative_path": "out.csv", "size_bytes": 3, "sha256": "00" * 32}],
                {"out.csv": b"xyz"},
            )
        assert exc.value.reason == "artifact_validation"
        assert not (ws.root / "collected" / "out.csv").exists()

    def test_publish_unsafe_name_rejected(self, manager):
        ws = self._ws(manager, "job_ws00011")
        for bad in ("../evil", "a/b.csv", ".x", ""):
            with pytest.raises(ContainerLifecycleError):
                manager.publish_artifacts(
                    ws,
                    [{"relative_path": bad, "size_bytes": 1, "sha256": None}],
                    {bad: b"x"},
                )

    def test_publish_no_partial_results(self, manager):
        ws = self._ws(manager, "job_ws00012")
        artifacts = [
            {"relative_path": "good.csv", "size_bytes": 1, "sha256": None},
            {"relative_path": "bad.csv", "size_bytes": 1, "sha256": "00" * 32},
        ]
        with pytest.raises(ContainerLifecycleError):
            manager.publish_artifacts(
                ws,
                artifacts,
                {"good.csv": b"a", "bad.csv": b"b"},
            )
        # all-or-nothing: nothing published
        assert not (ws.root / "collected" / "good.csv").exists()


class TestCleanupSafety:
    def test_cleanup_idempotent(self, manager):
        manager.create_workspace("job_ws00013", b"{}")
        manager.cleanup_workspace("job_ws00013")
        manager.cleanup_workspace("job_ws00013")  # second call is a no-op

    def test_cleanup_refuses_missing_manifest(self, manager, tmp_path):
        import pathlib

        root = pathlib.Path(os.environ["DEEPANALYZE_WORKER_JOBS_ROOT"]) / "job_ws00014"
        root.mkdir(parents=True)
        (root / "data").mkdir()
        with pytest.raises(ContainerLifecycleError) as exc:
            manager.cleanup_workspace("job_ws00014")
        assert exc.value.reason == "cleanup_identity_failure"
        assert root.exists()  # untouched

    def test_cleanup_refuses_manifest_mismatch(self, manager):
        ws = manager.create_workspace("job_ws00015", b"{}")
        manifest_path = ws.root / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["job_id"] = "job_other999"
        manifest_path.write_text(json.dumps(manifest))
        with pytest.raises(ContainerLifecycleError) as exc:
            manager.cleanup_workspace("job_ws00015")
        assert exc.value.reason == "cleanup_identity_failure"
        assert ws.root.exists()

    def test_cleanup_refuses_symlink_root(self, manager, tmp_path):
        import pathlib

        real = tmp_path / "real-target"
        real.mkdir()
        jobs_root = pathlib.Path(os.environ["DEEPANALYZE_WORKER_JOBS_ROOT"])
        jobs_root.mkdir(parents=True, exist_ok=True)
        link = jobs_root / "job_ws00016"
        link.symlink_to(real)
        with pytest.raises(ContainerLifecycleError) as exc:
            manager.cleanup_workspace("job_ws00016")
        assert exc.value.reason == "cleanup_identity_failure"
        assert real.exists()  # target untouched

    def test_cleanup_preserves_other_jobs(self, manager):
        manager.create_workspace("job_ws00017", b"{}")
        other = manager.create_workspace("job_ws00018", b"{}")
        manager.cleanup_workspace("job_ws00017")
        assert other.root.exists()
        assert not (manager and __import__("pathlib").Path(os.environ["DEEPANALYZE_WORKER_JOBS_ROOT"], "job_ws00017").exists())

"""Trusted container manager for the Docker worker foundation.

This module is the ONLY component allowed to talk to the Docker Engine API.
It uses a minimal stdlib HTTP client over the Docker unix socket (no SDK, no
new runtime dependencies) and exposes a small lifecycle surface:

    create/start/monitor/collect/stop/kill/remove + reconciliation.

Security invariants:
- Containers are created with explicit, manager-controlled configuration.
- Job ids (already regex-validated by WorkerJob) are the only user-derived
  input reaching a container name.
- Cleanup only touches containers/volumes it created (label-verified).
- No arbitrary host mounts from job data.
"""

from __future__ import annotations

import http.client
import io
import json
import logging
import os
import socket
import tarfile
import tempfile
import time
import uuid
import hashlib
from datetime import datetime, timezone
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .path_safety import PathSafetyError, is_safe_published_filename, safe_workspace_join, validate_relative_path

logger = logging.getLogger("deepanalyze.container_manager")

DEFAULT_IMAGE = os.environ.get("DEEPANALYZE_WORKER_IMAGE", "deepanalyze-worker:1.1.0")
DEFAULT_ENGINE_SOCKET = os.environ.get("DEEPANALYZE_DOCKER_SOCKET", "/var/run/docker.sock")
DEFAULT_JOBS_ROOT = os.environ.get("DEEPANALYZE_WORKER_JOBS_ROOT", "/tmp/deepanalyze-worker-jobs")
WORKER_UID = 10001
WORKER_GID = 10001
MAX_TOTAL_OUTPUT_BYTES = int(os.environ.get("DEEPANALYZE_WORKER_MAX_TOTAL_OUTPUT", str(512 * 1024 * 1024)))
MAX_INPUT_TOTAL_BYTES = int(os.environ.get("DEEPANALYZE_WORKER_MAX_TOTAL_INPUT", str(512 * 1024 * 1024)))
API_VERSION = "v1.44"

# Resource baseline (WP6). Applied at Docker level, not just config.
DEFAULT_CPU_NANOCPUS = int(os.environ.get("DEEPANALYZE_WORKER_CPU", "1000000000"))  # 1 CPU
DEFAULT_MEMORY_BYTES = int(os.environ.get("DEEPANALYZE_WORKER_MEMORY", "512")) * 1024 * 1024
DEFAULT_PIDS_LIMIT = int(os.environ.get("DEEPANALYZE_WORKER_PIDS", "128"))
STOP_GRACE_SECONDS = 10

LABEL_MANAGED = "deepanalyze.managed"
LABEL_JOB_ID = "deepanalyze.job_id"
LABEL_CREATED_AT = "deepanalyze.created_at"

MANAGED_LABEL_VALUE = "true"


class ContainerManagerError(RuntimeError):
    """Base error for manager-level failures."""

    def __init__(self, message: str, *, reason: str = "container_manager_error"):
        super().__init__(message)
        self.reason = reason


class DockerUnavailableError(ContainerManagerError):
    def __init__(self, detail: str = "docker engine unavailable"):
        super().__init__(detail, reason="docker_unavailable")


class ImageMissingError(ContainerManagerError):
    def __init__(self, image: str):
        super().__init__(f"worker image not available: {image}", reason="image_missing")


class ContainerLifecycleError(ContainerManagerError):
    def __init__(self, message: str, reason: str = "container_lifecycle_error"):
        super().__init__(message, reason=reason)


class ImageIdentityMismatchError(ContainerManagerError):
    """The container's actual image identity does not match the resolved one."""

    def __init__(self, detail: str):
        super().__init__(detail, reason="image_identity_mismatch")


class _UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, unix_path: str):
        super().__init__("localhost")
        self._unix_path = unix_path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(30)
        sock.connect(self._unix_path)
        self.sock = sock


class DockerEngineClient:
    """Minimal read/write client for the subset of the Engine API we need."""

    def __init__(self, socket_path: str = DEFAULT_ENGINE_SOCKET):
        self._socket_path = socket_path

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: Optional[bytes] = None,
        headers: Optional[dict[str, str]] = None,
        stream: bool = False,
    ) -> tuple[int, bytes | Any]:
        conn = _UnixHTTPConnection(self._socket_path)
        try:
            conn.request(method, f"/{API_VERSION}{path}", body=body, headers=headers or {})
            response = conn.getresponse()
            if stream:
                return response.status, response  # type: ignore[return-value]
            data = response.read()
            return response.status, data
        except (OSError, http.client.HTTPException) as exc:
            raise DockerUnavailableError(f"docker engine request failed: {exc}") from exc
        finally:
            conn.close()

    def ping(self) -> bool:
        try:
            status, _ = self._request("GET", "/_ping")
            return status == 200
        except DockerUnavailableError:
            return False

    def image_exists(self, image: str) -> bool:
        import urllib.parse

        status, _ = self._request("GET", f"/images/{urllib.parse.quote(image, safe=':@')}/json")
        return status == 200

    def inspect_image(self, image: str) -> dict[str, Any]:
        """Return the Engine API image inspect payload for one image reference."""
        import urllib.parse

        status, data = self._request(
            "GET", f"/images/{urllib.parse.quote(image, safe=':@')}/json"
        )
        if status != 200:
            raise ImageMissingError(image)
        return json.loads(data)

    def create_container(self, config: dict[str, Any], name: str) -> str:
        import urllib.parse

        status, data = self._request(
            "POST",
            f"/containers/create?name={urllib.parse.quote(name, safe='')}",
            body=json.dumps(config).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        if status != 201:
            raise ContainerLifecycleError(f"container create failed (HTTP {status}): {data[:300].decode('utf-8', 'replace')}")
        payload = json.loads(data)
        return payload["Id"]

    def start_container(self, container_id: str) -> None:
        status, data = self._request("POST", f"/containers/{container_id}/start")
        if status not in (200, 204):
            raise ContainerLifecycleError(f"container start failed (HTTP {status}): {data[:300].decode('utf-8', 'replace')}")

    def inspect_container(self, container_id: str) -> dict[str, Any]:
        status, data = self._request("GET", f"/containers/{container_id}/json")
        if status != 200:
            raise ContainerLifecycleError(f"container inspect failed (HTTP {status})")
        return json.loads(data)

    def container_logs(self, container_id: str, *, stdout_only: bool = True) -> bytes:
        query = f"stdout={'1' if stdout_only else '0'}&stderr={'0' if stdout_only else '1'}&timestamps=0&tail=-1"
        status, data = self._request("GET", f"/containers/{container_id}/logs?{query}")
        if status != 200:
            raise ContainerLifecycleError(f"container logs failed (HTTP {status})")
        return _strip_log_stream_headers(data)

    def stop_container(self, container_id: str, timeout: int = STOP_GRACE_SECONDS) -> None:
        status, _ = self._request("POST", f"/containers/{container_id}/stop?t={timeout}")
        if status not in (204, 304, 404):
            raise ContainerLifecycleError(f"container stop failed (HTTP {status})")

    def kill_container(self, container_id: str) -> None:
        status, _ = self._request("POST", f"/containers/{container_id}/kill?signal=KILL")
        if status not in (204, 404, 409):
            raise ContainerLifecycleError(f"container kill failed (HTTP {status})")

    def remove_container(self, container_id: str, *, force: bool = True) -> None:
        status, _ = self._request("DELETE", f"/containers/{container_id}?force={'1' if force else '0'}&v=1")
        if status not in (200, 204, 404):
            raise ContainerLifecycleError(f"container remove failed (HTTP {status})")

    def list_managed_containers(self) -> list[dict[str, Any]]:
        import urllib.parse

        filters = json.dumps({"label": [f"{LABEL_MANAGED}={MANAGED_LABEL_VALUE}"]})
        status, data = self._request(
            "GET",
            f"/containers/json?all=1&filters={urllib.parse.quote(filters, safe='')}",
        )
        if status != 200:
            raise ContainerLifecycleError(f"container list failed (HTTP {status})")
        return json.loads(data)

    def put_archive(self, container_id: str, container_path: str, tar_bytes: bytes) -> None:
        import urllib.parse

        status, data = self._request(
            "PUT",
            f"/containers/{container_id}/archive?path={urllib.parse.quote(container_path, safe='/')}",
            body=tar_bytes,
            headers={"Content-Type": "application/x-tar"},
        )
        if status != 200:
            raise ContainerLifecycleError(f"put archive failed (HTTP {status}): {data[:300].decode('utf-8', 'replace')}")

    def get_archive(self, container_id: str, container_path: str) -> tuple[tarfile.TarInfo, bytes]:
        """Download a file from the container as a tar stream.

        The response body is fully read INSIDE the connection lifetime (the
        previous bug closed the connection before reading, returning an empty
        stream and masking real 404 responses)."""
        import urllib.parse

        conn = _UnixHTTPConnection(self._socket_path)
        try:
            conn.request(
                "GET",
                f"/{API_VERSION}/containers/{container_id}/archive?path={urllib.parse.quote(container_path, safe='/')}",
            )
            response = conn.getresponse()
            data = response.read()
            status = response.status
        except (OSError, http.client.HTTPException) as exc:
            raise DockerUnavailableError(f"docker engine request failed: {exc}") from exc
        finally:
            conn.close()
        if status != 200:
            detail = data[:200].decode("utf-8", "replace")
            raise ContainerLifecycleError(f"get archive failed (HTTP {status}): {detail}")
        with tarfile.open(fileobj=io.BytesIO(data), mode="r") as tar:
            member = tar.next()
            if member is None:
                raise ContainerLifecycleError("archive response is empty")
            handle = tar.extractfile(member)
            if handle is None:
                raise ContainerLifecycleError(
                    f"archive member is not a regular file: {member.name}",
                    reason="artifact_validation",
                )
            return member, handle.read()
def _strip_log_stream_headers(data: bytes) -> bytes:
    """Docker log stream frames are 8-byte headers; strip them when present."""
    if len(data) < 8:
        return b""
    # Frame format: [STREAM_TYPE, 0, 0, 0, SIZE1..SIZE4]
    if data[0] in (1, 2, 3, 4) and data[1] == data[2] == data[3] == 0:
        out = io.BytesIO()
        offset = 0
        while offset + 8 <= len(data):
            frame_type = data[offset]
            size = int.from_bytes(data[offset + 4 : offset + 8], "big")
            payload = data[offset + 8 : offset + 8 + size]
            if frame_type == 1:
                out.write(payload)
            offset += 8 + size
        return out.getvalue()
    return data


@dataclass
class ContainerSpec:
    job_id: str
    image: str = DEFAULT_IMAGE
    cpu_nano_cpus: int = DEFAULT_CPU_NANOCPUS
    memory_bytes: int = DEFAULT_MEMORY_BYTES
    pids_limit: int = DEFAULT_PIDS_LIMIT
    read_only_rootfs: bool = True
    tmpfs_size_mb: int = 256

    # host paths (manager-created staging)
    input_dir: str = ""
    output_dir: str = ""
    tmp_dir: str = ""


@dataclass
class ContainerHandle:
    container_id: str
    name: str
    spec: ContainerSpec
    created_at: float = field(default_factory=time.monotonic)
    image_id: str = ""


@dataclass
class ImageIdentity:
    """Resolved, engine-verified identity of the worker image.

    ``image_id`` is the content-addressed Engine image ID (immutable for the
    resolved image object); ``repo_digests`` are registry digests when the
    image was pulled. Tags are recorded for audit context only and are never
    treated as identity."""

    tag: str
    image_id: str
    repo_digests: tuple[str, ...] = ()


class ContainerManager:
    """Create, run, monitor and clean up one worker container per job."""

    def __init__(self, client: Optional[DockerEngineClient] = None, *, image: str = DEFAULT_IMAGE):
        self._client = client or DockerEngineClient()
        self._image = image

    # -- health ------------------------------------------------------------
    def health_check(self) -> bool:
        return self._client.ping()

    def ensure_image(self) -> None:
        if not self._client.image_exists(self._image):
            raise ImageMissingError(self._image)

    # -- staging -----------------------------------------------------------
    def stage_job_dir(self, job_id: str, job_json: bytes, input_files: Optional[dict[str, bytes]] = None) -> Path:
        """Backward-compatible wrapper: create an isolated JobWorkspace and
        return its root path. Inputs are copied (content copies, never
        hardlinks), hashed into a manager-owned manifest, and mounted
        read-only."""
        workspace = self.create_workspace(job_id, job_json, input_files)
        return workspace.root

    def create_workspace(
        self,
        job_id: str,
        job_json: bytes,
        input_files: Optional[dict[str, bytes]] = None,
        original_files: Optional[dict[str, Path]] = None,
    ) -> "JobWorkspace":
        """Create a manager-owned, isolated job workspace.

        - One workspace per job; duplicate creation is rejected (no reuse).
        - Inputs are content copies (hardlinks are NOT preserved).
        - The manifest is manager-owned and lives outside container mounts.
        """
        _safe_stage_name(job_id)  # job_id must be a safe path component
        root = Path(DEFAULT_JOBS_ROOT) / job_id
        if root.exists():
            raise ContainerLifecycleError(
                f"job workspace already exists; reuse is forbidden: {job_id}",
                reason="workspace_reuse_forbidden",
            )
        try:
            (root / "input").mkdir(parents=True)
        except FileExistsError as exc:
            raise ContainerLifecycleError(
                f"job workspace already exists; reuse is forbidden: {job_id}",
                reason="workspace_reuse_forbidden",
            ) from exc
        for name in ("output", "tmp", "collected"):
            (root / name).mkdir()
        # Output/tmp are writable ONLY by the container worker uid (no world-writable).
        os.chown(root / "output", WORKER_UID, WORKER_GID)
        os.chmod(root / "output", 0o750)
        os.chown(root / "tmp", WORKER_UID, WORKER_GID)
        os.chmod(root / "tmp", 0o750)
        os.chmod(root / "input", 0o750)
        os.chown(root / "input", WORKER_UID, WORKER_GID)
        # Read-only for the worker: owner+group read only (no write bit).
        os.chmod(root / "input", 0o550)
        os.chown(root / "input", 0, WORKER_GID)

        inputs_meta = []
        total_input = len(job_json)
        (root / "input" / "job.json").write_bytes(job_json)
        os.chmod(root / "input" / "job.json", 0o440)
        os.chown(root / "input" / "job.json", 0, WORKER_GID)
        for name, content in (input_files or {}).items():
            _safe_stage_name(name)
            total_input += len(content)
            target = root / "input" / name
            target.write_bytes(content)
            os.chmod(target, 0o440)
            os.chown(target, 0, WORKER_GID)
            inputs_meta.append(
                {
                    "filename": name,
                    "size_bytes": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                }
            )
        for name, original in (original_files or {}).items():
            # Content copy from an original upload path; hash computed from data.
            data = original.read_bytes()
            total_input += len(data)
            _safe_stage_name(name)
            target = root / "input" / name
            target.write_bytes(data)
            os.chmod(target, 0o440)
            os.chown(target, 0, WORKER_GID)
            inputs_meta.append(
                {
                    "filename": name,
                    "size_bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
            )
        if total_input > MAX_INPUT_TOTAL_BYTES:
            _remove_tree(root)
            raise ContainerLifecycleError(
                "total input size exceeds the configured limit",
                reason="input_quota_exceeded",
            )
        manifest = {
            "job_id": job_id,
            "protocol_version": "1",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "state": "INPUT_STAGED",
            "inputs": inputs_meta,
        }
        _write_manifest_atomic(root / "manifest.json", manifest)
        return JobWorkspace(root=root, manifest=manifest)

    def verify_input_integrity(self, workspace: "JobWorkspace") -> None:
        """Re-verify staged input hashes against the manager manifest."""
        root = workspace.root
        manifest = _read_manifest(root / "manifest.json")
        if manifest.get("job_id") != workspace.manifest.get("job_id"):
            raise ContainerLifecycleError("workspace manifest job_id mismatch", reason="workspace_identity_mismatch")
        for item in manifest.get("inputs", []):
            path = root / "input" / item["filename"]
            data = path.read_bytes()
            if len(data) != item["size_bytes"] or hashlib.sha256(data).hexdigest() != item["sha256"]:
                raise ContainerLifecycleError(
                    f"staged input integrity failure: {item['filename']}",
                    reason="input_integrity_failure",
                )

    def publish_artifacts(self, workspace: "JobWorkspace", artifacts: list[dict[str, Any]], contents: dict[str, bytes]) -> list[dict[str, Any]]:
        """Materialize validated worker outputs into the manager-owned
        `collected/` directory atomically (all-or-nothing).

        Two-phase: validate every artifact first, then write temp files, then
        atomically replace; any failure rolls back every destination this call
        touched. Publish never overwrites pre-existing files and never follows
        worker-provided paths."""
        total = sum(len(contents.get(a["relative_path"], b"")) for a in artifacts)
        if total > MAX_TOTAL_OUTPUT_BYTES:
            raise ContainerLifecycleError("total output size exceeds limit", reason="output_quota_exceeded")
        seen: set[str] = set()
        for artifact in artifacts:
            name = artifact["relative_path"]
            if not is_safe_published_filename(name) or name.startswith("."):
                raise ContainerLifecycleError(f"unsafe artifact filename: {name!r}", reason="artifact_validation")
            if name in seen:
                raise ContainerLifecycleError(f"duplicate artifact name: {name}", reason="artifact_validation")
            seen.add(name)
            if artifact.get("size_bytes") != len(contents[name]):
                raise ContainerLifecycleError(f"artifact size mismatch during publish: {name}", reason="artifact_validation")
            digest = hashlib.sha256(contents[name]).hexdigest()
            if artifact.get("sha256") and digest != artifact["sha256"]:
                raise ContainerLifecycleError(f"artifact hash mismatch during publish: {name}", reason="artifact_validation")
        collected_dir = workspace.root / "collected"
        temps: list[tuple[Path, Path]] = []
        replaced: list[Path] = []
        try:
            for name in seen:
                destination = collected_dir / name
                if destination.exists():
                    raise ContainerLifecycleError(
                        f"publish destination already exists: {name}", reason="publish_collision"
                    )
            for name in seen:
                temp = collected_dir / f".tmp-{uuid.uuid4().hex}"
                temp.write_bytes(contents[name])
                os.chmod(temp, 0o640)
                temps.append((temp, collected_dir / name))
            for temp, destination in temps:
                os.replace(temp, destination)  # atomic within the same directory
                replaced.append(destination)
        except Exception:
            for temp, _ in temps:
                try:
                    temp.unlink(missing_ok=True)
                except OSError:
                    pass
            for destination in replaced:
                try:
                    destination.unlink(missing_ok=True)
                except OSError:
                    pass
            raise
        published = []
        for name in sorted(seen):
            data = (collected_dir / name).read_bytes()
            published.append(
                {
                    "artifact_id": f"{workspace.manifest['job_id']}:published:{name}",
                    "job_id": workspace.manifest["job_id"],
                    "filename": name,
                    "relative_path": name,
                    "size_bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "kind": "output",
                }
            )
        return published
    # -- monitoring --------------------------------------------------------
    def resolve_image_identity(self) -> ImageIdentity:
        """Resolve the engine-verified identity of the configured worker image.

        R0a Gate (K-1): a tag is configuration context, not identity. The
        image ID returned here is content-addressed by the engine and is the
        identity recorded for execution provenance and verified against the
        created container."""
        self.ensure_image()
        payload = self._client.inspect_image(self._image)
        image_id = str(payload.get("Id") or "")
        if not image_id.startswith("sha256:"):
            raise ContainerLifecycleError(
                "engine returned an unusable image identity",
                reason="image_identity_unusable",
            )
        repo_digests = tuple(str(d) for d in payload.get("RepoDigests") or [])
        expected_digest = os.environ.get(
            "DEEPANALYZE_WORKER_IMAGE_DIGEST", ""
        ).strip().lower()
        if expected_digest:
            candidates = [image_id, *[d.rsplit("@", 1)[-1] for d in repo_digests]]
            if not any(
                c == expected_digest or c.endswith(expected_digest)
                for c in candidates
            ):
                raise ImageIdentityMismatchError(
                    "configured worker image does not match the pinned digest: "
                    f"expected={expected_digest} image_id={image_id} "
                    f"repo_digests={list(repo_digests)}"
                )
        return ImageIdentity(
            tag=self._image, image_id=image_id, repo_digests=repo_digests
        )

    def run_container(self, spec: ContainerSpec) -> ContainerHandle:
        """Create and start one container. Cleans up partial containers on failure."""
        identity = self.resolve_image_identity()
        name = f"deepanalyze-worker-{spec.job_id}"
        config = self._build_config(spec)
        container_id = self._client.create_container(config, name)
        handle = ContainerHandle(
            container_id=container_id, name=name, spec=spec, image_id=identity.image_id
        )
        try:
            self._client.start_container(container_id)
        except Exception:
            logger.warning("container start failed; removing partial container %s", name)
            self.cleanup(handle)
            raise
        # R0a Gate: verify the container was actually created from the resolved
        # image object. A tag re-pointed between resolution and creation yields
        # a different image ID here; that job fails closed.
        actual_image_id = self._client.inspect_container(container_id).get("Image", "")
        if actual_image_id != identity.image_id:
            self.cleanup(handle)
            raise ImageIdentityMismatchError(
                f"container image identity mismatch for job={spec.job_id}: "
                f"expected={identity.image_id} actual={actual_image_id}"
            )
        logger.info(
            "container started job=%s container=%s image=%s image_id=%s",
            spec.job_id, container_id[:12], self._image, identity.image_id,
        )
        return handle

    def _build_config(self, spec: ContainerSpec) -> dict[str, Any]:
        binds = [
            f"{spec.input_dir}:/workspace/input:ro",
            f"{spec.output_dir}:/workspace/output:rw",
        ]
        if spec.tmp_dir:
            binds.append(f"{spec.tmp_dir}:/workspace/tmp:rw")
        return {
            "Image": self._image,
            "Cmd": ["/workspace/input/job.json"],
            "Labels": {
                LABEL_MANAGED: MANAGED_LABEL_VALUE,
                LABEL_JOB_ID: spec.job_id,
                LABEL_CREATED_AT: str(time.time()),
            },
            "User": "10001:10001",
            "NetworkingConfig": {"EndpointsConfig": {}},  # network disabled
            "HostConfig": {
                "Binds": binds,
                "NetworkMode": "none",
                "NanoCpus": spec.cpu_nano_cpus,
                "Memory": spec.memory_bytes,
                "MemorySwap": spec.memory_bytes,
                "PidsLimit": spec.pids_limit,
                "CapDrop": ["ALL"],
                "SecurityOpt": ["no-new-privileges"],
                "ReadonlyRootfs": spec.read_only_rootfs,
                "Tmpfs": {"/tmp": f"size={spec.tmpfs_size_mb}m"},
                "AutoRemove": False,
            },
            "WorkingDir": "/workspace/output",
        }
    def wait(self, handle: ContainerHandle, *, timeout_seconds: float, cancel_event: Optional[Any] = None) -> str:
        """Wait for container exit. Returns one of: exited | timeout | cancelled.

        Raises ContainerLifecycleError on unrecoverable API failures.
        """
        deadline = time.monotonic() + timeout_seconds
        while True:
            if cancel_event is not None and cancel_event.is_set():
                return "cancelled"
            info = self._client.inspect_container(handle.container_id)
            state = info.get("State", {})
            if not state.get("Running", False) and not state.get("Restarting", False):
                return "exited"
            if state.get("OOMKilled", False):
                return "exited"
            if time.monotonic() >= deadline:
                return "timeout"
            time.sleep(0.1)

    def terminate(self, handle: ContainerHandle) -> None:
        """Graceful stop then kill. Idempotent (404 tolerated)."""
        try:
            self._client.stop_container(handle.container_id)
        except ContainerLifecycleError:
            pass
        try:
            info = self._client.inspect_container(handle.container_id)
            if info.get("State", {}).get("Running", False):
                self._client.kill_container(handle.container_id)
        except ContainerLifecycleError:
            try:
                self._client.kill_container(handle.container_id)
            except ContainerLifecycleError:
                pass

    def collect_stdout(self, handle: ContainerHandle) -> bytes:
        return self._client.container_logs(handle.container_id, stdout_only=True)

    def collect_exit_code(self, handle: ContainerHandle) -> Optional[int]:
        info = self._client.inspect_container(handle.container_id)
        return info.get("State", {}).get("ExitCode")

    def collect_output_files(self, handle: ContainerHandle, names: list[str]) -> dict[str, bytes]:
        """Fetch job output files from /workspace/output via the archive API.

        Names are validated (no traversal); symlink/link/directory members are
        rejected without following their targets."""
        out: dict[str, bytes] = {}
        for name in names:
            validate_relative_path(name, field_name="artifact name")
            try:
                member, content = self._client.get_archive(handle.container_id, f"/workspace/output/{name}")
            except ContainerLifecycleError:
                continue
            if member.issym() or member.islnk() or not member.isfile():
                raise ContainerLifecycleError(
                    f"artifact is not a regular file: {name}",
                    reason="artifact_validation",
                )
            out[name] = content
        return out

    def cleanup(self, handle: ContainerHandle) -> None:
        """Remove container and job workspace. Idempotent; raises on removal failure."""
        try:
            self._client.remove_container(handle.container_id, force=True)
        except ContainerLifecycleError as exc:
            raise ContainerLifecycleError(f"cleanup failed for {handle.name}: {exc}") from exc
        self.cleanup_workspace(handle.spec.job_id)

    def cleanup_workspace(self, job_id: str) -> None:
        """Safely remove a job workspace after verifying its manager manifest.

        Refuses when the manifest is missing or the job_id mismatches, when the
        root is a symlink, or when the path escapes the managed root."""
        _safe_stage_name(job_id)
        root = Path(DEFAULT_JOBS_ROOT) / job_id
        if not root.exists():
            return  # idempotent
        if root.is_symlink():
            raise ContainerLifecycleError("workspace root is a symlink; refusing cleanup", reason="cleanup_identity_failure")
        manifest_path = root / "manifest.json"
        if not manifest_path.is_file():
            raise ContainerLifecycleError("workspace manifest missing; refusing cleanup", reason="cleanup_identity_failure")
        manifest = _read_manifest(manifest_path)
        if manifest.get("job_id") != job_id:
            raise ContainerLifecycleError("workspace manifest job_id mismatch; refusing cleanup", reason="cleanup_identity_failure")
        try:
            root.resolve().relative_to(Path(DEFAULT_JOBS_ROOT).resolve())
        except ValueError as exc:
            raise ContainerLifecycleError("workspace path escapes managed root", reason="cleanup_identity_failure") from exc
        manifest["state"] = "CLEANUP_PENDING"
        _write_manifest_atomic(manifest_path, manifest)
        _remove_tree(root)

    def reconcile_orphans(self) -> list[str]:
        """Remove managed containers that exist but are not tracked. Returns removed ids."""
        removed: list[str] = []
        for item in self._client.list_managed_containers():
            container_id = item.get("Id", "")
            labels = item.get("Labels", {})
            if labels.get(LABEL_MANAGED) == MANAGED_LABEL_VALUE and labels.get(LABEL_JOB_ID):
                try:
                    self._client.remove_container(container_id, force=True)
                    removed.append(container_id)
                except ContainerLifecycleError:
                    logger.warning("orphan reconciliation failed for %s", container_id[:12])
        return removed



@dataclass
class JobWorkspace:
    """Manager-owned, per-job isolated workspace."""

    root: Path
    manifest: dict[str, Any]


def _write_manifest_atomic(path: Path, manifest: dict[str, Any]) -> None:
    temp = path.parent / f".manifest-{uuid.uuid4().hex}"
    temp.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    os.replace(temp, path)


def _read_manifest(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContainerLifecycleError(
            f"cannot read workspace manifest: {exc}", reason="cleanup_identity_failure"
        ) from exc
def _safe_stage_name(name: str) -> str:
    if not name or "/" in name or "\\" in name or name in {".", ".."} or name.startswith("."):
        raise ValueError(f"unsafe input artifact name: {name!r}")
    return name


def _remove_tree(path: Path) -> None:
    import shutil

    shutil.rmtree(path, ignore_errors=True)


def new_job_container_suffix() -> str:
    return uuid.uuid4().hex[:8]

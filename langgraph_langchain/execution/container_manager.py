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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("deepanalyze.container_manager")

DEFAULT_IMAGE = os.environ.get("DEEPANALYZE_WORKER_IMAGE", "deepanalyze-worker:1.0.0")
DEFAULT_ENGINE_SOCKET = os.environ.get("DEEPANALYZE_DOCKER_SOCKET", "/var/run/docker.sock")
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
        import urllib.parse

        status, response = self._request(
            "GET",
            f"/containers/{container_id}/archive?path={urllib.parse.quote(container_path, safe='/')}",
            stream=True,
        )
        if status != 200:
            raise ContainerLifecycleError(f"get archive failed (HTTP {status})")
        stream: http.client.HTTPResponse = response  # type: ignore[assignment]
        data = stream.read()
        stream.close()
        with tarfile.open(fileobj=io.BytesIO(data), mode="r") as tar:
            member = tar.next()
            if member is None:
                raise ContainerLifecycleError("archive response is empty")
            return member, tar.extractfile(member).read() if tar.extractfile(member) else b""


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


@dataclass
class ContainerHandle:
    container_id: str
    name: str
    spec: ContainerSpec
    created_at: float = field(default_factory=time.monotonic)


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
        """Create a job-specific host staging dir (input copied, never referenced in place)."""
        root = Path(tempfile.gettempdir()) / f"deepanalyze-worker-{job_id}"
        if root.exists():
            _remove_tree(root)
        input_dir = root / "input"
        output_dir = root / "output"
        input_dir.mkdir(parents=True)
        output_dir.mkdir(parents=True)
        (input_dir / "job.json").write_bytes(job_json)
        for name, content in (input_files or {}).items():
            safe = _safe_stage_name(name)
            (input_dir / safe).write_bytes(content)
        os.chmod(input_dir, 0o555)  # read-only for all users incl. container uid
        os.chmod(output_dir, 0o777)  # writable by the container worker uid (10001)
        return root

    def run_container(self, spec: ContainerSpec) -> ContainerHandle:
        """Create and start one container. Cleans up partial containers on failure."""
        self.ensure_image()
        name = f"deepanalyze-worker-{spec.job_id}"
        config = self._build_config(spec)
        container_id = self._client.create_container(config, name)
        handle = ContainerHandle(container_id=container_id, name=name, spec=spec)
        try:
            self._client.start_container(container_id)
        except Exception:
            logger.warning("container start failed; removing partial container %s", name)
            self.cleanup(handle)
            raise
        logger.info(
            "container started job=%s container=%s image=%s",
            spec.job_id, container_id[:12], self._image,
        )
        return handle

    def _build_config(self, spec: ContainerSpec) -> dict[str, Any]:
        binds = [
            f"{spec.input_dir}:/job/input:ro",
            f"{spec.output_dir}:/job/output:rw",
        ]
        return {
            "Image": self._image,
            "Cmd": ["/job/input/job.json"],
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
        }

    # -- monitoring --------------------------------------------------------
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
        """Fetch job output files from /job/output via the archive API."""
        out: dict[str, bytes] = {}
        for name in names:
            try:
                _, content = self._client.get_archive(handle.container_id, f"/job/output/{name}")
                out[name] = content
            except ContainerLifecycleError:
                continue
        return out

    def cleanup(self, handle: ContainerHandle) -> None:
        """Remove container and staging dir. Idempotent; raises on removal failure."""
        try:
            self._client.remove_container(handle.container_id, force=True)
        except ContainerLifecycleError as exc:
            raise ContainerLifecycleError(f"cleanup failed for {handle.name}: {exc}") from exc
        root = Path(tempfile.gettempdir()) / f"deepanalyze-worker-{handle.spec.job_id}"
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


def _safe_stage_name(name: str) -> str:
    if not name or "/" in name or "\\" in name or name in {".", ".."} or name.startswith("."):
        raise ValueError(f"unsafe input artifact name: {name!r}")
    return name


def _remove_tree(path: Path) -> None:
    import shutil

    shutil.rmtree(path, ignore_errors=True)


def new_job_container_suffix() -> str:
    return uuid.uuid4().hex[:8]

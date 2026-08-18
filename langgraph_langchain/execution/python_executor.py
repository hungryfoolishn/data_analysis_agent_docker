"""A bounded child-process executor for analysis Python code.

The executor deliberately passes only serializable analysis values into the
worker and removes the parent service's environment before evaluating code.
It is process isolation for reliable cancellation, not a replacement for an
OS/container security sandbox.
"""

from __future__ import annotations

import contextlib
import io
import multiprocessing as mp
import os
import pickle
import queue
import signal
import sys
import tempfile
import traceback
from pathlib import Path
from time import monotonic
from typing import Any

from .models import PythonExecutionRequest, PythonExecutionResult


_RESERVED_NAMES = {
    "__builtins__", "WORKSPACE_DIR", "SOURCE_PATH", "Path", "save_fig",
    "load_csv", "safe_workspace_path", "fix_chinese",
}


def _safe_child_path(workspace: Path, filename: str) -> Path:
    candidate = (workspace / filename).resolve()
    try:
        candidate.relative_to(workspace)
    except ValueError as exc:
        raise ValueError("Output path must remain inside WORKSPACE_DIR") from exc
    candidate.parent.mkdir(parents=True, exist_ok=True)
    return candidate


def _load_csv(path: str | Path):
    import pandas as pd

    for encoding in ("utf-8", "utf-8-sig", "gbk", "gb2312", "latin-1"):
        try:
            return pd.read_csv(path, encoding=encoding)
        except UnicodeDecodeError:
            continue
    return pd.read_csv(path, encoding="latin-1")


def _serializable_namespace(namespace: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for name, value in namespace.items():
        if name in _RESERVED_NAMES or callable(value) or name.startswith("__"):
            continue
        try:
            pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL)
        except (pickle.PickleError, TypeError, AttributeError):
            continue
        safe[name] = value
    return safe


def _fork_namespace(namespace: dict[str, Any]) -> dict[str, Any]:
    """Keep analysis helpers when the OS can inherit memory without pickling."""
    return {
        name: value for name, value in namespace.items()
        if name not in _RESERVED_NAMES and not name.startswith("__")
    }


def _trim(text: str, maximum: int) -> str:
    return text if len(text) <= maximum else text[:maximum] + f"\n...[output truncated at {maximum} chars]"


def _inside(path: Path, roots: tuple[Path, ...]) -> bool:
    for root in roots:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def _install_file_audit(workspace: Path, allowed_directories: list[str]) -> None:
    writable_roots = tuple({workspace, *(Path(item).resolve() for item in allowed_directories)})
    trusted_read_roots = tuple(
        root for root in (Path(sys.prefix).resolve(), Path("/usr/share/fonts")) if root.exists()
    )

    def audit(event: str, args: tuple[Any, ...]) -> None:
        if event != "open" or not args or isinstance(args[0], int):
            return
        path = Path(os.fspath(args[0])).resolve()
        mode = str(args[1]) if len(args) > 1 else "r"
        if path.name.lower() == ".env":
            raise PermissionError("Access to environment files is forbidden")
        if _inside(path, writable_roots):
            return
        is_write = any(marker in mode for marker in ("w", "a", "x", "+"))
        if not is_write and _inside(path, trusted_read_roots):
            return
        raise PermissionError(f"File access outside the session workspace is forbidden: {path}")

    sys.addaudithook(audit)


def _workspace_files(workspace: Path) -> dict[Path, tuple[int, int]]:
    return {
        path: (path.stat().st_mtime_ns, path.stat().st_size)
        for path in workspace.rglob("*") if path.is_file()
    }


def _worker(payload: dict[str, Any], result_queue) -> None:
    request = PythonExecutionRequest.model_validate(payload)
    workspace = Path(request.workspace_dir).resolve()
    stdout, stderr = io.StringIO(), io.StringIO()
    started = monotonic()
    try:
        if hasattr(os, "setsid"):
            os.setsid()
        # Do not expose API credentials and service configuration to code steps.
        os.environ.clear()
        os.environ.update(request.environment)
        os.environ.setdefault("MPLBACKEND", "Agg")
        mpl_config = workspace / ".mplconfig"
        temp_dir = workspace / ".tmp"
        mpl_config.mkdir(exist_ok=True)
        temp_dir.mkdir(exist_ok=True)
        os.environ.setdefault("MPLCONFIGDIR", str(mpl_config))
        os.environ.setdefault("TMPDIR", str(temp_dir))
        os.chdir(workspace)
        if request.max_memory_mb:
            try:
                import resource
                memory_bytes = request.max_memory_mb * 1024 * 1024
                resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
            except (ImportError, OSError, ValueError):
                pass
        files_before = _workspace_files(workspace)

        def save_fig(filename: str) -> None:
            target = _safe_child_path(workspace, filename)
            import matplotlib
            matplotlib.use("Agg", force=True)
            import matplotlib.pyplot as plt
            plt.savefig(str(target), bbox_inches="tight", dpi=100)
            plt.close()
            print(f"Saved: {filename}")

        def fix_chinese() -> None:
            import matplotlib as mpl
            mpl.rcParams["axes.unicode_minus"] = False

        namespace = {
            "WORKSPACE_DIR": str(workspace),
            "SOURCE_PATH": request.source_path,
            "Path": Path,
            "save_fig": save_fig,
            "load_csv": _load_csv,
            "safe_workspace_path": lambda filename: _safe_child_path(workspace, filename),
            "fix_chinese": fix_chinese,
            **request.namespace,
        }
        _install_file_audit(workspace, request.allowed_directories)
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exec(compile(request.code, "<agent>", "exec"), namespace, namespace)  # noqa: S102
        namespace_updates = _serializable_namespace(namespace)
        if request.state_output_path:
            state_path = Path(request.state_output_path).resolve()
            if not _inside(state_path, (workspace,)):
                raise ValueError("Namespace state path must remain inside WORKSPACE_DIR")
            with state_path.open("wb") as handle:
                pickle.dump(namespace_updates, handle, protocol=pickle.HIGHEST_PROTOCOL)
            namespace_updates = {}
        files_after = _workspace_files(workspace)
        artifacts = []
        for path, state in files_after.items():
            if files_before.get(path) == state:
                continue
            relative = path.relative_to(workspace)
            if any(part.startswith(".") for part in relative.parts):
                continue
            size = state[1]
            if size > request.max_artifact_bytes:
                path.unlink(missing_ok=True)
                raise OSError(f"Artifact exceeds size limit: {relative}")
            artifacts.append({"relative_path": str(relative), "size_bytes": size})
        result_queue.put({
            "status": "succeeded",
            "stdout": _trim(stdout.getvalue(), request.max_output_chars),
            "stderr": _trim(stderr.getvalue(), request.max_output_chars),
            "namespace_updates": namespace_updates,
            "artifacts": artifacts,
            "duration_ms": (monotonic() - started) * 1000,
        })
    except BaseException as exc:
        traceback.print_exc(file=stderr)
        result_queue.put({
            "status": "failed",
            "stdout": _trim(stdout.getvalue(), request.max_output_chars),
            "stderr": _trim(stderr.getvalue(), request.max_output_chars),
            "error_type": type(exc).__name__,
            "error_message": str(exc),
            "duration_ms": (monotonic() - started) * 1000,
        })


class IsolatedPythonExecutor:
    """Execute one Python step in a process that can be reliably terminated."""

    def __init__(self, *, start_method: str | None = None):
        if start_method is None:
            start_method = "fork" if "fork" in mp.get_all_start_methods() else "spawn"
        self._context = mp.get_context(start_method)
        self._start_method = start_method

    def execute(self, request: PythonExecutionRequest, *, cancel_event: Any = None) -> PythonExecutionResult:
        workspace = Path(request.workspace_dir).resolve()
        workspace.mkdir(parents=True, exist_ok=True)
        state_fd, state_name = tempfile.mkstemp(
            dir=workspace, prefix=".python_state_", suffix=".pkl",
        )
        os.close(state_fd)
        state_path = Path(state_name)
        prepared = request.model_copy(update={
            "namespace": (
                _fork_namespace(request.namespace)
                if self._start_method == "fork"
                else _serializable_namespace(request.namespace)
            ),
            "state_output_path": str(state_path),
        })
        result_queue = self._context.Queue(maxsize=1)
        process = self._context.Process(
            target=_worker,
            args=(prepared.model_dump(), result_queue),
            daemon=True,
        )
        started = monotonic()
        try:
            process.start()
        except BaseException:
            state_path.unlink(missing_ok=True)
            result_queue.close()
            raise
        deadline = started + prepared.timeout_seconds
        cancelled = False
        while process.is_alive() and monotonic() < deadline:
            process.join(timeout=min(0.1, max(0.0, deadline - monotonic())))
            if cancel_event is not None and cancel_event.is_set():
                cancelled = True
                break
        if process.is_alive():
            if hasattr(os, "killpg"):
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            else:
                process.terminate()
            process.join(timeout=5)
            if process.is_alive():
                if hasattr(os, "killpg"):
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                else:
                    process.kill()
                process.join(timeout=2)
            result_queue.close()
            state_path.unlink(missing_ok=True)
            if cancelled:
                return PythonExecutionResult(
                    status="cancelled",
                    error_type="CancelledError",
                    error_message="Execution was cancelled and the worker was terminated",
                    duration_ms=(monotonic() - started) * 1000,
                )
            return PythonExecutionResult(
                status="timed_out",
                error_type="TimeoutError",
                error_message=f"Execution timed out after {prepared.timeout_seconds:g}s",
                duration_ms=(monotonic() - started) * 1000,
            )
        try:
            raw = result_queue.get(timeout=0.5)
        except queue.Empty:
            raw = {
                "status": "failed",
                "error_type": "WorkerExitError",
                "error_message": "Python worker exited without returning a result",
            }
        finally:
            result_queue.close()
        if raw.get("status") == "succeeded" and state_path.exists() and state_path.stat().st_size:
            try:
                with state_path.open("rb") as handle:
                    raw["namespace_updates"] = pickle.load(handle)
            except Exception as exc:
                raw = {
                    "status": "failed",
                    "error_type": "StateTransferError",
                    "error_message": str(exc),
                }
        state_path.unlink(missing_ok=True)
        raw["exit_code"] = process.exitcode
        raw.setdefault("duration_ms", (monotonic() - started) * 1000)
        return PythonExecutionResult.model_validate(raw)

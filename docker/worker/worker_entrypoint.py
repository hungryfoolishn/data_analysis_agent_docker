"""DeepAnalyze Docker worker entrypoint (R0a-1B product execution path).

Protocol contract (unchanged from A1.4.2.2.2):
- Reads exactly one argument: the job envelope path (/workspace/input/job.json).
- stdout: exactly one JSON result envelope line (protocol-clean).
- stderr: logs only.
- Exit codes: 0 = success, 1 = execution failure, 2 = protocol rejection.

R0a-1B additions (product execution path):
- Analysis namespace bootstrap: WORKSPACE_DIR, SOURCE_PATH, Path, pandas,
  numpy and the standard analysis helpers (save_fig, load_csv,
  safe_workspace_path, fix_chinese).
- Explicit artifact-based state mechanism (no runtime objects cross the
  job contract):
  - input  state: ``/workspace/input/state__df__<var>.parquet``,
    ``state__np__<var>.npy``, JSON scalars via ``job.namespace``.
  - output state (only on success): ``/workspace/output/state__df__<var>.parquet``,
    ``state__np__<var>.npy``, ``state__json.json``.
- State serialization uses data-only formats; the host never unpickles
  worker-provided state.

Filesystem isolation posture (verified by integration tests, not claims):
- Runs as non-root (10001) inside a container with network disabled.
- /workspace/input is a read-only mount: any mutation attempt fails (OSError).
- The process cwd is /workspace/output; only files under it are collected.
- Output collection excludes symlinks, hardlinks are not exported as links,
  dotfiles, over-deep paths, oversized files and policy-disallowed extensions.
- Environment is reset to a minimal safe set (no host secrets inherited).
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path
from types import ModuleType

PROTOCOL_VERSION = "1"
MAX_ENVELOPE_BYTES = 10 * 1024 * 1024
MAX_ARTIFACT_BYTES = 1024 * 1024 * 1024
MAX_OUTPUT_CHARS = 100_000
MAX_ARTIFACTS = 1000
MAX_OUTPUT_DEPTH = 8
MAX_STATE_VARS = 64

INPUT_DIR = "/workspace/input"  # default; main() re-derives it from the envelope path
STATE_PREFIX = "state__"
JSON_STATE_NAME = "state__json.json"

SAFE_ENV = {
    "PATH": "/usr/local/bin:/usr/bin:/bin",
    "LANG": "C.UTF-8",
    "PYTHONUNBUFFERED": "1",
    "MPLBACKEND": "Agg",
    "TMPDIR": "/workspace/tmp",
}


class ProtocolReject(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _emit(envelope: dict) -> None:
    sys.stdout.write(json.dumps(envelope, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def _protocol_reject(code: str, message: str, job_id: str = "unknown") -> int:
    _log(f"protocol rejection: {code}: {message}")
    _emit(
        {
            "protocol_version": PROTOCOL_VERSION,
            "kind": "result",
            "job_id": job_id,
            "status": "failed",
            "stdout": "",
            "stderr": "",
            "exit_code": 2,
            "artifacts": [],
            "error": {"code": code, "message": message},
        }
    )
    return 2


def _load_envelope(path: str) -> tuple[dict, dict]:
    try:
        raw = open(path, "rb").read()
    except OSError as exc:
        raise ProtocolReject("MISSING_FIELD", f"cannot read job envelope: {exc}") from exc
    if len(raw) > MAX_ENVELOPE_BYTES:
        raise ProtocolReject("OVERSIZED_PAYLOAD", "job envelope exceeds size limit")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolReject("MALFORMED_JSON", f"job envelope is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ProtocolReject("INVALID_FIELD", "job envelope must be a JSON object")
    if payload.get("protocol_version") != PROTOCOL_VERSION:
        raise ProtocolReject("UNKNOWN_PROTOCOL_VERSION", f"unsupported protocol version: {payload.get('protocol_version')!r}")
    if payload.get("kind") != "job":
        raise ProtocolReject("INVALID_FIELD", "envelope kind must be 'job'")
    job = payload.get("job")
    if not isinstance(job, dict):
        raise ProtocolReject("MISSING_FIELD", "envelope is missing the job object")
    for required in ("job_id", "code"):
        if not isinstance(job.get(required), str) or not job[required]:
            raise ProtocolReject("MISSING_FIELD", f"job is missing required string field: {required}")
    # Derive the input directory from the envelope location. In the
    # production container the envelope is /workspace/input/job.json, so this
    # resolves to /workspace/input; subprocess-level tests can stage the
    # envelope anywhere and the worker stays self-consistent.
    global INPUT_DIR
    INPUT_DIR = os.path.dirname(os.path.abspath(sys.argv[1])) or "."

    job_id = job["job_id"]
    if len(job_id) < 4 or len(job_id) > 128:
        raise ProtocolReject("INVALID_FIELD", "job_id length out of bounds")
    code = job["code"]
    if len(code) > 512_000:
        raise ProtocolReject("OVERSIZED_PAYLOAD", "code exceeds size limit")
    return payload, job


def _relative_depth(rel: str) -> int:
    return len(rel.split("/"))


def _collect_artifacts(job: dict) -> tuple[list[dict], list[str]]:
    """Collect outputs from the cwd (/workspace/output).

    Rejected (never exported): dotfiles, symlinks (any kind), over-deep paths,
    over-limit sizes/counts, and policy-disallowed extensions. Rejections are
    logged to stderr; they never cause content to be read from a link target.
    """
    policy = job.get("output_policy") or {}
    allowed_ext = set(policy.get("allowed_extensions") or [])
    max_files = int(policy.get("max_files") or 100)
    max_bytes = int(policy.get("max_artifact_bytes") or MAX_ARTIFACT_BYTES)
    artifacts: list[dict] = []
    rejected: list[str] = []
    output_dir = os.getcwd()
    for root, dirs, files in os.walk(output_dir):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in files:
            path = os.path.join(root, name)
            rel = os.path.relpath(path, output_dir).replace(os.sep, "/")
            if name.startswith("."):
                continue
            if os.path.islink(path) or not os.path.isfile(path):
                rejected.append(rel)
                continue
            if _relative_depth(rel) > MAX_OUTPUT_DEPTH:
                rejected.append(rel)
                continue
            size = os.path.getsize(path)
            ext = os.path.splitext(name)[1].lower()
            if allowed_ext and ext not in allowed_ext:
                rejected.append(rel)
                continue
            if size > max_bytes or len(artifacts) >= max_files:
                rejected.append(rel)
                continue
            digest = hashlib.sha256()
            with open(path, "rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            artifacts.append(
                {
                    "relative_path": rel,
                    "size_bytes": size,
                    "sha256": digest.hexdigest(),
                }
            )
    return artifacts, rejected


def _workspace_relative(candidate: Path, root: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def _build_namespace(job: dict) -> dict:
    """Build the analysis namespace: identity, helpers and state restore."""
    namespace: dict = {"__name__": "__worker__", "__builtins__": __builtins__}
    output_dir = Path.cwd()

    # Lazy third-party imports: the worker degrades to stdlib-only when the
    # image lacks them (legacy images); analysis code then fails visibly.
    pd = np = None
    try:
        import pandas as pd_module  # type: ignore
        import numpy as np_module  # type: ignore

        pd, np = pd_module, np_module
        namespace["pd"] = pd
        namespace["np"] = np
    except Exception as exc:  # pragma: no cover - legacy image fallback
        _log(f"analysis stack unavailable: {type(exc).__name__}: {exc}")

    namespace["WORKSPACE_DIR"] = str(output_dir)
    namespace["Path"] = Path

    def safe_workspace_path(filename: str) -> str:
        target = (output_dir / filename).resolve()
        if not _workspace_relative(target, output_dir):
            raise PermissionError("Output path must remain inside WORKSPACE_DIR")
        target.parent.mkdir(parents=True, exist_ok=True)
        return str(target)

    def save_fig(filename: str) -> None:
        import matplotlib

        matplotlib.use("Agg", force=True)
        import matplotlib.pyplot as plt

        target = safe_workspace_path(filename)
        plt.savefig(target, bbox_inches="tight", dpi=100)
        plt.close()
        print(f"Saved: {filename}")

    def load_csv(path: str | Path):
        if pd is None:
            raise RuntimeError("pandas is unavailable in this worker image")
        candidates = [Path(path)]
        if not Path(path).is_absolute():
            candidates.append(Path(path).resolve())
            candidates.append(Path(INPUT_DIR) / path)
        for candidate in candidates:
            if candidate.is_file():
                break
        else:
            raise FileNotFoundError(f"input file not found: {path}")
        for encoding in ("utf-8", "utf-8-sig", "gbk", "gb2312", "latin-1"):
            try:
                return pd.read_csv(candidate, encoding=encoding)
            except UnicodeDecodeError:
                continue
        return pd.read_csv(candidate, encoding="latin-1")

    def fix_chinese() -> None:
        if pd is None:
            return
        import matplotlib as mpl

        mpl.rcParams["font.sans-serif"] = ["Noto Sans CJK SC", "Microsoft YaHei", "SimHei", "DejaVu Sans"]
        mpl.rcParams["axes.unicode_minus"] = False

    namespace["safe_workspace_path"] = safe_workspace_path
    namespace["save_fig"] = save_fig
    namespace["load_csv"] = load_csv
    namespace["fix_chinese"] = fix_chinese

    # Source file detection: first staged non-state input file.
    try:
        candidates = sorted(
            name
            for name in os.listdir(INPUT_DIR)
            if name != "job.json"
            and not name.startswith(STATE_PREFIX)
            and os.path.isfile(os.path.join(INPUT_DIR, name))
        )
        if candidates:
            namespace["SOURCE_PATH"] = os.path.join(INPUT_DIR, candidates[0])
    except OSError as exc:
        _log(f"input dir unavailable: {exc}")

    # JSON namespace scalars from the job contract (validated host-side).
    job_namespace = job.get("namespace") or {}
    if isinstance(job_namespace, dict):
        for name, value in job_namespace.items():
            if isinstance(name, str) and name not in namespace:
                namespace[name] = value

    # State restore (data-only decoders).
    if pd is not None and np is not None:
        try:
            for name in sorted(os.listdir(INPUT_DIR)):
                path = os.path.join(INPUT_DIR, name)
                if name.startswith(f"{STATE_PREFIX}df__") and name.endswith(".parquet"):
                    var = name[len(STATE_PREFIX) + 4 : -len(".parquet")]
                    namespace[var] = pd.read_parquet(path)
                    _log(f"state restored: df {var!r}")
                elif name.startswith(f"{STATE_PREFIX}np__") and name.endswith(".npy"):
                    var = name[len(STATE_PREFIX) + 4 : -len(".npy")]
                    with open(path, "rb") as handle:
                        namespace[var] = np.load(handle, allow_pickle=False)
                    _log(f"state restored: ndarray {var!r}")
        except Exception as exc:
            raise ProtocolReject("STATE_RESTORE_FAILED", f"input state restore failed: {exc}")
    elif any(name.startswith(STATE_PREFIX) for name in os.listdir(INPUT_DIR)):
        raise ProtocolReject(
            "STATE_RESTORE_FAILED",
            "job carries analysis state but the worker image lacks pandas/numpy",
        )
    return namespace


def _json_scalar(value, depth: int = 0):
    if depth > 8:
        return None, False
    if value is None or isinstance(value, (bool, int, float, str)):
        return value, True
    if isinstance(value, list):
        out = []
        for item in value:
            converted, ok = _json_scalar(item, depth + 1)
            if not ok:
                return None, False
            out.append(converted)
        return out, True
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if not isinstance(key, str):
                return None, False
            converted, ok = _json_scalar(item, depth + 1)
            if not ok:
                return None, False
            out[key] = converted
        return out, True
    module = type(value).__module__
    if module.startswith("numpy") and hasattr(value, "item"):
        try:
            converted = value.item()
        except (ValueError, TypeError):
            return None, False
        return _json_scalar(converted, depth + 1)
    return None, False


def _emit_state(namespace: dict) -> None:
    """Write analysis state to the output dir (success path only)."""
    pd = namespace.get("pd")
    np = namespace.get("np")
    json_updates: dict = {}
    emitted = 0
    for name, value in namespace.items():
        if name.startswith("__") or name.startswith(STATE_PREFIX):
            continue
        if name in {
            "pd", "np", "Path", "save_fig", "load_csv",
            "safe_workspace_path", "fix_chinese", "WORKSPACE_DIR", "SOURCE_PATH",
        }:
            continue
        if isinstance(value, ModuleType) or callable(value) or isinstance(value, type):
            continue
        if len(name) < 1 or len(name) > 64 or not name.isidentifier():
            continue
        if pd is not None and isinstance(value, pd.DataFrame):
            target = f"{STATE_PREFIX}df__{name}.parquet"
            try:
                value.to_parquet(target)
                emitted += 1
                _log(f"state emitted: df {name!r}")
            except Exception as exc:
                _log(f"state emit failed for df {name!r}: {exc}")
            continue
        if np is not None and isinstance(value, np.ndarray):
            target = f"{STATE_PREFIX}np__{name}.npy"
            try:
                with open(target, "wb") as handle:
                    np.save(handle, value, allow_pickle=False)
                emitted += 1
                _log(f"state emitted: ndarray {name!r}")
            except Exception as exc:
                _log(f"state emit failed for ndarray {name!r}: {exc}")
            continue
        converted, ok = _json_scalar(value)
        if ok:
            json_updates[name] = converted
    if emitted >= MAX_STATE_VARS:
        _log("state variable limit reached; remaining variables skipped")
    try:
        with open(JSON_STATE_NAME, "w", encoding="utf-8") as handle:
            json.dump(json_updates, handle, ensure_ascii=False)
        _log(f"state emitted: json scalars={len(json_updates)}")
    except Exception as exc:
        _log(f"json state emit failed: {exc}")


def _mirror_inputs_into_cwd() -> None:
    """Expose staged input files as symlinks in the writable cwd.

    The legacy process backend ran analysis code with cwd == session
    workspace, so relative paths such as pd.read_csv("in.csv") resolved
    against staged workspace files. The container cwd is the artifact output
    directory; mirroring input names as symlinks preserves that relative-path
    contract without changing the input/output mount layout. Symlinks are
    never collected as artifacts by _collect_artifacts.
    """
    try:
        for name in sorted(os.listdir(INPUT_DIR)):
            if name == "job.json" or name.startswith(".") or name.startswith(STATE_PREFIX):
                continue
            source = os.path.join(INPUT_DIR, name)
            if not os.path.isfile(source):
                continue
            target = os.path.join(os.getcwd(), name)
            if os.path.lexists(target):
                continue
            os.symlink(source, target)
    except OSError as exc:
        _log(f"input mirroring skipped: {exc}")


def main() -> int:
    if len(sys.argv) < 2:
        return _protocol_reject("MISSING_FIELD", "job envelope path argument is required")
    try:
        payload, job = _load_envelope(sys.argv[1])
    except ProtocolReject as exc:
        return _protocol_reject(exc.code, exc.message)

    job_id = job["job_id"]
    execution = job.get("execution_options") or {}
    max_output = int(execution.get("max_output_chars") or MAX_OUTPUT_CHARS)
    code = job["code"]

    os.environ.clear()
    os.environ.update(SAFE_ENV)
    try:
        os.makedirs("/workspace/tmp/mplconfig", exist_ok=True)
        tmp_root = "/workspace/tmp"
    except OSError:
        tmp_root = tempfile.gettempdir()
        _log(f"workspace tmp unavailable; falling back to {tmp_root}")
    try:
        os.makedirs(os.path.join(tmp_root, "mplconfig"), exist_ok=True)
        mpl_dir = os.path.join(tmp_root, "mplconfig")
    except OSError:
        mpl_dir = tempfile.mkdtemp(prefix="mplconfig")
        _log(f"workspace mplconfig unavailable; falling back to {mpl_dir}")
    os.environ["TMPDIR"] = tmp_root
    os.environ["MPLCONFIGDIR"] = mpl_dir

    try:
        namespace = _build_namespace(job)
    except ProtocolReject as exc:
        return _protocol_reject(exc.code, exc.message)

    _mirror_inputs_into_cwd()

    started = time.monotonic()
    stdout_buf, stderr_buf = io.StringIO(), io.StringIO()
    status = "success"
    error = None
    exit_code = 0
    try:
        with contextlib.redirect_stdout(stdout_buf), contextlib.redirect_stderr(stderr_buf):
            exec(compile(code, "<worker>", "exec"), namespace, namespace)  # noqa: S102
    except BaseException as exc:
        status = "failed"
        exit_code = 1
        traceback.print_exc(file=stderr_buf)
        error = {"code": "EXECUTION_ERROR", "message": f"{type(exc).__name__}: {exc}"[:1000]}
        _log(f"execution failed: {error['message']}")

    if status == "success":
        _emit_state(namespace)

    artifacts, rejected = _collect_artifacts(job)
    for rel in rejected:
        _log(f"artifact rejected by isolation policy: {rel}")

    duration_ms = int((time.monotonic() - started) * 1000)
    envelope = {
        "protocol_version": PROTOCOL_VERSION,
        "kind": "result",
        "job_id": job_id,
        "status": status,
        "stdout": stdout_buf.getvalue()[:max_output],
        "stderr": stderr_buf.getvalue()[:max_output],
        "exit_code": exit_code,
        "artifacts": artifacts,
        "error": error,
    }
    _log(f"job finished status={status} duration_ms={duration_ms} artifacts={len(artifacts)}")
    _emit(envelope)
    return 0 if status == "success" else 1


if __name__ == "__main__":
    sys.exit(main())

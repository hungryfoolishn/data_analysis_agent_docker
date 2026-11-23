"""DeepAnalyze Docker worker entrypoint (A1.4.2.2.1 foundation).

Protocol contract:
- Reads exactly one argument: the job envelope path (/job/input/job.json).
- stdout: exactly one JSON result envelope line (protocol-clean).
- stderr: logs only.
- Exit codes: 0 = success, 1 = execution failure, 2 = protocol rejection.

Security posture:
- Runs as non-root (10001) inside a container with network disabled.
- Environment is reset to a minimal safe set (no host secrets inherited).
- Input directory is read-only; artifacts must land under the cwd
  (/job/output) to be collected.
- No pickle, no arbitrary imports of host code, no network access.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import sys
import time
import traceback

PROTOCOL_VERSION = "1"
MAX_ENVELOPE_BYTES = 10 * 1024 * 1024
MAX_ARTIFACT_BYTES = 1024 * 1024 * 1024
MAX_OUTPUT_CHARS = 100_000
MAX_ARTIFACTS = 1000

SAFE_ENV = {
    "PATH": "/usr/local/bin:/usr/bin:/bin",
    "LANG": "C.UTF-8",
    "PYTHONUNBUFFERED": "1",
    "MPLBACKEND": "Agg",
    "TMPDIR": "/tmp",
}


def _log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _emit(envelope: dict) -> None:
    sys.stdout.write(json.dumps(envelope, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def _protocol_reject(code: str, message: str) -> int:
    _log(f"protocol rejection: {code}: {message}")
    _emit(
        {
            "protocol_version": PROTOCOL_VERSION,
            "kind": "result",
            "job_id": "unknown",
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
    if len(sys.argv) < 2:
        raise ProtocolReject("MISSING_FIELD", "job envelope path argument is required")
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
    job_id = job["job_id"]
    if len(job_id) < 4 or len(job_id) > 128:
        raise ProtocolReject("INVALID_FIELD", "job_id length out of bounds")
    code = job["code"]
    if len(code) > 512_000:
        raise ProtocolReject("OVERSIZED_PAYLOAD", "code exceeds size limit")
    return payload, job


class ProtocolReject(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _collect_artifacts(job: dict) -> tuple[list[dict], list[str]]:
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
            if name.startswith("."):
                continue
            path = os.path.join(root, name)
            rel = os.path.relpath(path, output_dir).replace(os.sep, "/")
            if os.path.islink(path):
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


def main() -> int:
    job_id = "unknown"
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
    os.makedirs("/tmp/mplconfig", exist_ok=True)
    os.environ["MPLCONFIGDIR"] = "/tmp/mplconfig"

    started = time.monotonic()
    stdout_buf, stderr_buf = io.StringIO(), io.StringIO()
    status = "success"
    error = None
    exit_code = 0
    try:
        namespace: dict = {"__name__": "__worker__", "__builtins__": __builtins__}
        with contextlib.redirect_stdout(stdout_buf), contextlib.redirect_stderr(stderr_buf):
            exec(compile(code, "<worker>", "exec"), namespace, namespace)  # noqa: S102
    except BaseException as exc:
        status = "failed"
        exit_code = 1
        traceback.print_exc(file=stderr_buf)
        error = {"code": "EXECUTION_ERROR", "message": f"{type(exc).__name__}: {exc}"[:1000]}
        _log(f"execution failed: {error['message']}")

    artifacts, rejected = _collect_artifacts(job)
    for rel in rejected:
        _log(f"artifact rejected by policy: {rel}")

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

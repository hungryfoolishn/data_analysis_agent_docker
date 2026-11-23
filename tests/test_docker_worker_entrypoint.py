"""Entrypoint tests (WP10.2): run worker_entrypoint.py as a real subprocess."""

import json
import subprocess
import sys
import hashlib
from pathlib import Path

import pytest

ENTRYPOINT = Path(__file__).resolve().parents[1] / "docker" / "worker" / "worker_entrypoint.py"


def stage_job(tmp_path: Path, job: dict) -> Path:
    path = tmp_path / "job.json"
    path.write_text(json.dumps({"protocol_version": "1", "kind": "job", "job": job}), encoding="utf-8")
    return path


def make_job(**overrides) -> dict:
    job = {
        "job_id": "job_test0001",
        "code": "result = sum([1, 2, 3, 4, 5])\nprint(result)\n",
        "output_policy": {"allowed_extensions": [".txt"], "max_files": 10, "max_artifact_bytes": 1024 * 1024},
        "execution_options": {"timeout_seconds": 10, "max_output_chars": 3000},
    }
    job.update(overrides)
    return job


def run_entrypoint(tmp_path: Path, job: dict) -> subprocess.CompletedProcess:
    path = stage_job(tmp_path, job)
    return subprocess.run(
        [sys.executable, str(ENTRYPOINT), str(path)],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        timeout=60,
    )


def parse_envelope(proc: subprocess.CompletedProcess) -> dict:
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    assert len(lines) == 1, f"stdout must contain exactly one envelope line, got {lines!r}"
    return json.loads(lines[0])


class TestEntrypoint:
    def test_valid_job_success_stdout_clean(self, tmp_path):
        proc = run_entrypoint(tmp_path, make_job())
        assert proc.returncode == 0
        envelope = parse_envelope(proc)
        assert envelope["job_id"] == "job_test0001"
        assert envelope["status"] == "success"
        assert envelope["protocol_version"] == "1"
        assert "15" in envelope["stdout"]
        assert envelope["error"] is None

    def test_execution_failure_propagated(self, tmp_path):
        proc = run_entrypoint(tmp_path, make_job(code="raise ValueError('boom')\n"))
        assert proc.returncode == 1
        envelope = parse_envelope(proc)
        assert envelope["status"] == "failed"
        assert envelope["error"]["code"] == "EXECUTION_ERROR"
        assert "boom" in envelope["error"]["message"]

    def test_artifact_collected_with_hash(self, tmp_path):
        job = make_job(code="open('out.txt','w').write('hello artifact')\n")
        proc = run_entrypoint(tmp_path, job)
        assert proc.returncode == 0
        envelope = parse_envelope(proc)
        artifacts = envelope["artifacts"]
        assert len(artifacts) == 1
        artifact = artifacts[0]
        assert artifact["relative_path"] == "out.txt"
        assert artifact["size_bytes"] == len(b"hello artifact")
        expected_hash = hashlib.sha256(b"hello artifact").hexdigest()
        assert artifact["sha256"] == expected_hash
        assert (tmp_path / "out.txt").read_text() == "hello artifact"

    def test_disallowed_extension_rejected(self, tmp_path):
        job = make_job(code="open('evil.exe','wb').write(b'MZ')\n")
        proc = run_entrypoint(tmp_path, job)
        envelope = parse_envelope(proc)
        assert envelope["artifacts"] == []

    def test_missing_argument_fails_closed(self, tmp_path):
        proc = subprocess.run([sys.executable, str(ENTRYPOINT)], capture_output=True, text=True, timeout=60)
        assert proc.returncode == 2
        envelope = parse_envelope(proc)
        assert envelope["status"] == "failed"
        assert envelope["error"]["code"] == "MISSING_FIELD"

    def test_malformed_json_fails_closed(self, tmp_path):
        path = tmp_path / "job.json"
        path.write_text("{not json", encoding="utf-8")
        proc = subprocess.run([sys.executable, str(ENTRYPOINT), str(path)], capture_output=True, text=True, timeout=60)
        assert proc.returncode == 2
        envelope = parse_envelope(proc)
        assert envelope["error"]["code"] == "MALFORMED_JSON"

    def test_unknown_version_fails_closed(self, tmp_path):
        payload = json.loads(stage_job(tmp_path, make_job()).read_text())
        payload["protocol_version"] = "42"
        path = tmp_path / "job.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        proc = subprocess.run([sys.executable, str(ENTRYPOINT), str(path)], capture_output=True, text=True, timeout=60)
        assert proc.returncode == 2
        envelope = parse_envelope(proc)
        assert envelope["error"]["code"] == "UNKNOWN_PROTOCOL_VERSION"

    def test_stderr_is_logs_not_protocol(self, tmp_path):
        proc = run_entrypoint(tmp_path, make_job(code="import sys\nsys.stderr.write('debug line')\nprint('out')\n"))
        envelope = parse_envelope(proc)
        assert "debug line" in envelope["stderr"]
        # stdout must remain a single JSON envelope line (no raw log lines)

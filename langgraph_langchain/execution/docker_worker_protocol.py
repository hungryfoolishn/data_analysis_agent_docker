"""Versioned JSON protocol for the Docker worker container.

The protocol is the only trust boundary between the trusted container manager
and the untrusted worker container. It is JSON-only: no pickle, no live Python
objects, no fork namespaces, no environment dumps and no host paths that the
container could interpret as mount requests.
"""

from __future__ import annotations

import json
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .job_contract import WorkerJob

PROTOCOL_VERSION = "1"
PROTOCOL_VERSIONS = (PROTOCOL_VERSION,)

# Hard transport caps enforced before schema validation (fail closed on
# oversized input regardless of content).
MAX_ENVELOPE_BYTES = 10 * 1024 * 1024
MAX_RESULT_BYTES = 10 * 1024 * 1024
MAX_STDOUT_CHARS = 100_000
MAX_STDERR_CHARS = 100_000
MAX_ARTIFACTS = 1000


class ProtocolErrorCode:
    MALFORMED_JSON = "MALFORMED_JSON"
    UNKNOWN_VERSION = "UNKNOWN_PROTOCOL_VERSION"
    MISSING_FIELD = "MISSING_FIELD"
    INVALID_FIELD = "INVALID_FIELD"
    JOB_ID_MISMATCH = "JOB_ID_MISMATCH"
    INVALID_STATUS = "INVALID_STATUS"
    OVERSIZED = "OVERSIZED_PAYLOAD"
    CONTRACT_INVALID = "CONTRACT_INVALID"


class ProtocolError(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str = Field(min_length=1, max_length=64)
    message: str = Field(default="", max_length=2000)


class DockerJobEnvelope(BaseModel):
    """Job envelope sent to the worker container (as /job/input/job.json)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol_version: str
    kind: Literal["job"] = "job"
    job: WorkerJob

    @field_validator("protocol_version")
    @classmethod
    def validate_version(cls, value: str) -> str:
        if value not in PROTOCOL_VERSIONS:
            raise ValueError(f"unsupported protocol version: {value}")
        return value

    def to_json_bytes(self) -> bytes:
        return self.model_dump_json(exclude_none=True).encode("utf-8")

    @classmethod
    def from_json_bytes(cls, raw: bytes) -> "DockerJobEnvelope":
        _check_size(raw, MAX_ENVELOPE_BYTES, ProtocolErrorCode.OVERSIZED, "job envelope")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise _protocol_error(ProtocolErrorCode.MALFORMED_JSON, "job envelope is not valid JSON") from exc
        if not isinstance(payload, dict):
            raise _protocol_error(ProtocolErrorCode.INVALID_FIELD, "job envelope must be a JSON object")
        version = payload.get("protocol_version")
        if version != PROTOCOL_VERSION:
            raise _protocol_error(
                ProtocolErrorCode.UNKNOWN_VERSION,
                f"unsupported protocol version: {version!r}",
            )
        try:
            return cls.model_validate(payload)
        except ValueError as exc:
            raise _protocol_error(ProtocolErrorCode.CONTRACT_INVALID, str(exc)[:500]) from exc


class DockerResultArtifact(BaseModel):
    """Artifact metadata produced by the worker container."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    relative_path: str = Field(min_length=1, max_length=512)
    size_bytes: int = Field(ge=0)
    sha256: Optional[str] = Field(default=None)

    @field_validator("relative_path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        if value.startswith(("/", "\\")) or value.startswith("~"):
            raise ValueError("artifact path must be relative")
        parts = value.replace("\\", "/").split("/")
        if any(part in {"", ".", ".."} for part in parts):
            raise ValueError("artifact path must be normalized and must not traverse")
        return value

    @field_validator("sha256")
    @classmethod
    def validate_sha256(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        import re

        if not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError("sha256 must be a lowercase 64-character hex digest")
        return value


class DockerResultEnvelope(BaseModel):
    """Result envelope produced by the worker container on stdout."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol_version: str
    kind: Literal["result"] = "result"
    job_id: str = Field(min_length=4, max_length=128)
    status: Literal["success", "failed"]
    stdout: str = Field(default="", max_length=MAX_STDOUT_CHARS)
    stderr: str = Field(default="", max_length=MAX_STDERR_CHARS)
    exit_code: int = 0
    artifacts: tuple[DockerResultArtifact, ...] = ()
    error: Optional[ProtocolError] = None

    @field_validator("protocol_version")
    @classmethod
    def validate_version(cls, value: str) -> str:
        if value not in PROTOCOL_VERSIONS:
            raise ValueError(f"unsupported protocol version: {value}")
        return value

    @model_validator(mode="after")
    def validate_status_semantics(self) -> "DockerResultEnvelope":
        if self.status == "failed" and self.error is None:
            raise ValueError("failed result must carry an error")
        if self.status == "success" and self.error is not None:
            raise ValueError("successful result must not carry an error")
        return self

    def to_json_bytes(self) -> bytes:
        return self.model_dump_json(exclude_none=True).encode("utf-8")

    @classmethod
    def parse_output(cls, raw: bytes, expected_job_id: str) -> "DockerResultEnvelope":
        """Parse and validate a worker stdout payload. Fails closed."""
        _check_size(raw, MAX_RESULT_BYTES, ProtocolErrorCode.OVERSIZED, "result output")
        text = raw.decode("utf-8", errors="replace")
        lines = [line for line in text.splitlines() if line.strip()]
        if len(lines) != 1:
            raise _protocol_error(
                ProtocolErrorCode.MALFORMED_JSON,
                "worker stdout must contain exactly one JSON envelope line",
            )
        try:
            payload = json.loads(lines[0])
        except json.JSONDecodeError as exc:
            raise _protocol_error(ProtocolErrorCode.MALFORMED_JSON, "worker stdout is not valid JSON") from exc
        if not isinstance(payload, dict):
            raise _protocol_error(ProtocolErrorCode.INVALID_FIELD, "result envelope must be a JSON object")
        version = payload.get("protocol_version")
        if version != PROTOCOL_VERSION:
            raise _protocol_error(
                ProtocolErrorCode.UNKNOWN_VERSION,
                f"unsupported protocol version: {version!r}",
            )
        try:
            envelope = cls.model_validate(payload)
        except ValueError as exc:
            raise _protocol_error(ProtocolErrorCode.CONTRACT_INVALID, str(exc)[:500]) from exc
        if envelope.job_id != expected_job_id:
            raise _protocol_error(
                ProtocolErrorCode.JOB_ID_MISMATCH,
                "result job_id does not match the submitted job",
            )
        return envelope


def _check_size(raw: bytes, limit: int, code: str, what: str) -> None:
    if len(raw) > limit:
        raise _protocol_error(code, f"{what} exceeds {limit} bytes")


def _protocol_error(code: str, message: str) -> "ProtocolProtocolError":
    return ProtocolProtocolError(code=code, message=message)


class ProtocolProtocolError(Exception):
    """Raised when protocol parsing/validation fails. Fails closed."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message

    def to_error(self) -> ProtocolError:
        return ProtocolError(code=self.code, message=self.message)

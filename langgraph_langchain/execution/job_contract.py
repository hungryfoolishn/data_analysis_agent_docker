"""Serializable worker job and artifact contracts.

This module is the transport protocol for the future worker dispatcher. It
intentionally contains no Python execution logic and never accepts runtime
context, callables, database clients, LLM clients or raw multiprocessing objects.
"""

from __future__ import annotations

import io
import pickle
import re
import uuid
from types import ModuleType
from typing import Any, Literal, Optional, Union
from typing_extensions import TypeAliasType

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# A deliberately closed recursive JSON value type. It prevents callers from
# embedding arbitrary Python objects in the namespace while still allowing the
# simple analysis values that a future container backend can transport.
JsonValue = TypeAliasType(
    "JsonValue",
    Union[
        None,
        bool,
        int,
        float,
        str,
        list["JsonValue"],
        dict[str, "JsonValue"],
    ],
)

WorkerJobVersion = Literal["worker_job.v1"]


def new_job_id() -> str:
    """Generate an opaque, path-safe job id."""
    return f"job_{uuid.uuid4().hex}"


def _validate_relative_path(value: str, *, field_name: str) -> str:
    if not value:
        raise ValueError(f"{field_name} must not be empty")
    if "\x00" in value:
        raise ValueError(f"{field_name} contains an invalid byte")
    if value.startswith("/") or value.startswith("\\"):
        raise ValueError(f"{field_name} must be relative")
    if re.match(r"^[A-Za-z]:", value):
        raise ValueError(f"{field_name} must not be an absolute Windows path")
    parts = value.replace("\\", "/").split("/")
    if any(part == ".." for part in parts):
        raise ValueError(f"{field_name} must not contain path traversal")
    if any(part in {"", "."} for part in parts):
        raise ValueError(f"{field_name} must be a normalized relative path")
    return value


class ArtifactReference(BaseModel):
    """A read-only input artifact referenced relative to a controlled root."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_id: str = Field(min_length=1, max_length=128)
    artifact_type: str = Field(min_length=1, max_length=64)
    relative_path: str = Field(min_length=1, max_length=512)
    size_bytes: Optional[int] = Field(default=None, ge=0)
    sha256: Optional[str] = Field(default=None, description="lowercase SHA-256 hex digest")

    @field_validator("artifact_id", "artifact_type")
    @classmethod
    def validate_ids(cls, value: str) -> str:
        if value.strip() != value or not value:
            raise ValueError("artifact identifiers must not be blank or padded")
        return value

    @field_validator("relative_path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return _validate_relative_path(value, field_name="relative_path")

    @field_validator("sha256")
    @classmethod
    def validate_sha256(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        if not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError("sha256 must be a lowercase 64-character hex digest")
        return value


class ArtifactPolicy(BaseModel):
    """Controlled output policy for worker-generated files."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    output_dir: str = Field(default="output", min_length=1, max_length=512)
    allowed_extensions: tuple[str, ...] = (
        ".csv", ".json", ".txt", ".md", ".png", ".svg", ".parquet", ".xlsx", ".xls", ".pkl", ".pickle", ".npy", ".npz", ".html", ".pdf", ".log", ".zip", ".db", ".sqlite", ".geojson",
    )
    max_files: int = Field(default=100, ge=1, le=1000)
    max_artifact_bytes: int = Field(default=100 * 1024 * 1024, ge=1, le=1024 * 1024 * 1024)

    @field_validator("output_dir")
    @classmethod
    def validate_output_dir(cls, value: str) -> str:
        return _validate_relative_path(value, field_name="output_dir")

    @field_validator("allowed_extensions")
    @classmethod
    def validate_extensions(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("allowed_extensions must not be empty")
        normalized: list[str] = []
        for item in value:
            extension = item.strip().lower()
            if not extension.startswith("."):
                extension = f".{extension}"
            if extension == "." or "/" in extension or "\\" in extension:
                raise ValueError("allowed_extensions must contain simple file extensions")
            if len(extension) > 32:
                raise ValueError("allowed_extensions entries are too long")
            normalized.append(extension)
        return tuple(dict.fromkeys(normalized))


class WorkerExecutionOptions(BaseModel):
    """Structured, serializable execution options.

    allow_network expresses intent only. It does not mean that the current
    process backend enforces network isolation.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_output_chars: int = Field(default=3000, ge=256, le=100000)
    max_artifact_bytes: int = Field(default=100 * 1024 * 1024, ge=1, le=1024 * 1024 * 1024)
    max_artifacts: int = Field(default=100, ge=1, le=1000)
    timeout_seconds: float = Field(default=60.0, gt=0, le=3600)
    max_memory_mb: Optional[int] = Field(default=None, ge=64, le=16384)
    allow_network: bool = False
    environment: dict[str, str] = Field(default_factory=dict)

    @field_validator("environment")
    @classmethod
    def validate_environment(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > 32:
            raise ValueError("environment must contain at most 32 variables")
        forbidden_markers = (
            "API_KEY", "APIKEY", "SECRET", "TOKEN", "PASSWORD", "PASSWD",
            "DATABASE_URL", "DB_URL", "DSN", "CREDENTIAL",
        )
        for key, item in value.items():
            if not key or key.strip() != key or "=" in key:
                raise ValueError("environment keys must be non-empty shell identifiers")
            if any(marker in key.upper() for marker in forbidden_markers):
                raise ValueError(f"environment key is not allowed: {key}")
            if "\x00" in item:
                raise ValueError("environment values contain invalid bytes")
        return value


class WorkerJob(BaseModel):
    """The cross-process and cross-container input protocol for one code job.

    The current process backend additionally needs a controlled workspace path.
    That implementation detail is represented by workspace_dir. A future Docker
    backend must map the same job to container-scoped paths and must not use
    this value as an arbitrary host mount request.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=False)

    schema_version: WorkerJobVersion = "worker_job.v1"
    job_id: str = Field(default_factory=new_job_id, min_length=4, max_length=128)
    session_id: Optional[str] = Field(default=None, max_length=128)
    task_id: Optional[str] = Field(default=None, max_length=128)
    execution_id: Optional[str] = Field(default=None, max_length=128)
    correlation_id: Optional[str] = Field(default=None, max_length=128)

    code: str = Field(min_length=1, max_length=512000)
    workspace_dir: str = Field(min_length=1, max_length=1024)
    source_path: str = ""

    namespace: dict[str, JsonValue] = Field(default_factory=dict)
    input_artifacts: tuple[ArtifactReference, ...] = ()
    output_policy: ArtifactPolicy = Field(default_factory=ArtifactPolicy)
    execution_options: WorkerExecutionOptions = Field(default_factory=WorkerExecutionOptions)

    @field_validator("job_id")
    @classmethod
    def validate_job_id(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", value):
            raise ValueError("job_id may contain only letters, digits, dot, dash and underscore")
        return value

    @field_validator("session_id", "task_id", "execution_id", "correlation_id")
    @classmethod
    def validate_optional_ids(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        if value.strip() != value or not value:
            raise ValueError("optional identifiers must not be blank or padded")
        return value

    @field_validator("workspace_dir")
    @classmethod
    def validate_workspace_dir(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("workspace_dir contains an invalid byte")
        if not value.startswith("/") and not re.match(r"^[A-Za-z]:", value):
            raise ValueError("workspace_dir must be an absolute controlled path")
        if any(part == ".." for part in value.replace("\\", "/").split("/")):
            raise ValueError("workspace_dir must not contain path traversal")
        return value

    @field_validator("source_path")
    @classmethod
    def validate_source_path(cls, value: str) -> str:
        if not value:
            return value
        if "\x00" in value:
            raise ValueError("source_path contains an invalid byte")
        # The current process backend receives an absolute source path. Future
        # container mapping must treat this as a backend path, not a job mount.
        return value

    @model_validator(mode="after")
    def validate_limits(self) -> "WorkerJob":
        if len(self.input_artifacts) > self.execution_options.max_artifacts:
            raise ValueError("input_artifacts exceeds max_artifacts")
        for artifact in self.input_artifacts:
            if artifact.size_bytes is not None and artifact.size_bytes > self.output_policy.max_artifact_bytes:
                raise ValueError("input artifact exceeds max_artifact_bytes")
        return self

    @property
    def timeout_seconds(self) -> float:
        return self.execution_options.timeout_seconds

    def safe_payload(self) -> dict:
        """Return a log-safe payload without code or environment values."""
        import hashlib
        payload = self.model_dump(mode="json", exclude={"code"})
        payload["code_sha256"] = hashlib.sha256(self.code.encode("utf-8")).hexdigest()
        payload["code_length"] = len(self.code)
        return payload

    def __repr_args__(self):
        for key, value in super().__repr_args__():
            if key == "code":
                yield key, "<redacted>"
            else:
                yield key, value


class ProcessWorkerJob(WorkerJob):
    """Process-backend compatibility extension of the portable WorkerJob.

    The portable WorkerJob keeps namespace JSON-safe. The current process
    executor intentionally uses fork memory to preserve closures, modules and
    analysis objects across calls. This subclass is therefore process-only and
    is not accepted by a container backend; A1.4.2.2 must replace it with an
    explicit artifact-based state mechanism.
    """

    namespace: dict[str, Any] = Field(default_factory=dict)



def build_process_worker_job(
    request,
    *,
    session_id: str | None = None,
    task_id: str | None = None,
    execution_id: str | None = None,
) -> ProcessWorkerJob:
    """Build a process-only job from the existing PythonExecutionRequest."""
    return ProcessWorkerJob(
        session_id=session_id,
        task_id=task_id,
        execution_id=execution_id,
        code=request.code,
        workspace_dir=request.workspace_dir,
        source_path=request.source_path,
        namespace=request.namespace,
        execution_options=WorkerExecutionOptions(
            timeout_seconds=request.timeout_seconds,
            max_output_chars=request.max_output_chars,
            max_artifact_bytes=request.max_artifact_bytes,
            max_memory_mb=request.max_memory_mb,
            environment=request.environment,
        ),
    )

"""Transport-safe models for Python execution in a worker process."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class PythonExecutionRequest(BaseModel):
    code: str
    workspace_dir: str
    source_path: str = ""
    namespace: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: float = Field(default=60.0, gt=0.0)
    max_output_chars: int = Field(default=3000, ge=256)
    environment: dict[str, str] = Field(default_factory=dict)
    input_asset_ids: list[str] = Field(default_factory=list)
    allowed_directories: list[str] = Field(default_factory=list)
    max_memory_mb: int | None = Field(default=None, gt=0)
    max_artifact_bytes: int = Field(default=100 * 1024 * 1024, gt=0)
    state_output_path: str = ""


class PythonExecutionResult(BaseModel):
    status: Literal["succeeded", "failed", "timed_out", "cancelled", "blocked"]
    stdout: str = ""
    stderr: str = ""
    error_type: str | None = None
    error_message: str | None = None
    exit_code: int | None = None
    duration_ms: float = 0.0
    namespace_updates: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)

    @property
    def output(self) -> str:
        text = self.stdout
        if self.stderr:
            text = f"{text}\n{self.stderr}" if text else self.stderr
        return text

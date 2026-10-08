"""Executable tool registry models for Runtime V10.2."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()



class ToolExecutionStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    NOT_EXECUTABLE = "NOT_EXECUTABLE"


class ToolExecutionRequest(BaseModel):
    request_id: str = Field(default_factory=lambda: f"tool_request_{uuid4().hex}")
    tool_id: str
    payload: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolExecutionResult(BaseModel):
    request_id: str
    tool_id: str
    status: ToolExecutionStatus
    output: Any = None
    error: str | None = None
    started_at: str
    finished_at: str
    duration_ms: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)

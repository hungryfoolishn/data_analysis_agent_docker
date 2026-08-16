"""Stable, domain-neutral models for analysis tasks and executions."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


class AnalysisTask(BaseModel):
    task_id: str = Field(default_factory=lambda: new_id("task"))
    session_id: str
    question: str
    input_asset_ids: list[str] = Field(default_factory=list)
    constraints: dict[str, Any] = Field(default_factory=dict)
    external_context: Optional[dict[str, Any]] = None
    created_at: str = Field(default_factory=utc_now)


class RuntimePlanStep(BaseModel):
    """A runtime step, distinct from the legacy code-generation PlanStep."""

    step_id: str = Field(default_factory=lambda: new_id("step"))
    objective: str
    method: str
    required_inputs: list[str] = Field(default_factory=list)
    expected_outputs: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    status: Literal[
        "pending", "running", "paused", "succeeded", "needs_revision", "failed", "skipped"
    ] = "pending"
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    error: Optional[str] = None


class PlanRevision(BaseModel):
    """Immutable snapshot of one human- or system-authored plan version."""

    version: int = Field(ge=1)
    reason: str
    revised_by: str = "user"
    created_at: str = Field(default_factory=utc_now)
    steps: list[dict[str, Any]] = Field(default_factory=list)


class PlanConfirmation(BaseModel):
    version: int = Field(ge=1)
    confirmed_by: str = "user"
    note: Optional[str] = None
    confirmed_at: str = Field(default_factory=utc_now)


class AnalysisRun(BaseModel):
    run_id: str = Field(default_factory=lambda: new_id("run"))
    task_id: str
    plan_version: int = 1
    status: Literal[
        "pending", "running", "paused", "awaiting_confirmation", "completed", "failed", "cancelled"
    ] = "running"
    plan_status: Literal[
        "active", "paused", "awaiting_confirmation", "confirmed", "completed"
    ] = "active"
    current_step_id: Optional[str] = None
    steps: list[RuntimePlanStep] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)
    parent_run_id: Optional[str] = None
    retry_of_step_id: Optional[str] = None
    attempt: int = Field(default=1, ge=1)
    pause_reason: Optional[str] = None
    paused_at: Optional[str] = None
    plan_revisions: list[PlanRevision] = Field(default_factory=list)
    plan_confirmation: Optional[PlanConfirmation] = None


class ExecutionResult(BaseModel):
    execution_id: str = Field(default_factory=lambda: new_id("exec"))
    run_id: str
    step_id: Optional[str] = None
    tool_name: str
    status: Literal["succeeded", "needs_revision", "failed", "cancelled"]
    code_or_query: Optional[str] = None
    input_asset_ids: list[str] = Field(default_factory=list)
    output_artifact_ids: list[str] = Field(default_factory=list)
    stdout_preview: str = ""
    error: Optional[dict[str, Any]] = None
    duration_ms: float = 0.0
    created_at: str = Field(default_factory=utc_now)


class RuntimeArtifact(BaseModel):
    artifact_id: str = Field(default_factory=lambda: new_id("artifact"))
    artifact_type: Literal["chart", "table", "report", "code", "file", "data"]
    name: str
    path: str
    relative_path: str
    url: str
    execution_id: Optional[str] = None
    step_id: Optional[str] = None
    input_asset_ids: list[str] = Field(default_factory=list)
    created_by_tool: Optional[str] = None
    content_hash: Optional[str] = None
    created_at: str = Field(default_factory=utc_now)

    def to_legacy_dict(self) -> dict[str, Any]:
        """Return fields understood by the existing SSE and frontend code."""
        return self.model_dump()

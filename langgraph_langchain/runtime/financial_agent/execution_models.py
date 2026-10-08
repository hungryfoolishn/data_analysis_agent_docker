"""Executable tool registry models for Runtime V10.2."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from .models import ExecutionStatus, FailurePolicy, PlanStatus, VerificationStatus


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


class ExecutionPlanStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"


class ExecutionTask(BaseModel):
    task_id: str
    plan_task_id: str | None = None
    tool_id: str
    tool_version: str
    company_id: str
    company_name: str
    period: str
    metric_id: str
    metric_name: str
    dependencies: list[str] = Field(default_factory=list)
    required_data: list[str] = Field(default_factory=list)
    inputs: dict[str, Any] = Field(default_factory=dict)
    status: ExecutionStatus = ExecutionStatus.PENDING
    verification_status: str = "REQUIRED"
    status_reason: str | None = None
    missing_fields: list[str] = Field(default_factory=list)
    output_refs: list[str] = Field(default_factory=list)
    failure_policy: FailurePolicy = FailurePolicy.CONTINUE_WITH_WARNING
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExecutionPlan(BaseModel):
    execution_id: str = Field(default_factory=lambda: f"execution_{uuid4().hex}")
    plan_id: str
    query_id: str
    version: str = "financial-execution-plan-v1"
    status: ExecutionPlanStatus = ExecutionPlanStatus.PENDING
    tasks: list[ExecutionTask] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=_utc_now)
    started_at: str | None = None
    finished_at: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def pending_tasks(self) -> list[ExecutionTask]:
        return [task for task in self.tasks if task.status == ExecutionStatus.PENDING]

    @property
    def unavailable_tasks(self) -> list[ExecutionTask]:
        return [
            task for task in self.tasks
            if task.status == ExecutionStatus.UNAVAILABLE
        ]


class RuntimeExecutionStatus(str, Enum):
    SUCCEEDED = "SUCCEEDED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class RuntimeTaskResult(BaseModel):
    execution_task_id: str
    company_id: str
    company_name: str
    period: str
    metric_id: str
    status: ExecutionStatus
    verification_status: VerificationStatus = VerificationStatus.NOT_REQUIRED
    calculation_id: str | None = None
    verification_id: str | None = None
    evidence_id: str | None = None
    status_reason: str | None = None
    error: str | None = None
    output_refs: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RuntimeExecutionResult(BaseModel):
    execution_id: str
    plan_id: str
    query_id: str
    status: RuntimeExecutionStatus
    task_results: list[RuntimeTaskResult] = Field(default_factory=list)
    succeeded_count: int = 0
    unavailable_count: int = 0
    failed_count: int = 0
    analysis_result: Any = None
    report_markdown: str = ""
    started_at: str = Field(default_factory=_utc_now)
    finished_at: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

"""Stable models for the V10 financial agent planning layer."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class FinancialPlanTaskType(str, Enum):
    RESOLVE_COMPANIES = "RESOLVE_COMPANIES"
    RESOLVE_PERIODS = "RESOLVE_PERIODS"
    VALIDATE_DATA_COVERAGE = "VALIDATE_DATA_COVERAGE"
    REVENUE_TREND = "REVENUE_TREND"
    PROFIT_TREND = "PROFIT_TREND"
    PROFITABILITY_ANALYSIS = "PROFITABILITY_ANALYSIS"
    CASHFLOW_ANALYSIS = "CASHFLOW_ANALYSIS"
    SOLVENCY_ANALYSIS = "SOLVENCY_ANALYSIS"
    OPERATING_ANALYSIS = "OPERATING_ANALYSIS"
    RISK_DETECTION = "RISK_DETECTION"
    PEER_COMPARISON = "PEER_COMPARISON"
    VERIFY_CALCULATIONS = "VERIFY_CALCULATIONS"
    BUILD_FINDINGS = "BUILD_FINDINGS"
    GENERATE_REPORT = "GENERATE_REPORT"


class PlanStatus(str, Enum):
    DRAFT = "DRAFT"
    VALIDATED = "VALIDATED"
    INVALID = "INVALID"
    READY = "READY"
    RUNNING = "RUNNING"
    PARTIALLY_COMPLETED = "PARTIALLY_COMPLETED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"


class ExecutionStatus(str, Enum):
    PENDING = "PENDING"
    READY = "READY"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    SKIPPED = "SKIPPED"
    NOT_EXECUTABLE = "NOT_EXECUTABLE"
    CANCELLED = "CANCELLED"


class VerificationStatus(str, Enum):
    NOT_REQUIRED = "NOT_REQUIRED"
    REQUIRED = "REQUIRED"
    PENDING = "PENDING"
    PASSED = "PASSED"
    FAILED = "FAILED"
    UNAVAILABLE = "UNAVAILABLE"


class FailurePolicy(str, Enum):
    FAIL_FAST = "FAIL_FAST"
    CONTINUE_WITH_WARNING = "CONTINUE_WITH_WARNING"
    REPLAN_OR_FAIL = "REPLAN_OR_FAIL"


class CompanyResolution(BaseModel):
    company_name: str
    company_id: str
    stock_code: str = ""
    resolution: str = "resolved"


class PeriodResolution(BaseModel):
    start_year: int
    end_year: int
    period_type: str = "ANNUAL"


class FinancialToolSpec(BaseModel):
    tool_id: str
    name: str
    version: str
    input_schema: str
    output_schema: str
    capabilities: list[str] = Field(default_factory=list)
    supported_task_types: list[FinancialPlanTaskType] = Field(default_factory=list)
    deterministic: bool = True
    requires_verification: bool = False


class FinancialTaskUnderstanding(BaseModel):
    query_id: str
    raw_question: str
    task_type: str
    companies: list[CompanyResolution] = Field(default_factory=list)
    period_range: PeriodResolution | None = None
    objectives: list[str] = Field(default_factory=list)
    required_metrics: list[str] = Field(default_factory=list)
    comparison_enabled: bool = False
    ambiguities: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    confidence: float = 0.0


class FinancialPlanTask(BaseModel):
    task_id: str
    task_type: FinancialPlanTaskType
    name: str
    dependencies: list[str] = Field(default_factory=list)
    required_data: list[str] = Field(default_factory=list)
    required_metrics: list[str] = Field(default_factory=list)
    selected_tool: str | None = None
    tool_version: str | None = None
    execution_status: ExecutionStatus = ExecutionStatus.PENDING
    verification_status: VerificationStatus = VerificationStatus.NOT_REQUIRED
    output_refs: list[str] = Field(default_factory=list)
    failure_policy: FailurePolicy = FailurePolicy.FAIL_FAST
    metadata: dict[str, Any] = Field(default_factory=dict)


class FinancialPlan(BaseModel):
    plan_id: str
    query_id: str
    version: str = "financial-plan-v1"
    status: PlanStatus = PlanStatus.DRAFT
    tasks: list[FinancialPlanTask] = Field(default_factory=list)
    validation_errors: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

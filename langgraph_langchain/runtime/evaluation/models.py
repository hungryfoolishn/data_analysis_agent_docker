"""Stable models for Runtime V8.5 golden evaluation."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

from langgraph_langchain.runtime.models import utc_now


def _eval_id() -> str:
    return f"evalrun_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}"


class MetricExpectation(BaseModel):
    """One deterministic numeric answer in a golden case."""

    metric: str
    value: float
    tolerance: float = Field(default=0.001, ge=0)
    period: Optional[str] = None
    dimension: Optional[str] = None
    group: Optional[str] = None
    filters: dict[str, Any] = Field(default_factory=dict)
    unit: Optional[str] = None
    description: Optional[str] = None


class GoldenCase(BaseModel):
    case_id: str
    name: str
    question: str
    dataset: str
    dataset_sha256: Optional[str] = None
    task_type: str = "metric"
    executor_type: str = "structured"
    skill_name: Optional[str] = None
    expected: Optional[MetricExpectation] = None
    expected_metrics: list[MetricExpectation] = Field(default_factory=list)
    required_evidence: bool = True
    minimum_verified_evidence: int = Field(default=1, ge=0)
    required_sql: bool = False
    required_finding: bool = True
    required_report: bool = True
    report_must_contain: list[str] = Field(default_factory=list)
    report_must_not_contain: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    critical: bool = False

    @model_validator(mode="after")
    def _merge_legacy_expected(self) -> "GoldenCase":
        if self.expected is not None:
            self.expected_metrics.append(self.expected)
            self.expected = None
        if not self.expected_metrics:
            raise ValueError(f"Golden case {self.case_id} has no expected metrics")
        return self


class MetricAnswer(BaseModel):
    """One contextual numeric answer supplied by the candidate run."""

    metric: str
    value: float
    period: Optional[str] = None
    dimension: Optional[str] = None
    group: Optional[str] = None
    filters: dict[str, Any] = Field(default_factory=dict)


class MetricComparison(BaseModel):
    metric: str
    expected: float
    actual: Optional[float]
    tolerance: float
    absolute_error: Optional[float] = None
    passed: bool
    period: Optional[str] = None
    dimension: Optional[str] = None
    group: Optional[str] = None
    filters: dict[str, Any] = Field(default_factory=dict)
    message: str = ""


class EvaluationScore(BaseModel):
    execution_score: float = Field(default=0.0, ge=0, le=1)
    verification_score: float = Field(default=0.0, ge=0, le=1)
    numeric_score: float = Field(default=0.0, ge=0, le=1)
    evidence_score: float = Field(default=0.0, ge=0, le=1)
    finding_score: float = Field(default=0.0, ge=0, le=1)
    report_score: float = Field(default=0.0, ge=0, le=1)
    latency_score: Optional[float] = Field(default=None, ge=0, le=1)
    cost_score: Optional[float] = Field(default=None, ge=0, le=1)
    total_score: float = Field(default=0.0, ge=0, le=1)


class GoldenCandidateResult(BaseModel):
    """Normalized output submitted to the offline golden evaluator."""

    case_id: str
    run_id: str = "golden_candidate"
    status: str = "succeeded"
    verification_passed: bool = False
    metric_answers: list[MetricAnswer] = Field(default_factory=list)
    evidence_count: int = Field(default=0, ge=0)
    verified_evidence_count: int = Field(default=0, ge=0)
    finding_count: int = Field(default=0, ge=0)
    sql_text: str = ""
    report_text: str = ""
    duration_ms: float = Field(default=0.0, ge=0)
    skill_id: Optional[str] = None
    skill_name: Optional[str] = None
    skill_version: Optional[str] = None
    skill_hash: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CaseEvaluation(BaseModel):
    evaluation_id: str = Field(default_factory=lambda: f"case_eval_{uuid4().hex}")
    case_id: str
    run_id: str
    task_type: str
    executor_type: str
    skill_name: Optional[str] = None
    skill_id: Optional[str] = None
    skill_version: Optional[str] = None
    skill_hash: Optional[str] = None
    passed: bool
    score: EvaluationScore
    metric_comparisons: list[MetricComparison] = Field(default_factory=list)
    failures: list[str] = Field(default_factory=list)
    duration_ms: float = Field(default=0.0, ge=0)
    evaluated_at: str = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvaluationRun(BaseModel):
    run_id: str = Field(default_factory=_eval_id)
    total_cases: int = Field(default=0, ge=0)
    passed_cases: int = Field(default=0, ge=0)
    failed_cases: int = Field(default=0, ge=0)
    pass_rate: float = Field(default=0.0, ge=0, le=1)
    average_score: float = Field(default=0.0, ge=0, le=1)
    by_task_type: dict[str, float] = Field(default_factory=dict)
    by_executor: dict[str, float] = Field(default_factory=dict)
    by_skill: dict[str, float] = Field(default_factory=dict)
    failures: list[CaseEvaluation] = Field(default_factory=list)
    evaluations: list[CaseEvaluation] = Field(default_factory=list)
    generated_at: str = Field(default_factory=utc_now)

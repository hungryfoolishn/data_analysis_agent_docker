"""Stable models for deterministic analysis-quality evaluation."""

from __future__ import annotations

from collections import Counter
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from langgraph_langchain.runtime.models import utc_now


EvaluationCategory = Literal[
    "calculation",
    "evidence",
    "overclaim",
    "workflow",
    "report",
    "artifact",
    "coverage",
]


class FactAssertion(BaseModel):
    """A fact that must be recoverable from structured run output."""

    assertion_id: str
    description: str
    selector: str
    expected: Any
    category: EvaluationCategory = "calculation"
    absolute_tolerance: float = Field(default=0.0, ge=0.0)
    relative_tolerance: float = Field(default=0.0, ge=0.0)
    required: bool = True


class ForbiddenClaim(BaseModel):
    """A regular expression that must not occur in the final report."""

    assertion_id: str
    pattern: str
    reason: str
    category: EvaluationCategory = "overclaim"
    flags: str = "ignorecase"


class ArtifactAssertion(BaseModel):
    assertion_id: str
    description: str
    artifact_type: Optional[str] = None
    name_pattern: Optional[str] = None
    minimum_count: int = Field(default=1, ge=0)
    require_content_hash: bool = False
    category: EvaluationCategory = "artifact"


class LineageAssertion(BaseModel):
    assertion_id: str
    description: str
    finding_selector: str = "findings"
    require_execution_id: bool = True
    require_input_asset: bool = True
    require_artifact_or_stats: bool = True
    category: EvaluationCategory = "evidence"


class EvaluationCase(BaseModel):
    case_id: str
    name: str
    question: str
    data_files: list[str] = Field(default_factory=list)
    data_hashes: dict[str, str] = Field(default_factory=dict)
    required_metrics: list[str] = Field(default_factory=list)
    required_dimensions: list[str] = Field(default_factory=list)
    required_time_windows: list[str] = Field(default_factory=list)
    allowed_limitations: list[str] = Field(default_factory=list)
    facts: list[FactAssertion] = Field(default_factory=list)
    forbidden_claims: list[ForbiddenClaim] = Field(default_factory=list)
    artifacts: list[ArtifactAssertion] = Field(default_factory=list)
    lineage: list[LineageAssertion] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    critical: bool = False


class EvaluationRunInput(BaseModel):
    """A normalized result. It can be built from a persisted run snapshot."""

    run_id: str
    status: str
    report_text: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    findings: list[dict[str, Any]] = Field(default_factory=list)
    metrics: list[dict[str, Any]] = Field(default_factory=list)
    duration_seconds: Optional[float] = None
    token_usage: Optional[dict[str, int]] = None
    model: Optional[str] = None
    prompt_version: Optional[str] = None
    code_version: Optional[str] = None
    data_hashes: dict[str, str] = Field(default_factory=dict)

    @classmethod
    def from_snapshot(cls, snapshot: dict[str, Any]) -> "EvaluationRunInput":
        run = snapshot.get("run") or {}
        task = snapshot.get("task") or {}
        report = snapshot.get("report") or snapshot.get("report_markdown") or ""
        if isinstance(report, dict):
            report = report.get("markdown") or report.get("content") or ""
        assets = snapshot.get("assets") or []
        return cls(
            run_id=str(run.get("run_id") or snapshot.get("run_id") or "unknown"),
            status=str(run.get("status") or snapshot.get("status") or "unknown"),
            report_text=str(report),
            payload=snapshot,
            artifacts=snapshot.get("artifacts") or [],
            findings=snapshot.get("findings") or [],
            metrics=snapshot.get("metric_definitions") or snapshot.get("metrics") or [],
            duration_seconds=snapshot.get("duration_seconds"),
            token_usage=snapshot.get("token_usage"),
            model=snapshot.get("model"),
            prompt_version=snapshot.get("prompt_version"),
            code_version=snapshot.get("code_version"),
            data_hashes={
                str(item.get("name") or item.get("location") or item.get("asset_id")): str(item.get("content_hash"))
                for item in assets if isinstance(item, dict) and item.get("content_hash")
            },
        )


class AssertionResult(BaseModel):
    assertion_id: str
    category: EvaluationCategory
    passed: bool
    message: str
    expected: Any = None
    actual: Any = None
    required: bool = True


class EvaluationResult(BaseModel):
    case_id: str
    run_id: str
    passed: bool
    assertion_results: list[AssertionResult]
    category_scores: dict[str, float]
    failure_counts: dict[str, int]
    metadata: dict[str, Any] = Field(default_factory=dict)
    evaluated_at: str = Field(default_factory=utc_now)


class EvaluationSummary(BaseModel):
    suite_id: str
    results: list[EvaluationResult]
    case_count: int
    passed_cases: int
    critical_failures: int = 0
    category_scores: dict[str, float] = Field(default_factory=dict)
    failure_counts: dict[str, int] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    generated_at: str = Field(default_factory=utc_now)

    @classmethod
    def from_results(
        cls,
        suite_id: str,
        results: list[EvaluationResult],
        *,
        critical_case_ids: Optional[set[str]] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> "EvaluationSummary":
        critical_case_ids = critical_case_ids or set()
        category_values: dict[str, list[float]] = {}
        failures: Counter[str] = Counter()
        for result in results:
            for category, value in result.category_scores.items():
                category_values.setdefault(category, []).append(value)
            failures.update(result.failure_counts)
        return cls(
            suite_id=suite_id,
            results=results,
            case_count=len(results),
            passed_cases=sum(result.passed for result in results),
            critical_failures=sum(
                (not result.passed) and result.case_id in critical_case_ids
                for result in results
            ),
            category_scores={
                category: sum(values) / len(values)
                for category, values in sorted(category_values.items())
            },
            failure_counts=dict(sorted(failures.items())),
            metadata=metadata or {},
        )

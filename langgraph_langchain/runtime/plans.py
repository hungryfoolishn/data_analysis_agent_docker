"""Execution-before-compute analysis plan models and deterministic validation."""

from __future__ import annotations

from typing import Iterable, Optional
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

from langgraph_langchain.runtime.models import RuntimePlanStep


class PlanBudget(BaseModel):
    max_steps: int = Field(default=20, ge=1, le=200)
    max_duration_seconds: float = Field(default=900.0, gt=0, le=86400)
    max_memory_mb: Optional[int] = Field(default=None, ge=128, le=262144)


class AnalysisPlan(BaseModel):
    plan_id: str = Field(default_factory=lambda: f"plan_{uuid4().hex}")
    version: int = Field(default=1, ge=1)
    goal: str = Field(..., min_length=1)
    success_criteria: list[str] = Field(default_factory=list)
    input_asset_ids: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)
    time_field: Optional[str] = None
    steps: list[RuntimePlanStep] = Field(..., min_length=1)
    budget: PlanBudget = Field(default_factory=PlanBudget)
    require_confirmation: bool = False
    strict_execution: bool = True

    @model_validator(mode="after")
    def validate_steps(self) -> "AnalysisPlan":
        if len(self.steps) > self.budget.max_steps:
            raise ValueError("Plan contains more steps than its budget allows")
        ids: set[str] = set()
        for step in self.steps:
            if step.step_id in ids:
                raise ValueError(f"Duplicate plan step id: {step.step_id}")
            ids.add(step.step_id)
            if step.step_id in step.depends_on:
                raise ValueError(f"Plan step '{step.step_id}' cannot depend on itself")
            unknown = [item for item in step.depends_on if item not in ids]
            if unknown:
                raise ValueError(
                    f"Plan step '{step.step_id}' has unknown or forward dependencies: "
                    + ", ".join(unknown)
                )
        return self


def validate_plan(
    plan: AnalysisPlan,
    *,
    available_methods: Iterable[str],
    available_asset_ids: Iterable[str] = (),
    asset_columns: Optional[dict[str, set[str]]] = None,
) -> list[str]:
    """Return deterministic, user-actionable validation errors."""
    errors: list[str] = []
    methods = set(available_methods)
    assets = set(available_asset_ids)
    unknown_methods = sorted({step.method for step in plan.steps if step.method not in methods})
    errors.extend(f"Unknown analysis method: {method}" for method in unknown_methods)
    missing_assets = sorted(set(plan.input_asset_ids) - assets)
    errors.extend(f"Unknown input asset: {asset_id}" for asset_id in missing_assets)
    for step in plan.steps:
        missing = sorted(set(step.required_inputs) - assets)
        errors.extend(
            f"Step '{step.step_id}' references unknown input asset: {asset_id}"
            for asset_id in missing
        )
    if asset_columns:
        known_columns = set().union(
            *(asset_columns.get(asset_id, set()) for asset_id in plan.input_asset_ids)
        )
        requested = set(plan.metrics) | set(plan.dimensions)
        if plan.time_field:
            requested.add(plan.time_field)
        errors.extend(
            f"Plan references unknown field: {field}"
            for field in sorted(requested - known_columns)
        )
    return errors


def default_analysis_plan(question: str) -> AnalysisPlan:
    """Provide a reviewable compatibility plan before the first tool call."""
    methods = [
        ("Load and inspect source data", "load_data"),
        ("Profile data quality and distributions", "eda_profile"),
        ("Run the requested formal analysis", "python_repl"),
        ("Record evidence-backed findings", "record_finding"),
        ("Validate and publish the report", "finish_report"),
    ]
    steps: list[RuntimePlanStep] = []
    previous: Optional[str] = None
    for objective, method in methods:
        step = RuntimePlanStep(
            objective=objective,
            method=method,
            expected_outputs=["analysis_output"] if method == "python_repl" else [],
            depends_on=[previous] if previous else [],
        )
        steps.append(step)
        previous = step.step_id
    return AnalysisPlan(
        goal=question.strip() or "Complete the requested data analysis",
        success_criteria=[
            "All requested facts are supported by recorded evidence",
            "Final report passes validation",
        ],
        steps=steps,
        require_confirmation=False,
        strict_execution=False,
    )

"""Execution-before-compute analysis plan models and deterministic validation."""

from __future__ import annotations

import json
from typing import Any, Iterable, Mapping, Optional
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

from langgraph_langchain.runtime.models import RuntimePlanStep
from langgraph_langchain.runtime.output_bindings import (
    OutputBindingConflictError,
    classify_input_reference,
    parse_logical_reference,
)


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


class PlanExecutionValidationError(ValueError):
    """Raised when a plan cannot safely be handed to Runtime execution."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = list(errors)
        super().__init__("Plan execution validation failed: " + "; ".join(errors))


def _has_multiple_statements(sql: str) -> bool:
    quote: str | None = None
    index = 0
    while index < len(sql):
        char = sql[index]
        if quote is not None:
            if char == quote:
                if index + 1 < len(sql) and sql[index + 1] == quote:
                    index += 2
                    continue
                quote = None
            index += 1
            continue
        if char in {"'", '"', "`"}:
            quote = char
        elif char == "[":
            quote = "]"
        elif sql.startswith("--", index):
            end = sql.find("\n", index + 2)
            if end < 0:
                break
            index = end + 1
            continue
        elif sql.startswith("/*", index):
            end = sql.find("*/", index + 2)
            if end < 0:
                return True
            index = end + 2
            continue
        elif char == ";" and sql[index + 1 :].strip():
            return True
        index += 1
    return False


def _first_sql_keyword(sql: str) -> str:
    index = 0
    while index < len(sql):
        if sql[index].isspace():
            index += 1
            continue
        if sql.startswith("--", index):
            end = sql.find("\n", index + 2)
            if end < 0:
                break
            index = end + 1
            continue
        if sql.startswith("/*", index):
            end = sql.find("*/", index + 2)
            if end < 0:
                break
            index = end + 2
            continue
        match = end = None
        for token in ("SELECT", "WITH"):
            if sql[index:].upper().startswith(token):
                return token
        break
    return ""


def validate_runtime_plan(
    plan: AnalysisPlan,
    *,
    available_methods: Iterable[str],
    available_asset_ids: Iterable[str],
    tool_schemas: Mapping[str, type] | None = None,
    require_known_methods: bool = True,
) -> list[str]:
    """Validate a Runtime plan immediately before TaskRunner execution."""
    assets = set(available_asset_ids)
    step_ids = {step.step_id for step in plan.steps}

    # v3 §7.3: classify physical asset IDs vs logical output references across
    # required_inputs AND planner arguments (including parsed parameters_json).
    # Logical references must never enter the physical asset check of
    # validate_plan(), or they would be rejected as unknown input assets.
    errors: list[str] = []
    logical_refs: list[tuple[RuntimePlanStep, str]] = []
    filtered_steps: list[RuntimePlanStep] = []
    for step in plan.steps:
        physical_required: list[str] = []
        candidate_values: list[str] = []
        for item in step.required_inputs:
            if isinstance(item, str):
                candidate_values.append(item)
        argument_values = dict(step.parameters)
        raw_parameters = argument_values.get("parameters_json")
        if isinstance(raw_parameters, str):
            try:
                parsed = json.loads(raw_parameters)
                if isinstance(parsed, dict):
                    argument_values.update(parsed)
            except (TypeError, ValueError, json.JSONDecodeError):
                pass  # reported by the dedicated parameters_json check below
        candidate_values.extend(
            value for value in argument_values.values() if isinstance(value, str)
        )
        physical_set: set[str] = set()
        for value in candidate_values:
            try:
                kind = classify_input_reference(value, step_ids, assets)
            except OutputBindingConflictError as exc:
                errors.append(f"Step '{step.step_id}': {exc}")
                kind = "asset_id"
            if kind == "logical_ref":
                if value in step.required_inputs:
                    logical_refs.append((step, value))
                else:
                    logical_refs.append((step, value))
            elif value in step.required_inputs:
                physical_set.add(value)
        physical_required = [
            item for item in step.required_inputs if item in physical_set
        ]
        filtered_steps.append(
            step.model_copy(update={"required_inputs": physical_required})
        )
    typed_plan = plan.model_copy(update={"steps": filtered_steps})
    errors = list(
        validate_plan(
            typed_plan,
            available_methods=available_methods,
            available_asset_ids=available_asset_ids,
        )
    )

    # Logical reference validation: producer exists, belongs to the step's
    # dependency closure, and declares the referenced output name.
    steps_by_id = {step.step_id: step for step in plan.steps}
    closures: dict[str, set[str]] = {}

    def closure_for(target_step_id: str) -> set[str]:
        if target_step_id not in closures:
            computed: set[str] = set()
            stack = list(steps_by_id[target_step_id].depends_on)
            while stack:
                current = stack.pop()
                if current in computed:
                    continue
                computed.add(current)
                producer_step = steps_by_id.get(current)
                if producer_step is not None:
                    stack.extend(producer_step.depends_on)
            closures[target_step_id] = computed
        return closures[target_step_id]

    for step, reference in logical_refs:
        try:
            producer_id, output_name = parse_logical_reference(reference)
        except OutputBindingConflictError as exc:
            errors.append(f"Step '{step.step_id}': {exc}")
            continue
        producer = steps_by_id.get(producer_id)
        if producer is None:
            errors.append(
                f"Step '{step.step_id}' references unknown step '{producer_id}'"
            )
            continue
        if producer_id != step.step_id and producer_id not in closure_for(
            step.step_id
        ):
            errors.append(
                f"Step '{step.step_id}' references '{reference}' which is not "
                "one of its dependencies"
            )
            continue
        if output_name not in producer.expected_outputs:
            errors.append(
                f"Step '{step.step_id}' references output '{output_name}' which "
                f"is not declared by step '{producer_id}'"
            )
    if not require_known_methods:
        errors = [
            error
            for error in errors
            if not error.startswith("Unknown analysis method:")
        ]
    methods = set(available_methods)
    assets = set(available_asset_ids)
    schemas = tool_schemas or {}
    step_ids = {step.step_id for step in plan.steps}
    completed: set[str] = set()
    visiting: set[str] = set()

    def visit(step_id: str, path: list[str]) -> None:
        if step_id in completed:
            return
        if step_id in visiting:
            cycle = path[path.index(step_id) :] + [step_id]
            errors.append("Plan dependency cycle: " + " -> ".join(cycle))
            return
        step = next((item for item in plan.steps if item.step_id == step_id), None)
        if step is None:
            return
        visiting.add(step_id)
        for dependency in step.depends_on:
            visit(dependency, path + [step_id])
        visiting.remove(step_id)
        completed.add(step_id)

    for step in plan.steps:
        visit(step.step_id, [])
        if not step.expected_outputs:
            errors.append(f"Step '{step.step_id}' has no expected output")
        if len(step.expected_outputs) != len(set(step.expected_outputs)):
            errors.append(
                f"Step '{step.step_id}' has duplicate expected outputs"
            )

        args = dict(step.parameters)
        raw_parameters = args.get("parameters_json")
        if isinstance(raw_parameters, str):
            try:
                parsed = json.loads(raw_parameters)
                if isinstance(parsed, dict):
                    args.update(parsed)
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                errors.append(
                    f"Step '{step.step_id}' has invalid parameters_json: {exc}"
                )
                continue
        schema = schemas.get(step.method)
        if schema is not None:
            try:
                schema.model_validate(args)
            except Exception as exc:
                errors.append(
                    f"Step '{step.step_id}' has invalid arguments for "
                    f"'{step.method}': {exc}"
                )

        if step.method == "run_analysis_operation":
            from langgraph_langchain.runtime.general_planner import (
                PLAN_OPERATIONS,
                _valid_parameters,
            )

            operation = args.get("operation")
            if operation not in PLAN_OPERATIONS:
                errors.append(
                    f"Step '{step.step_id}' uses unsupported analysis operation: "
                    f"{operation}"
                )
            else:
                errors.extend(
                    f"Step '{step.step_id}': {error}"
                    for error in _valid_parameters(
                        operation,
                        {
                            key: value
                            for key, value in args.items()
                            if key not in {"asset_id", "parameters_json"}
                        },
                    )
                )
        elif step.method == "run_sqlite_query":
            sql = args.get("sql")
            if not isinstance(sql, str) or not sql.strip():
                errors.append(f"Step '{step.step_id}' has no SQLite query")
            elif _has_multiple_statements(sql):
                errors.append(
                    f"Step '{step.step_id}' contains multiple SQL statements"
                )
            elif _first_sql_keyword(sql) not in {"SELECT", "WITH"}:
                errors.append(
                    f"Step '{step.step_id}' is not a read-only SQLite query"
                )

    return errors

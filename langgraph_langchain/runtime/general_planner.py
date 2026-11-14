"""Deterministic structured planning for general analysis requests.

The planner intentionally does not execute Python or SQL.  It produces an
allowlisted plan; execution remains a Runtime responsibility.
"""

from __future__ import annotations

import re
from typing import Any, Literal, Mapping, Sequence
from uuid import uuid4

from pydantic import BaseModel, Field

PLAN_OPERATIONS = {
    "inspect": {"min_inputs": 1, "max_inputs": 1, "output": "dataframe"},
    "select_columns": {"min_inputs": 1, "max_inputs": 1, "output": "dataframe"},
    "filter": {"min_inputs": 1, "max_inputs": 1, "output": "dataframe"},
    "group_aggregate": {"min_inputs": 1, "max_inputs": 1, "output": "dataframe"},
    "sort": {"min_inputs": 1, "max_inputs": 1, "output": "dataframe"},
    "trend": {"min_inputs": 1, "max_inputs": 1, "output": "dataframe"},
    "detect_anomalies": {"min_inputs": 1, "max_inputs": 1, "output": "dataframe"},
    "correlation": {"min_inputs": 1, "max_inputs": 1, "output": "dataframe"},
    "data_quality": {"min_inputs": 1, "max_inputs": 1, "output": "dataframe"},
    "summarize": {"min_inputs": 1, "max_inputs": 1, "output": "metric"},
}

OperationName = Literal[
    "inspect",
    "select_columns",
    "filter",
    "group_aggregate",
    "sort",
    "trend",
    "detect_anomalies",
    "correlation",
    "data_quality",
    "summarize",
]
ExpectedOutput = Literal["dataframe", "metric", "chart"]


class PlanValidationError(ValueError):
    """Raised when a generated or supplied plan fails validation."""

    def __init__(self, errors: Sequence[str]) -> None:
        self.errors = list(errors)
        super().__init__("Invalid analysis plan: " + "; ".join(self.errors))


class PlannerClarificationError(ValueError):
    """Raised when the user goal lacks enough information to plan safely."""

    def __init__(self, questions: Sequence[str]) -> None:
        self.questions = list(questions)
        super().__init__("Analysis goal needs clarification: " + "; ".join(self.questions))


class GeneralPlanStep(BaseModel):
    step_id: str = Field(pattern=r"^step_[0-9a-f]{32}$")
    operation: OperationName
    input_refs: list[str] = Field(min_length=1)
    parameters: dict[str, Any] = Field(default_factory=dict)
    expected_output: ExpectedOutput
    # B4.2.1 §8.3: the declared output *name* (not the output type).  Runtime
    # steps emit ``expected_outputs=[output_name]`` and downstream steps may
    # reference ``{step_id}.{output_name}`` as a logical input reference.
    output_name: str = Field(default="output", pattern=r"^[a-z][a-z0-9_]{0,63}$")
    depends_on: list[str] = Field(default_factory=list)
    rationale: str = Field(default="", max_length=1000)


class GeneralAnalysisPlan(BaseModel):
    plan_id: str = Field(default_factory=lambda: f"plan_{uuid4().hex}")
    user_goal: str = Field(min_length=1, max_length=2000)
    input_asset_ids: list[str] = Field(min_length=1)
    steps: list[GeneralPlanStep] = Field(min_length=1)


class PlannerResult(BaseModel):
    plan: GeneralAnalysisPlan
    clarification_questions: list[str] = Field(default_factory=list)
    unsupported_reason: str | None = None


def _valid_parameters(operation: str, parameters: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if operation == "select_columns":
        columns = parameters.get("columns")
        if not isinstance(columns, list) or not columns:
            errors.append("select_columns requires non-empty columns list")
        elif not all(isinstance(item, str) for item in columns):
            errors.append("select_columns columns must be strings")
    elif operation == "filter":
        column = parameters.get("column")
        if not isinstance(column, str) or not column:
            errors.append("filter requires column")
        if "value" not in parameters:
            errors.append("filter requires value")
    elif operation == "group_aggregate":
        group_by = parameters.get("group_by")
        metric = parameters.get("metric")
        agg = parameters.get("agg")
        if not isinstance(group_by, str) or not group_by:
            errors.append("group_aggregate requires group_by")
        if not isinstance(metric, str) or not metric:
            errors.append("group_aggregate requires metric")
        if agg not in {"sum", "mean", "count", "min", "max"}:
            errors.append("group_aggregate agg is not allowed")
    elif operation == "sort":
        if not isinstance(parameters.get("by"), str):
            errors.append("sort requires by")
        if parameters.get("ascending", True) not in {True, False}:
            errors.append("sort ascending must be boolean")
    elif operation == "trend":
        if not isinstance(parameters.get("time_column"), str):
            errors.append("trend requires time_column")
        if not isinstance(parameters.get("value_column"), str):
            errors.append("trend requires value_column")
    elif operation == "detect_anomalies":
        if not isinstance(parameters.get("value_column"), str):
            errors.append("detect_anomalies requires value_column")
        if parameters.get("method", "iqr") != "iqr":
            errors.append("detect_anomalies only supports method=iqr")
    elif operation == "correlation":
        columns = parameters.get("columns")
        if not isinstance(columns, list) or len(columns) < 2:
            errors.append("correlation requires at least two columns")
        elif not all(isinstance(item, str) for item in columns):
            errors.append("correlation columns must be strings")
    elif operation == "data_quality":
        if not isinstance(parameters.get("time_column"), (str, type(None))):
            errors.append("data_quality time_column must be text or null")
    return errors


def _match_goal_columns(goal: str, columns: set[str]) -> list[str]:
    lowered = goal.lower()
    return [
        column
        for column in sorted(columns)
        if column.lower() in lowered
    ]


def _operation_for_goal(
    goal: str,
    columns: set[str] | None = None,
) -> tuple[str | None, dict[str, Any]]:
    text = goal.lower()
    matched_columns = _match_goal_columns(goal, columns or set())
    parameters: dict[str, Any] = {}

    group_match = re.search(
        r"(?:group(?:ed)?\s+by|按|根据|每个)\s*[\s:：]*([\w\u4e00-\u9fff]+)",
        goal,
        re.IGNORECASE,
    )
    if group_match:
        parameters["group_by"] = group_match.group(1)
        if re.search(r"\baverage\b|\bavg\b|平均", text):
            parameters["agg"] = "mean"
        elif re.search(r"\bcount\b|数量|计数", text):
            parameters["agg"] = "count"
        elif re.search(r"\bminimum\b|\bmin\b|最小", text):
            parameters["agg"] = "min"
        elif re.search(r"\bmaximum\b|\bmax\b|最大", text):
            parameters["agg"] = "max"
        else:
            parameters["agg"] = "sum"
        if re.search(r"\b(?:sum|total|average|avg|count|min|max)\b", text):
            metric_match = re.search(
                r"(?:sum|total|average|avg|count|minimum|min|maximum|max)\s+(?:of\s+)?([\w\u4e00-\u9fff]+)",
                text,
            )
            if metric_match:
                parameters["metric"] = metric_match.group(1)
        return "group_aggregate", parameters

    if re.search(r"\btrend\b|趋势|随时间|over time", text):
        return "trend", parameters
    if re.search(r"\banomal(?:y|ies)\b|异常|离群", text):
        return "detect_anomalies", parameters
    if re.search(r"\bcorrelat(?:e|ion)\b|相关", text):
        selected = matched_columns or sorted(columns or set())
        if len(selected) >= 2:
            parameters["columns"] = selected[:5]
        return "correlation", parameters
    if re.search(r"\bdata quality\b|质量问题|缺失|重复|数据质量", text):
        return "data_quality", parameters
    top_match = re.search(r"\btop\s+(\d+)\b|前\s*(\d+)\s*名", text)
    if top_match or re.search(r"\bsort\b|排序|rank|排名", text):
        parameters["ascending"] = False if re.search(r"desc|descending|降序|top|rank|排名", text) else True
        by_match = re.search(r"\bby\s+([\w\u4e00-\u9fff]+)|按\s*([\w\u4e00-\u9fff]+)", text)
        if by_match:
            parameters["by"] = by_match.group(1) or by_match.group(2)
        elif matched_columns:
            parameters["by"] = matched_columns[0]
        return "sort", parameters

    if re.search(r"\bfilter\b|过滤|筛选|missing rows|缺失行|greater than|大于|less than|小于", text):
        if matched_columns:
            parameters["column"] = matched_columns[0]
        greater = re.search(r"(?:greater than|>)\s*(-?\d+(?:\.\d+)?)", text)
        less = re.search(r"(?:less than|<)\s*(-?\d+(?:\.\d+)?)", text)
        if greater:
            parameters["operator"] = "gt"
            parameters["value"] = float(greater.group(1))
        elif less:
            parameters["operator"] = "lt"
            parameters["value"] = float(less.group(1))
        elif re.search(r"missing rows|缺失行", text):
            parameters["operator"] = "eq"
            parameters["value"] = None
        else:
            parameters["operator"] = "eq"
            parameters["value"] = matched_columns[0] if matched_columns else None
        if "column" not in parameters and columns:
            parameters["column"] = sorted(columns)[0]
        if "value" not in parameters:
            parameters["value"] = None
        return "filter", parameters

    if re.search(r"\bsum\b|总和|total|总计|average|avg|平均|count|计数|minimum|min|最小|maximum|max|最大", text):
        return "summarize", parameters
    if re.search(r"\bsummar(?:y|ize|ise)\b|概览|摘要|统计", text):
        return "summarize", parameters
    return None, parameters


def _preferred_numeric_columns(columns: set[str]) -> list[str]:
    preferred = [
        column
        for column in sorted(columns)
        if any(
            token in column.lower()
            for token in ("amount", "value", "revenue", "sales", "price", "count", "metric")
        )
    ]
    return preferred or sorted(columns)


def _columns_for_asset(
    asset_columns: Mapping[str, Sequence[str]], asset_id: str
) -> set[str]:
    return set(asset_columns.get(asset_id, []))


def _parse_output_reference(ref: str) -> tuple[str, str] | None:
    """Return ``(producer_step_id, output_name)`` for ``{step_id}.{output_name}``.

    Anything that is not a known-step output reference (e.g. a physical asset
    ID) returns ``None``.  The runtime shares this syntax (B11.2.1 §8.2).
    """
    if "." not in ref:
        return None
    step_id, _, output_name = ref.partition(".")
    if not step_id.startswith("step_") or not step_id[5:].isalnum() or not output_name:
        return None
    return step_id, output_name


def validate_general_plan(
    plan: GeneralAnalysisPlan,
    *,
    asset_columns: Mapping[str, Sequence[str]],
) -> list[str]:
    """Return deterministic validation errors; an empty list means valid.

    v2 (B4.2.1 §8): ``input_refs`` may reference initial assets or the declared
    output of an earlier step (``{step_id}.{output_name}``).  Forward
    references, self references, unknown steps, name mismatches and cycles are
    rejected here — GeneralPlan validation is authoritative and must not rely
    only on the second Runtime layer.
    """
    errors: list[str] = []
    available_inputs = set(plan.input_asset_ids)
    output_by_step: dict[str, str] = {}
    seen_steps: set[str] = set()

    for asset_id in plan.input_asset_ids:
        if asset_id not in asset_columns:
            errors.append(f"Unknown input asset: {asset_id}")

    for index, step in enumerate(plan.steps, start=1):
        if step.step_id in seen_steps:
            errors.append(f"Duplicate step_id: {step.step_id}")
            continue
        seen_steps.add(step.step_id)
        spec = PLAN_OPERATIONS.get(step.operation)
        if spec is None:
            errors.append(f"Unsupported plan operation: {step.operation}")
            continue

        if len(step.input_refs) < spec["min_inputs"] or len(
            step.input_refs
        ) > spec["max_inputs"]:
            errors.append(
                f"Step {step.step_id} operation {step.operation} has invalid input count"
            )

        if step.step_id in step.depends_on:
            errors.append(f"Step {step.step_id} depends on itself")

        # Joint cycle detection over depends_on edges and output references.
        edges: dict[str, set[str]] = {}
        for candidate in plan.steps:
            deps = set(candidate.depends_on)
            for ref in candidate.input_refs:
                parsed = _parse_output_reference(ref)
                if parsed is not None:
                    deps.add(parsed[0])
            edges[candidate.step_id] = deps
        visiting: set[str] = set()
        completed: set[str] = set()

        def _has_cycle(node: str) -> bool:
            if node in completed:
                return False
            if node in visiting:
                return True
            visiting.add(node)
            for dep in edges.get(node, ()):
                if _has_cycle(dep):
                    return True
            visiting.discard(node)
            completed.add(node)
            return False

        if _has_cycle(step.step_id):
            errors.append(f"Plan contains a dependency cycle at step {step.step_id}")

        for ref in step.input_refs:
            parsed = _parse_output_reference(ref)
            if parsed is None:
                if ref not in available_inputs:
                    errors.append(
                        f"Step {step.step_id} references unknown input {ref}"
                    )
                continue
            producer_id, output_name = parsed
            if producer_id == step.step_id:
                errors.append(
                    f"Step {step.step_id} references its own output {ref}"
                )
                continue
            producer = next(
                (item for item in plan.steps if item.step_id == producer_id),
                None,
            )
            if producer is None:
                errors.append(
                    f"Step {step.step_id} references unknown step {producer_id}"
                )
                continue
            if plan.steps.index(producer) >= index - 1:
                errors.append(
                    f"Step {step.step_id} references forward or self step {producer_id}"
                )
                continue
            if producer.output_name != output_name:
                errors.append(
                    f"Step {step.step_id} references output {output_name} which "
                    f"is not declared by step {producer_id} "
                    f"(declared: {producer.output_name})"
                )
            if producer_id not in step.depends_on:
                errors.append(
                    f"Step {step.step_id} references {ref} without declaring "
                    f"{producer_id} in depends_on"
                )

        for dependency in step.depends_on:
            if dependency not in output_by_step:
                errors.append(
                    f"Step {step.step_id} depends on unknown or forward step {dependency}"
                )

        errors.extend(_valid_parameters(step.operation, step.parameters))
        if step.expected_output != spec["output"]:
            errors.append(
                f"Step {step.step_id} expected output does not match operation"
            )

        # Column existence is only checkable for initial-asset inputs; columns
        # of upstream step outputs are unknown at plan time and are validated
        # at execution time by analysis_operations.
        for column_name in _parameter_columns(step.operation, step.parameters):
            inputs_with_columns = [
                ref
                for ref in step.input_refs
                if _parse_output_reference(ref) is None
                and ref in asset_columns
                and column_name in _columns_for_asset(asset_columns, ref)
            ]
            if not inputs_with_columns and all(
                _parse_output_reference(ref) is None
                for ref in step.input_refs
            ):
                errors.append(
                    f"Step {step.step_id} references unknown column {column_name}"
                )

        if errors:
            continue
        output_by_step[step.step_id] = f"{step.step_id}.{step.output_name}"

    return errors


def _parameter_columns(operation: str, parameters: Mapping[str, Any]) -> list[str]:
    if operation == "select_columns":
        return [item for item in parameters.get("columns", []) if isinstance(item, str)]
    if operation == "filter":
        value = parameters.get("column")
        return [value] if isinstance(value, str) else []
    if operation == "group_aggregate":
        return [
            item
            for item in (parameters.get("group_by"), parameters.get("metric"))
            if isinstance(item, str)
        ]
    if operation == "sort":
        value = parameters.get("by")
        return [value] if isinstance(value, str) else []
    if operation == "trend":
        return [
            item
            for item in (
                parameters.get("time_column"),
                parameters.get("value_column"),
            )
            if isinstance(item, str)
        ]
    if operation == "detect_anomalies":
        value = parameters.get("value_column")
        return [value] if isinstance(value, str) else []
    return []


class DeterministicAnalysisPlanner:
    """Turn a narrow set of common analysis goals into validated plans."""

    def plan(
        self,
        *,
        goal: str,
        input_asset_ids: Sequence[str],
        asset_columns: Mapping[str, Sequence[str]],
    ) -> PlannerResult:
        if not goal.strip():
            raise PlanValidationError(["user_goal cannot be blank"])
        if not input_asset_ids:
            raise PlannerClarificationError(
                ["Which input dataset should be analyzed?"]
            )

        primary_asset = input_asset_ids[0]
        columns = _columns_for_asset(asset_columns, primary_asset)
        operation, parsed_parameters = _operation_for_goal(
            goal, columns=set(columns)
        )
        questions: list[str] = []
        if operation is None:
            raise PlannerClarificationError(
                [
                    "Supported goals include summarize, group aggregation, trend, "
                    "and anomaly detection. Please clarify the requested analysis."
                ]
            )

        parameters = dict(parsed_parameters)
        if operation == "trend":
            if not parameters.get("time_column"):
                date_columns = sorted(
                    column
                    for column in columns
                    if "date" in column.lower() or "time" in column.lower()
                )
                if date_columns:
                    parameters["time_column"] = date_columns[0]
                else:
                    questions.append("Which column contains the time field?")
            if not parameters.get("value_column"):
                numeric_candidates = sorted(
                    column
                    for column in columns
                    if column != parameters.get("time_column")
                )
            preferred_numeric = [
                column
                for column in numeric_candidates
                if any(
                    token in column.lower()
                    for token in ("amount", "value", "revenue", "sales", "metric")
                )
            ]
            if preferred_numeric:
                numeric_candidates = preferred_numeric
                if numeric_candidates:
                    parameters["value_column"] = numeric_candidates[-1]
                else:
                    questions.append("Which numeric column should be analyzed?")
        elif operation == "detect_anomalies":
            numeric_candidates = sorted(columns)
            if numeric_candidates:
                parameters["value_column"] = numeric_candidates[-1]
            else:
                questions.append("Which numeric column should be checked for anomalies?")
        elif operation == "group_aggregate":
            metric = parameters.get("metric")
            if not metric or metric not in columns:
                numeric_candidates = sorted(
                    columns - {parameters.get("group_by")}
                )
                preferred_numeric = [
                    column
                    for column in numeric_candidates
                    if any(
                        token in column.lower()
                        for token in ("amount", "value", "revenue", "sales", "metric")
                    )
                ]
                if preferred_numeric:
                    numeric_candidates = preferred_numeric
                if numeric_candidates:
                    parameters["metric"] = numeric_candidates[-1]
                else:
                    questions.append("Which metric should be aggregated?")
        elif operation == "sort":
            parameters.setdefault("ascending", False)

        if questions:
            raise PlannerClarificationError(questions)

        first_step = GeneralPlanStep(
            step_id=f"step_{uuid4().hex}",
            operation="inspect",
            input_refs=[primary_asset],
            parameters={},
            expected_output="dataframe",
            depends_on=[],
            rationale="Inspect the registered input before analysis.",
        )
        analysis_step = GeneralPlanStep(
            step_id=f"step_{uuid4().hex}",
            operation=operation,
            input_refs=[primary_asset],
            parameters=parameters,
            expected_output=PLAN_OPERATIONS[operation]["output"],
            depends_on=[first_step.step_id],
            rationale=f"Deterministic operation selected from goal: {goal[:300]}",
        )
        plan = GeneralAnalysisPlan(
            user_goal=goal,
            input_asset_ids=list(input_asset_ids),
            steps=[first_step, analysis_step],
        )
        errors = validate_general_plan(plan, asset_columns=asset_columns)
        if errors:
            raise PlanValidationError(errors)
        return PlannerResult(plan=plan)

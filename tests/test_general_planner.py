from __future__ import annotations

from uuid import uuid4

import pytest

from langgraph_langchain.runtime.general_planner import (
    DeterministicAnalysisPlanner,
    GeneralAnalysisPlan,
    GeneralPlanStep,
    PlannerClarificationError,
    PlanValidationError,
    validate_general_plan,
)

COLUMNS = {
    "asset_orders": ["order_id", "region", "amount", "order_date"],
    "asset_customers": ["customer_id", "name"],
}


def _valid_plan() -> GeneralAnalysisPlan:
    return GeneralAnalysisPlan(
        user_goal="Summarize orders",
        input_asset_ids=["asset_orders"],
        steps=[
            GeneralPlanStep(
                step_id=f"step_{uuid4().hex}",
                operation="inspect",
                input_refs=["asset_orders"],
                expected_output="dataframe",
            ),
            GeneralPlanStep(
                step_id=f"step_{uuid4().hex}",
                operation="group_aggregate",
                input_refs=["asset_orders"],
                parameters={"group_by": "region", "metric": "amount", "agg": "sum"},
                expected_output="dataframe",
                depends_on=[],
            ),
        ],
    )


def test_planner_builds_group_aggregate_plan():
    result = DeterministicAnalysisPlanner().plan(
        goal="Sum total amount group by region",
        input_asset_ids=["asset_orders"],
        asset_columns=COLUMNS,
    )

    operations = [step.operation for step in result.plan.steps]
    assert operations == ["inspect", "group_aggregate"]
    analysis = result.plan.steps[-1]
    assert analysis.parameters["group_by"] == "region"
    assert analysis.parameters["metric"] == "amount"
    assert analysis.parameters["agg"] == "sum"
    assert validate_general_plan(result.plan, asset_columns=COLUMNS) == []


def test_planner_builds_trend_and_anomaly_plans():
    planner = DeterministicAnalysisPlanner()
    trend = planner.plan(
        goal="Show amount trend over time",
        input_asset_ids=["asset_orders"],
        asset_columns=COLUMNS,
    )
    anomaly = planner.plan(
        goal="Detect amount anomalies",
        input_asset_ids=["asset_orders"],
        asset_columns=COLUMNS,
    )

    assert trend.plan.steps[-1].operation == "trend"
    assert trend.plan.steps[-1].parameters["time_column"] == "order_date"
    assert trend.plan.steps[-1].parameters["value_column"] == "amount"
    assert anomaly.plan.steps[-1].operation == "detect_anomalies"
    assert validate_general_plan(trend.plan, asset_columns=COLUMNS) == []
    assert validate_general_plan(anomaly.plan, asset_columns=COLUMNS) == []


def test_planner_requires_clarification_for_ambiguous_goal():
    with pytest.raises(PlannerClarificationError):
        DeterministicAnalysisPlanner().plan(
            goal="Please tell me something interesting",
            input_asset_ids=["asset_orders"],
            asset_columns=COLUMNS,
        )


def test_planner_requires_input_asset():
    with pytest.raises(PlannerClarificationError):
        DeterministicAnalysisPlanner().plan(
            goal="Summarize data",
            input_asset_ids=[],
            asset_columns=COLUMNS,
        )


def test_validator_rejects_unknown_operation_column_and_asset():
    plan = _valid_plan()
    plan.input_asset_ids.append("asset_missing")
    plan.steps[-1].operation = "python_exec"  # type: ignore[arg-type]
    plan.steps[-1].parameters = {
        "group_by": "unknown_dimension",
        "metric": "unknown_metric",
        "agg": "sum",
    }

    errors = validate_general_plan(plan, asset_columns=COLUMNS)

    assert "Unknown input asset: asset_missing" in errors
    assert any("Unsupported plan operation" in error for error in errors)
    # Parameter validation is operation-specific; unsupported operations are
    # rejected before their arbitrary parameter shape can be trusted.


def test_validator_rejects_parameter_types_and_forward_dependency():
    plan = _valid_plan()
    plan.steps[-1].parameters = {"group_by": 1, "metric": 2, "agg": "median"}
    plan.steps[0].depends_on = [plan.steps[-1].step_id]

    errors = validate_general_plan(plan, asset_columns=COLUMNS)

    assert any("group_aggregate requires group_by" in error for error in errors)
    assert any("group_aggregate requires metric" in error for error in errors)
    assert any("agg is not allowed" in error for error in errors)
    assert any("unknown or forward step" in error for error in errors)


def test_validator_rejects_cycle():
    plan = _valid_plan()
    plan.steps[0].depends_on = [plan.steps[1].step_id]
    plan.steps[1].depends_on = [plan.steps[0].step_id]

    errors = validate_general_plan(plan, asset_columns=COLUMNS)

    assert any("unknown or forward step" in error for error in errors)

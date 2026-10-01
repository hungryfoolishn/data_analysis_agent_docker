"""Runtime V10.0 plan validation tests."""

from __future__ import annotations

import pytest

from langgraph_langchain.runtime.financial_agent import (
    FinancialPlanTaskType,
    FinancialTaskPlanner,
    FinancialTaskUnderstandingBuilder,
    VerificationStatus,
)


KNOWN_COMPANIES = ["贵州茅台", "五粮液"]


@pytest.fixture()
def plan():
    builder = FinancialTaskUnderstandingBuilder(KNOWN_COMPANIES)
    understanding = builder.build(
        "分析贵州茅台 2021-2025 年收入、盈利能力和现金流，并与五粮液比较。"
    )
    return FinancialTaskPlanner().build(understanding)


def _task(plan, task_type):
    return next(task for task in plan.tasks if task.task_type == task_type)


def test_valid_plan_has_no_validation_errors(plan):
    assert plan.status.value == "VALIDATED"
    assert plan.validation_errors == []
    assert FinancialTaskPlanner().validate_plan(plan) == []


def test_validate_detects_unknown_dependency(plan):
    _task(plan, FinancialPlanTaskType.RESOLVE_PERIODS).dependencies.append(
        "task_missing"
    )

    errors = FinancialTaskPlanner().validate_plan(plan)

    assert any("unknown dependency task_missing" in error for error in errors)
    assert plan.status.value == "VALIDATED"


def test_validate_detects_duplicate_task_ids(plan):
    plan.tasks[1].task_id = plan.tasks[0].task_id

    errors = FinancialTaskPlanner().validate_plan(plan)

    assert any("Duplicate task IDs" in error for error in errors)


def test_validate_detects_cycle(plan):
    resolve_companies = _task(plan, FinancialPlanTaskType.RESOLVE_COMPANIES)
    resolve_periods = _task(plan, FinancialPlanTaskType.RESOLVE_PERIODS)
    resolve_companies.dependencies = [resolve_periods.task_id]

    errors = FinancialTaskPlanner().validate_plan(plan)

    assert any("Dependency cycle detected" in error for error in errors)


def test_validate_detects_missing_tool(plan):
    revenue = _task(plan, FinancialPlanTaskType.REVENUE_TREND)
    revenue.selected_tool = None

    errors = FinancialTaskPlanner().validate_plan(plan)

    assert any("no registered tool" in error for error in errors)


def test_validate_detects_unregistered_tool(plan):
    revenue = _task(plan, FinancialPlanTaskType.REVENUE_TREND)
    revenue.selected_tool = "unregistered_tool"

    errors = FinancialTaskPlanner().validate_plan(plan)

    assert any("unregistered tool" in error for error in errors)


def test_validate_detects_tool_task_type_mismatch(plan):
    revenue = _task(plan, FinancialPlanTaskType.REVENUE_TREND)
    revenue.selected_tool = "report_builder"

    errors = FinancialTaskPlanner().validate_plan(plan)

    assert any("does not support task type" in error for error in errors)


def test_validate_detects_unregistered_metric(plan):
    revenue = _task(plan, FinancialPlanTaskType.REVENUE_TREND)
    revenue.required_metrics.append("definitely_unknown_metric")

    errors = FinancialTaskPlanner().validate_plan(plan)

    assert any(
        "requires unregistered metric definitely_unknown_metric" in error
        for error in errors
    )


def test_validate_requires_verification_for_numeric_task(plan):
    revenue = _task(plan, FinancialPlanTaskType.REVENUE_TREND)
    revenue.verification_status = VerificationStatus.NOT_REQUIRED

    errors = FinancialTaskPlanner().validate_plan(plan)

    assert any("must require verification" in error for error in errors)


def test_validate_requires_report_to_depend_on_findings(plan):
    report = _task(plan, FinancialPlanTaskType.GENERATE_REPORT)
    findings = _task(plan, FinancialPlanTaskType.BUILD_FINDINGS)
    report.dependencies = [_task(plan, FinancialPlanTaskType.REVENUE_TREND).task_id]

    errors = FinancialTaskPlanner().validate_plan(plan)

    # Removing findings dependency may also produce other DAG errors; the
    # report-contract error is the assertion that matters here.
    assert any(
        "Report task must depend on finding generation." in error
        for error in errors
    )

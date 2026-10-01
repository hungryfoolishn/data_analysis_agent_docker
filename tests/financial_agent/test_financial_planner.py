"""Runtime V10.0 financial task planner tests."""

from __future__ import annotations

import pytest

from langgraph_langchain.runtime.financial_agent import (
    ExecutionStatus,
    FailurePolicy,
    FINANCIAL_TOOL_REGISTRY,
    FinancialPlanTaskType,
    FinancialTaskPlanner,
    FinancialTaskUnderstandingBuilder,
    PlanStatus,
    VerificationStatus,
)
from langgraph_langchain.runtime.financial_agent.planner import _plan_id


KNOWN_COMPANIES = ["贵州茅台", "五粮液", "泸州老窖"]


@pytest.fixture()
def comprehensive_understanding():
    builder = FinancialTaskUnderstandingBuilder(KNOWN_COMPANIES)
    return builder.build(
        "分析贵州茅台 2021—2025 年的收入、利润、盈利能力和现金流变化，并与五粮液比较。"
    )


@pytest.fixture()
def comprehensive_plan(comprehensive_understanding):
    return FinancialTaskPlanner().build(comprehensive_understanding)


def test_comprehensive_example_generates_expected_task_dag(comprehensive_plan):
    task_types = [task.task_type for task in comprehensive_plan.tasks]

    assert task_types == [
        FinancialPlanTaskType.RESOLVE_COMPANIES,
        FinancialPlanTaskType.RESOLVE_PERIODS,
        FinancialPlanTaskType.VALIDATE_DATA_COVERAGE,
        FinancialPlanTaskType.REVENUE_TREND,
        FinancialPlanTaskType.PROFIT_TREND,
        FinancialPlanTaskType.PROFITABILITY_ANALYSIS,
        FinancialPlanTaskType.CASHFLOW_ANALYSIS,
        FinancialPlanTaskType.PEER_COMPARISON,
        FinancialPlanTaskType.VERIFY_CALCULATIONS,
        FinancialPlanTaskType.BUILD_FINDINGS,
        FinancialPlanTaskType.GENERATE_REPORT,
    ]
    assert comprehensive_plan.status == PlanStatus.VALIDATED
    assert comprehensive_plan.validation_errors == []
    assert comprehensive_plan.metadata["company_count"] == 2
    assert comprehensive_plan.metadata["comparison_enabled"] is True


def test_plan_task_dependencies_form_a_dag(comprehensive_plan):
    dependencies = {
        task.task_id: set(task.dependencies)
        for task in comprehensive_plan.tasks
    }

    assert dependencies["task_001"] == set()
    assert dependencies["task_002"] == {"task_001"}
    assert dependencies["task_003"] == {"task_001", "task_002"}
    assert dependencies["task_004"] == {"task_003"}
    assert dependencies["task_008"] == {"task_003"}
    assert dependencies["task_009"] == {
        "task_004", "task_005", "task_006", "task_007", "task_008"
    }
    assert dependencies["task_010"] == {"task_009"}
    assert dependencies["task_011"] == {"task_010"}


def test_all_tasks_are_mapped_to_registered_tools(comprehensive_plan):
    for task in comprehensive_plan.tasks:
        assert task.selected_tool in FINANCIAL_TOOL_REGISTRY
        tool = FINANCIAL_TOOL_REGISTRY[task.selected_tool]
        assert task.task_type in tool.supported_task_types
        assert task.tool_version == tool.version
        assert task.execution_status == ExecutionStatus.PENDING


def test_numeric_tasks_require_verification(comprehensive_plan):
    numeric_tasks = [
        task for task in comprehensive_plan.tasks
        if task.task_type in {
            FinancialPlanTaskType.REVENUE_TREND,
            FinancialPlanTaskType.PROFIT_TREND,
            FinancialPlanTaskType.PROFITABILITY_ANALYSIS,
            FinancialPlanTaskType.CASHFLOW_ANALYSIS,
            FinancialPlanTaskType.PEER_COMPARISON,
        }
    ]

    assert numeric_tasks
    assert all(task.verification_status == VerificationStatus.REQUIRED for task in numeric_tasks)
    assert all(
        task.failure_policy == FailurePolicy.CONTINUE_WITH_WARNING
        for task in numeric_tasks
    )


def test_report_is_preceded_by_verified_findings(comprehensive_plan):
    task_by_id = {task.task_id: task for task in comprehensive_plan.tasks}
    report = next(
        task for task in comprehensive_plan.tasks
        if task.task_type == FinancialPlanTaskType.GENERATE_REPORT
    )
    finding = next(
        task for task in comprehensive_plan.tasks
        if task.task_type == FinancialPlanTaskType.BUILD_FINDINGS
    )
    verification = next(
        task for task in comprehensive_plan.tasks
        if task.task_type == FinancialPlanTaskType.VERIFY_CALCULATIONS
    )

    assert report.dependencies == [finding.task_id]
    assert finding.dependencies == [verification.task_id]
    assert task_by_id[verification.task_id].task_type == FinancialPlanTaskType.VERIFY_CALCULATIONS


def test_revenue_plan_has_expected_required_metrics_and_data():
    understanding = FinancialTaskUnderstandingBuilder(KNOWN_COMPANIES).build(
        "分析贵州茅台 2021-2025 年营业收入变化"
    )
    plan = FinancialTaskPlanner().build(understanding)
    revenue_task = next(
        task for task in plan.tasks
        if task.task_type == FinancialPlanTaskType.REVENUE_TREND
    )

    assert revenue_task.required_metrics == ["revenue", "revenue_growth"]
    assert set(revenue_task.required_data) == {
        "income_statement.revenue"
    }
    assert plan.status == PlanStatus.VALIDATED


def test_plan_ids_are_stable_and_query_linked(comprehensive_understanding):
    first = FinancialTaskPlanner().build(comprehensive_understanding)
    second = FinancialTaskPlanner().build(comprehensive_understanding)

    assert first.plan_id == second.plan_id
    assert first.plan_id == _plan_id(comprehensive_understanding.query_id)
    assert first.plan_id.startswith("plan_")
    assert first.query_id == comprehensive_understanding.query_id


def test_plan_schema_has_required_planning_fields(comprehensive_plan):
    for task in comprehensive_plan.tasks:
        assert task.task_id
        assert task.task_type
        assert task.name
        assert isinstance(task.dependencies, list)
        assert isinstance(task.required_metrics, list)
        assert isinstance(task.required_data, list)
        assert task.selected_tool
        assert task.tool_version
        assert task.execution_status
        assert task.verification_status
        assert task.output_refs == []
        assert task.failure_policy

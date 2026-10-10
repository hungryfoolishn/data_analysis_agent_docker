"""Runtime V10.2 execution plan tests."""

from __future__ import annotations

import pytest

from langgraph_langchain.runtime.financial.models import (
    BalanceSheetStatement,
    CashFlowStatement,
    Company,
    IncomeStatement,
)
from langgraph_langchain.runtime.financial import FinancialDataService
from langgraph_langchain.runtime.financial_agent import (
    ExecutionPlanBuilder,
    ExecutionTask,
    ExecutionPlanStatus,
    ExecutionStatus,
    FinancialSemanticResolver,
    FinancialTaskUnderstandingBuilder,
    PlanStatus,
)


PERIODS = ["2021", "2022", "2023", "2024"]


def _company() -> Company:
    return Company(
        company_id="600519",
        stock_code="600519",
        company_name="贵州茅台",
        exchange="SSE",
        industry_id="baijiu",
    )


def _income(period: str, **overrides) -> IncomeStatement:
    values = {
        "company_id": "600519",
        "period": period,
        "revenue": 100.0 + int(period),
        "cost_of_revenue": 30.0,
        "gross_profit": 70.0,
        "operating_profit": 40.0,
        "net_profit": 25.0,
        "net_profit_attributable": 24.0,
        "source_id": f"income_{period}",
    }
    values.update(overrides)
    return IncomeStatement(**values)


def _balance(period: str, **overrides) -> BalanceSheetStatement:
    values = {
        "company_id": "600519",
        "period": period,
        "total_assets": 500.0,
        "total_liabilities": 200.0,
        "total_equity": 300.0,
        "cash": 150.0,
        "accounts_receivable": 20.0,
        "inventory": 30.0,
        "fixed_assets": 100.0,
        "short_term_debt": 50.0,
        "long_term_debt": 50.0,
        "current_assets": 200.0,
        "current_liabilities": 100.0,
        "source_id": f"balance_{period}",
    }
    values.update(overrides)
    return BalanceSheetStatement(**values)


def _cash(period: str, **overrides) -> CashFlowStatement:
    values = {
        "company_id": "600519",
        "period": period,
        "operating_cash_flow": 35.0,
        "investing_cash_flow": -10.0,
        "financing_cash_flow": -5.0,
        "capital_expenditure": 12.0,
        "free_cash_flow": 23.0,
        "source_id": f"cash_{period}",
    }
    values.update(overrides)
    return CashFlowStatement(**values)


def _service(
    *,
    periods: list[str] | None = None,
    income_overrides: dict[str, dict] | None = None,
    cash_overrides: dict[str, dict] | None = None,
) -> FinancialDataService:
    company = _company()
    selected = periods or PERIODS
    income_overrides = income_overrides or {}
    cash_overrides = cash_overrides or {}
    income = [
        _income(period, **income_overrides.get(period, {}))
        for period in selected
    ]
    balance = [_balance(period) for period in selected]
    cash = [
        _cash(period, **cash_overrides.get(period, {}))
        for period in selected
    ]
    return FinancialDataService(
        companies=[company],
        income_statements=income,
        balance_sheets=balance,
        cash_flows=cash,
    )


def _understanding(question: str):
    return FinancialTaskUnderstandingBuilder(["贵州茅台"]).build(question)


def _build_plan(service: FinancialDataService, question: str):
    understanding = _understanding(question)
    resolver = FinancialSemanticResolver(service)
    return ExecutionPlanBuilder(semantic_resolver=resolver).build(
        understanding,
        plan_id="plan_execution_test",
    )


def test_complete_revenue_and_profit_plan_expands_all_tasks():
    plan = _build_plan(
        _service(),
        "分析贵州茅台 2021-2024 年收入和净利润",
    )

    assert plan.status == ExecutionPlanStatus.PENDING
    assert plan.plan_id == "plan_execution_test"
    assert len(plan.tasks) == 16  # 4 periods x revenue/profit/growth metrics

    revenue_2024 = next(
        task for task in plan.tasks
        if task.period == "2024" and task.metric_id == "revenue"
    )
    profit_2024 = next(
        task for task in plan.tasks
        if task.period == "2024" and task.metric_id == "net_profit"
    )

    assert revenue_2024.company_id == "600519"
    assert revenue_2024.company_name == "贵州茅台"
    assert revenue_2024.status == ExecutionStatus.PENDING
    assert revenue_2024.tool_id == "financial_metric_engine"
    assert profit_2024.status == ExecutionStatus.PENDING

    reported_tasks = [
        task for task in plan.tasks
        if task.metric_id in {"revenue", "net_profit"}
    ]
    assert reported_tasks
    assert all(task.missing_fields == [] for task in reported_tasks)


def test_growth_task_links_current_and_previous_reported_metric_tasks():
    plan = _build_plan(
        _service(),
        "分析贵州茅台 2021-2024 年收入增长率",
    )

    growth_2023 = next(
        task for task in plan.tasks
        if task.period == "2023" and task.metric_id == "revenue_growth"
    )
    revenue_2023 = next(
        task for task in plan.tasks
        if task.period == "2023" and task.metric_id == "revenue"
    )
    revenue_2022 = next(
        task for task in plan.tasks
        if task.period == "2022" and task.metric_id == "revenue"
    )

    assert growth_2023.dependencies == [
        revenue_2023.task_id,
        revenue_2022.task_id,
    ]
    assert growth_2023.inputs["growth_input_contract"] == {
        "base_metric_id": "revenue",
        "current_period": "2023",
        "previous_period": "2022",
        "required_inputs": [
            "current_base_metric",
            "previous_base_metric",
        ],
        "input_mode": "task_output_or_direct_data_source",
    }
    assert growth_2023.inputs["linked_dependency_periods"] == ["2023", "2022"]


def test_first_growth_period_is_unavailable_without_previous_data():
    plan = _build_plan(
        _service(),
        "分析贵州茅台 2021-2024 年收入增长率",
    )

    growth_2021 = next(
        task for task in plan.tasks
        if task.period == "2021" and task.metric_id == "revenue_growth"
    )

    assert growth_2021.status == ExecutionStatus.UNAVAILABLE
    assert growth_2021.status_reason == "MISSING_PREVIOUS_DATA"
    assert growth_2021.missing_fields == ["income_statement.revenue"]
    assert growth_2021.inputs["previous_period"] is None
    assert growth_2021.inputs["coverage_status"] == "MISSING_PREVIOUS_DATA"


def test_missing_requested_period_becomes_unavailable_tasks():
    plan = _build_plan(
        _service(periods=["2021", "2022", "2024"]),
        "分析贵州茅台 2021-2024 年收入和净利润",
    )

    missing_revenue = next(
        task for task in plan.tasks
        if task.period == "2023" and task.metric_id == "revenue"
    )
    missing_profit = next(
        task for task in plan.tasks
        if task.period == "2023" and task.metric_id == "net_profit"
    )

    assert missing_revenue.status == ExecutionStatus.UNAVAILABLE
    assert missing_revenue.status_reason == "PERIOD_MISSING"
    assert missing_revenue.inputs["coverage_status"] == "PERIOD_MISSING"
    assert missing_profit.status == ExecutionStatus.UNAVAILABLE
    assert missing_profit.status_reason == "PERIOD_MISSING"
    assert len(plan.unavailable_tasks) >= 2


def test_missing_metric_fields_become_unavailable_tasks():
    plan = _build_plan(
        _service(
            income_overrides={"2023": {"gross_profit": None}},
        ),
        "分析贵州茅台 2021-2024 年毛利率",
    )

    missing_margin = next(
        task for task in plan.tasks
        if task.period == "2023" and task.metric_id == "gross_margin"
    )

    assert missing_margin.status == ExecutionStatus.UNAVAILABLE
    assert missing_margin.status_reason == "MISSING_CURRENT_DATA"
    assert missing_margin.missing_fields == ["income_statement.gross_profit"]
    assert "0" not in [task.inputs["formula"] for task in plan.tasks]


def test_no_data_does_not_produce_pending_tasks():
    plan = _build_plan(
        _service(
            income_overrides={
                period: {"revenue": None, "gross_profit": None, "net_profit": None}
                for period in PERIODS
            },
            cash_overrides={
                period: {"operating_cash_flow": None, "capital_expenditure": None}
                for period in PERIODS
            },
        ),
        "分析贵州茅台 2021-2024 年收入",
    )

    assert plan.status == ExecutionPlanStatus.PENDING
    assert plan.pending_tasks == []
    assert plan.unavailable_tasks
    assert all(
        task.status_reason in {"MISSING_CURRENT_DATA", "MISSING_PREVIOUS_DATA"}
        for task in plan.unavailable_tasks
    )


def test_execution_plan_validation_detects_duplicate_and_bad_dependency():
    plan = _build_plan(
        _service(),
        "分析贵州茅台 2021-2024 年收入增长率",
    )

    # Force representative invalid states.
    plan.tasks[0].task_id = plan.tasks[1].task_id
    plan.tasks[2].dependencies.append("exec_missing")

    errors = ExecutionPlanBuilder.validate(plan)

    assert any("duplicate task IDs" in error for error in errors)
    assert any("unknown dependency" in error for error in errors)


def test_execution_plan_records_semantic_resolution():
    service = _service()
    understanding = _understanding("分析贵州茅台 2021-2024 年收入")
    resolver = FinancialSemanticResolver(service)
    plan = ExecutionPlanBuilder(semantic_resolver=resolver).build(
        understanding,
        plan_id="plan_execution_test",
    )

    assert plan.metadata["resolved_company_count"] == 1
    assert plan.metadata["resolved_metric_count"] >= 1
    assert plan.metadata["semantic_resolution"]["ready_for_planning"] is True
    assert plan.diagnostics == list(resolver.resolve(understanding).diagnostics)



def test_growth_task_reads_direct_data_when_current_base_task_is_absent():
    builder = ExecutionPlanBuilder(
        semantic_resolver=FinancialSemanticResolver(_service())
    )
    growth_task = ExecutionTask(
        task_id="exec_growth",
        tool_id="financial_metric_engine",
        tool_version="test",
        company_id="600519",
        company_name="贵州茅台",
        period="2023",
        metric_id="revenue_growth",
        metric_name="收入增长率",
        status=ExecutionStatus.PENDING,
        metadata={"metric_id": "revenue_growth", "previous_period": "2022"},
    )

    builder._link_dependencies([growth_task], {})

    assert growth_task.status == ExecutionStatus.PENDING
    assert growth_task.status_reason is None
    assert growth_task.dependencies == []
    assert growth_task.inputs["growth_dependency_mode"] == (
        "task_output_or_direct_data_source"
    )
    assert growth_task.inputs["linked_dependency_periods"] == []


def test_growth_task_reads_direct_data_when_previous_base_task_is_absent():
    builder = ExecutionPlanBuilder(
        semantic_resolver=FinancialSemanticResolver(_service())
    )
    growth_task = ExecutionTask(
        task_id="exec_growth",
        tool_id="financial_metric_engine",
        tool_version="test",
        company_id="600519",
        company_name="贵州茅台",
        period="2023",
        metric_id="revenue_growth",
        metric_name="收入增长率",
        status=ExecutionStatus.PENDING,
        metadata={"metric_id": "revenue_growth", "previous_period": "2022"},
    )

    builder._link_dependencies(
        [growth_task],
        {("600519", "2023", "revenue"): "exec_current_revenue"},
    )

    assert growth_task.status == ExecutionStatus.PENDING
    assert growth_task.status_reason is None
    assert growth_task.dependencies == ["exec_current_revenue"]
    assert growth_task.inputs["growth_dependency_mode"] == (
        "task_output_or_direct_data_source"
    )
    assert growth_task.inputs["linked_dependency_periods"] == ["2023"]

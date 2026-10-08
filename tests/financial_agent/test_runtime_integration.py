"""Runtime V10.2 integration tests."""

from __future__ import annotations

import pytest

from langgraph_langchain.runtime.financial.models import (
    BalanceSheetStatement,
    CashFlowStatement,
    Company,
    IncomeStatement,
)
from langgraph_langchain.runtime.financial import (
    FinancialAnalysisWorkflow,
    FinancialDataService,
)
from langgraph_langchain.runtime.financial_agent import (
    ExecutionPlanBuilder,
    ExecutionPlanStatus,
    ExecutionStatus,
    FinancialSemanticResolver,
    FinancialRuntimeExecutor,
    FinancialTaskUnderstandingBuilder,
    RuntimeExecutionStatus,
    RuntimeIntegrationError,
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


def _balance(period: str) -> BalanceSheetStatement:
    return BalanceSheetStatement(
        company_id="600519",
        period=period,
        total_assets=500.0,
        total_liabilities=200.0,
        total_equity=300.0,
        cash=150.0,
        accounts_receivable=20.0,
        inventory=30.0,
        fixed_assets=100.0,
        short_term_debt=50.0,
        long_term_debt=50.0,
        current_assets=200.0,
        current_liabilities=100.0,
        source_id=f"balance_{period}",
    )


def _cash(period: str) -> CashFlowStatement:
    return CashFlowStatement(
        company_id="600519",
        period=period,
        operating_cash_flow=35.0,
        investing_cash_flow=-10.0,
        financing_cash_flow=-5.0,
        capital_expenditure=12.0,
        free_cash_flow=23.0,
        source_id=f"cash_{period}",
    )


def _service() -> FinancialDataService:
    company = _company()
    return FinancialDataService(
        companies=[company],
        income_statements=[_income(period) for period in PERIODS],
        balance_sheets=[_balance(period) for period in PERIODS],
        cash_flows=[_cash(period) for period in PERIODS],
    )


def _understanding(question: str):
    return FinancialTaskUnderstandingBuilder(["贵州茅台"]).build(question)


def _build_plan(question: str):
    understanding = _understanding(question)
    resolver = FinancialSemanticResolver(_service())
    return understanding, resolver, ExecutionPlanBuilder(
        semantic_resolver=resolver
    ).build(understanding, plan_id="plan_runtime_test")


def test_runtime_executor_completes_plan_and_preserves_evidence():
    understanding, resolver, execution_plan = _build_plan(
        "分析贵州茅台 2022-2024 年收入和净利润"
    )
    workflow = FinancialAnalysisWorkflow(_service())
    result = FinancialRuntimeExecutor(workflow).execute_plan(
        execution_plan,
        understanding=understanding,
    )

    assert result.status == RuntimeExecutionStatus.SUCCEEDED
    assert execution_plan.status == ExecutionPlanStatus.SUCCEEDED
    assert result.succeeded_count == len(plan_tasks := execution_plan.tasks)
    assert result.unavailable_count == 0
    assert result.failed_count == 0
    assert result.report_markdown
    assert result.metadata["evidence_count"] > 0
    assert result.metadata["finding_count"] > 0

    for task_result in result.task_results:
        assert task_result.status == ExecutionStatus.SUCCEEDED
        assert task_result.verification_status.value == "PASSED"
        assert task_result.calculation_id
        assert task_result.verification_id
        assert task_result.evidence_id
        assert len(task_result.output_refs) == 3

    revenue_2024 = next(
        item for item in result.task_results
        if item.period == "2024" and item.metric_id == "revenue"
    )
    assert revenue_2024.company_id == "600519"

    assert resolver.resolve(understanding).ready_for_planning is True
    assert len(plan_tasks) == 12


def test_runtime_executor_maps_first_missing_previous_period_to_unavailable():
    understanding, resolver, execution_plan = _build_plan(
        "分析贵州茅台 2021-2024 年收入和净利润"
    )
    workflow = FinancialAnalysisWorkflow(_service())
    result = FinancialRuntimeExecutor(workflow).execute_plan(
        execution_plan,
        understanding=understanding,
    )

    assert result.status == RuntimeExecutionStatus.PARTIAL
    assert execution_plan.status == ExecutionPlanStatus.PARTIAL
    assert result.succeeded_count == 14
    assert result.unavailable_count == 2
    assert result.failed_count == 0

    unavailable = [
        item for item in result.task_results
        if item.status == ExecutionStatus.UNAVAILABLE
    ]
    assert {item.metric_id for item in unavailable} == {
        "revenue_growth", "net_profit_growth"
    }
    assert all(item.period == "2021" for item in unavailable)
    assert all(
        item.status_reason in {
            "MISSING_PREVIOUS_DATA", "growth_previous_period_missing"
        }
        for item in unavailable
    )

    succeeded = [
        item for item in result.task_results
        if item.status == ExecutionStatus.SUCCEEDED
    ]
    assert len(succeeded) == result.succeeded_count
    assert all(item.evidence_id for item in succeeded)
    assert result.metadata["evidence_count"] >= len(succeeded)


def test_runtime_executor_rejects_non_pending_plan():
    understanding, resolver, execution_plan = _build_plan(
        "分析贵州茅台 2022-2024 年收入"
    )
    execution_plan.status = ExecutionPlanStatus.SUCCEEDED

    with pytest.raises(RuntimeIntegrationError, match="must be PENDING"):
        FinancialRuntimeExecutor(FinancialAnalysisWorkflow(_service())).execute_plan(
            execution_plan,
            understanding=understanding,
        )

"""Runtime V10.4 financial period semantics and auditability tests.

Covers the first V10.4 milestone:

1. Explicit workflow queries select exactly the requested canonical periods;
   quarter, half-year, and annual data for the same fiscal year are never
   mixed into one analysis result.
2. Quarterly average-balance metrics use distinct quarter balance sheets and
   match independently hand-computed golden values.
3. End-to-end: natural-language quarter questions resolve to exact quarter
   tasks, and every observation, calculation, verification, and evidence
   item stays consistent with the requested period.
"""

from __future__ import annotations

import pytest

from langgraph_langchain.runtime.financial import (
    FinancialAnalysisWorkflow,
    FinancialDataService,
    FinancialQuery,
    financial_metric_registry,
)
from langgraph_langchain.runtime.financial_agent import (
    ExecutionPlanBuilder,
    ExecutionStatus,
    FinancialRuntimeExecutor,
    FinancialSemanticResolver,
    FinancialTaskUnderstandingBuilder,
    RuntimeExecutionStatus,
)
from langgraph_langchain.runtime.financial_agent.task_executor import (
    TaskLevelFinancialExecutor,
    TaskOutputStore,
)
from langgraph_langchain.runtime.financial_agent.execution_models import ExecutionTask

from test_runtime_integration import _cash, _company, _income


_MIXED_PERIODS = ["2022Q1", "2022Q4", "2023Q1", "2023Q2", "2023H1", "2023"]


_MIXED_REVENUE = {
    "2022Q1": 250.0,
    "2022Q4": 1100.0,
    "2023Q1": 300.0,
    "2023Q2": 650.0,
    "2023H1": 470.0,
    "2023": 1300.0,
}


# Quarterly balance sheets with distinct values so average-balance metrics
# can be verified against hand-computed expectations.
_QUARTER_BALANCES = {
    "2022Q1": {
        "total_assets": 350.0, "total_liabilities": 175.0, "total_equity": 175.0,
        "cash": 110.0, "accounts_receivable": 7.0, "inventory": 14.0,
        "fixed_assets": 90.0, "short_term_debt": 40.0, "long_term_debt": 40.0,
        "current_assets": 170.0, "current_liabilities": 85.0,
    },
    "2022Q4": {
        "total_assets": 400.0, "total_liabilities": 200.0, "total_equity": 200.0,
        "cash": 125.0, "accounts_receivable": 10.0, "inventory": 20.0,
        "fixed_assets": 92.0, "short_term_debt": 42.0, "long_term_debt": 42.0,
        "current_assets": 180.0, "current_liabilities": 90.0,
    },
    "2023Q1": {
        "total_assets": 500.0, "total_liabilities": 200.0, "total_equity": 300.0,
        "cash": 150.0, "accounts_receivable": 30.0, "inventory": 40.0,
        "fixed_assets": 100.0, "short_term_debt": 50.0, "long_term_debt": 50.0,
        "current_assets": 200.0, "current_liabilities": 100.0,
    },
    "2023Q2": {
        "total_assets": 650.0, "total_liabilities": 250.0, "total_equity": 400.0,
        "cash": 190.0, "accounts_receivable": 45.0, "inventory": 60.0,
        "fixed_assets": 108.0, "short_term_debt": 55.0, "long_term_debt": 55.0,
        "current_assets": 230.0, "current_liabilities": 115.0,
    },
    "2023H1": {
        "total_assets": 700.0, "total_liabilities": 270.0, "total_equity": 430.0,
        "cash": 200.0, "accounts_receivable": 50.0, "inventory": 65.0,
        "fixed_assets": 110.0, "short_term_debt": 56.0, "long_term_debt": 56.0,
        "current_assets": 240.0, "current_liabilities": 120.0,
    },
    "2023": {
        "total_assets": 750.0, "total_liabilities": 280.0, "total_equity": 470.0,
        "cash": 210.0, "accounts_receivable": 55.0, "inventory": 70.0,
        "fixed_assets": 112.0, "short_term_debt": 58.0, "long_term_debt": 58.0,
        "current_assets": 250.0, "current_liabilities": 125.0,
    },
}


def _balance_for(period: str):
    from langgraph_langchain.runtime.financial.models import BalanceSheetStatement

    values = dict(_QUARTER_BALANCES[period])
    return BalanceSheetStatement(
        company_id="600519",
        period=period,
        **values,
        source_id=f"balance_{period}",
    )


def _income_for(period: str):
    """Cumulative income statements (Chinese quarterly reporting style)."""
    revenue = _MIXED_REVENUE[period]
    cost = round(revenue * 0.3, 6)
    net_profit = round(revenue * 0.2, 6)
    return _income(
        period,
        revenue=revenue,
        cost_of_revenue=cost,
        gross_profit=round(revenue - cost, 6),
        net_profit=net_profit,
        net_profit_attributable=round(net_profit * 0.97, 6),
    )


def _mixed_service() -> FinancialDataService:
    return FinancialDataService(
        companies=[_company()],
        income_statements=[_income_for(period) for period in _MIXED_PERIODS],
        balance_sheets=[_balance_for(period) for period in _MIXED_PERIODS],
        cash_flows=[_cash(period) for period in _MIXED_PERIODS],
    )


def _quarter_service() -> FinancialDataService:
    quarters = ["2022Q1", "2022Q4", "2023Q1", "2023Q2"]
    return FinancialDataService(
        companies=[_company()],
        income_statements=[_income_for(period) for period in quarters],
        balance_sheets=[_balance_for(period) for period in quarters],
        cash_flows=[_cash(period) for period in quarters],
    )


def _metric_task(task_number: int, metric_id: str, period: str) -> ExecutionTask:
    definition = financial_metric_registry.get(metric_id)
    return ExecutionTask(
        task_id=f"exec_v104_{task_number:04d}",
        tool_id="financial_metric_engine",
        tool_version="v10.4.0",
        company_id="600519",
        company_name="贵州茅台",
        period=period,
        metric_id=metric_id,
        metric_name=definition.name,
    )


def test_quarterly_average_balance_golden_values():
    """Quarter ROA/ROE/turnover golden values, hand-computed expectations."""
    workflow = FinancialAnalysisWorkflow(_quarter_service())
    executor = TaskLevelFinancialExecutor(workflow, TaskOutputStore())
    # 2023Q1: numerator from cumulative Q1, denominator avg(2022Q4, 2023Q1).
    # 2023Q2: numerator from cumulative Q2, denominator avg(2023Q1, 2023Q2).
    expected = {
        ("roa", "2023Q1"): {
            "numerator": 60.0, "current": 500.0, "previous": 400.0,
            "average": 450.0, "result": 13.333333,
        },
        ("roa", "2023Q2"): {
            "numerator": 130.0, "current": 650.0, "previous": 500.0,
            "average": 575.0, "result": 22.608696,
        },
        ("roe", "2023Q1"): {
            "numerator": 60.0, "current": 300.0, "previous": 200.0,
            "average": 250.0, "result": 24.0,
        },
        ("roe", "2023Q2"): {
            "numerator": 130.0, "current": 400.0, "previous": 300.0,
            "average": 350.0, "result": 37.142857,
        },
        ("asset_turnover", "2023Q1"): {
            "numerator": 300.0, "current": 500.0, "previous": 400.0,
            "average": 450.0, "result": 0.666667,
        },
        ("asset_turnover", "2023Q2"): {
            "numerator": 650.0, "current": 650.0, "previous": 500.0,
            "average": 575.0, "result": 1.130435,
        },
        ("receivable_turnover", "2023Q1"): {
            "numerator": 300.0, "current": 30.0, "previous": 10.0,
            "average": 20.0, "result": 15.0,
        },
        ("receivable_turnover", "2023Q2"): {
            "numerator": 650.0, "current": 45.0, "previous": 30.0,
            "average": 37.5, "result": 17.333333,
        },
        ("inventory_turnover", "2023Q1"): {
            "numerator": 90.0, "current": 40.0, "previous": 20.0,
            "average": 30.0, "result": 3.0,
        },
        ("inventory_turnover", "2023Q2"): {
            "numerator": 195.0, "current": 60.0, "previous": 40.0,
            "average": 50.0, "result": 3.9,
        },
    }
    for task_number, ((metric_id, period), values) in enumerate(
        expected.items(), start=1
    ):
        outcome = executor(_metric_task(task_number, metric_id, period))

        assert outcome.status == ExecutionStatus.SUCCEEDED, (
            f"{metric_id}/{period}: {outcome.status_reason}"
        )
        calculation = outcome.output.calculation
        assert calculation.inputs["numerator"] == pytest.approx(values["numerator"])
        assert calculation.inputs["current_denominator"] == pytest.approx(values["current"])
        assert calculation.inputs["previous_denominator"] == pytest.approx(values["previous"])
        assert calculation.inputs["average_denominator"] == pytest.approx(values["average"])
        assert calculation.result == pytest.approx(values["result"])
        assert calculation.source_ids == [
            f"income_{period}",
            f"balance_{period}",
            f"balance_{_previous_quarter(period)}",
        ]
        assert outcome.output.verification.passed is True
        assert outcome.output.verification.actual_value == pytest.approx(values["result"])
        assert outcome.output.observation.period == period
        assert outcome.output.observation.balance_policy == "average_balance"


def _previous_quarter(period: str) -> str:
    year, quarter = period[:4], int(period[-1])
    if quarter == 1:
        return f"{int(year) - 1}Q4"
    return f"{year}Q{quarter - 1}"


def test_explicit_query_periods_do_not_mix_quarter_half_and_fy():
    """Workflow queries with explicit periods select exactly those periods."""
    workflow = FinancialAnalysisWorkflow(_mixed_service())

    result = workflow.run(FinancialQuery(
        question="分析贵州茅台 2023Q1 营业收入",
        metrics=["revenue"],
        company_names=["贵州茅台"],
        start_year=2022,
        end_year=2023,
        periods=["2023Q1"],
    ))
    revenue_observations = [
        item for item in result.observations if item.metric_id == "revenue"
    ]
    assert [item.period for item in revenue_observations] == ["2023Q1"]
    assert revenue_observations[0].value == 300.0

    result = workflow.run(FinancialQuery(
        question="分析贵州茅台 2023 上半年营业收入",
        metrics=["revenue"],
        company_names=["贵州茅台"],
        start_year=2023,
        end_year=2023,
        periods=["2023H1"],
    ))
    revenue_observations = [
        item for item in result.observations if item.metric_id == "revenue"
    ]
    assert [item.period for item in revenue_observations] == ["2023H1"]
    assert revenue_observations[0].value == 470.0


def test_query_without_explicit_periods_keeps_year_range_behavior():
    """Backward compatibility: no explicit periods selects the year range."""
    workflow = FinancialAnalysisWorkflow(_mixed_service())
    result = workflow.run(FinancialQuery(
        question="分析贵州茅台 2022-2023 年营业收入",
        metrics=["revenue"],
        company_names=["贵州茅台"],
        start_year=2022,
        end_year=2023,
    ))
    revenue_periods = sorted(
        item.period
        for item in result.observations
        if item.metric_id == "revenue"
    )
    assert revenue_periods == sorted(_MIXED_PERIODS)


def test_quarter_question_resolves_to_exact_quarter_plan():
    """2023Q1 questions resolve to 2023Q1 tasks, never annual fallback."""
    service = _mixed_service()
    understanding = FinancialTaskUnderstandingBuilder(["贵州茅台"]).build(
        "分析贵州茅台 2023Q1 总资产收益率和净资产收益率"
    )
    resolver = FinancialSemanticResolver(service)
    plan = ExecutionPlanBuilder(semantic_resolver=resolver).build(
        understanding,
        plan_id="plan_v104_quarter",
    )
    task_periods = {task.period for task in plan.tasks}
    assert task_periods == {"2023Q1"}
    # The profitability objective expands to its full metric family, but it
    # must include the explicitly requested roa/roe and never fall back to
    # annual periods.
    assert {"roa", "roe"} <= {
        task.metric_id for task in plan.tasks
    }


def test_end_to_end_quarter_execution_keeps_period_and_evidence_consistent():
    """Full path: question -> plan -> DAG execution -> consistent evidence."""
    service = _mixed_service()
    workflow = FinancialAnalysisWorkflow(service)
    understanding = FinancialTaskUnderstandingBuilder(["贵州茅台"]).build(
        "分析贵州茅台 2023Q1 总资产收益率和净资产收益率"
    )
    resolver = FinancialSemanticResolver(service)
    plan = ExecutionPlanBuilder(semantic_resolver=resolver).build(
        understanding,
        plan_id="plan_v104_e2e",
    )

    result = FinancialRuntimeExecutor(workflow).execute_plan(
        plan,
        understanding=understanding,
    )

    assert result.status == RuntimeExecutionStatus.SUCCEEDED
    assert result.failed_count == 0
    assert result.unavailable_count == 0

    analysis = result.analysis_result
    # No half-year or annual data may leak into the quarter analysis.
    assert {item.period for item in analysis.observations} == {"2023Q1"}
    assert {item.period for item in analysis.calculations} == {"2023Q1"}
    assert {item.period for item in analysis.verifications} == {"2023Q1"}
    assert {item.period for item in analysis.evidence} == {"2023Q1"}

    calculations = {
        (item.company_id, item.period, item.metric_id): item
        for item in analysis.calculations
    }
    verifications = {item.verification_id: item for item in analysis.verifications}
    evidence_items = {item.evidence_id: item for item in analysis.evidence}

    for task_result in result.task_results:
        assert task_result.status == ExecutionStatus.SUCCEEDED
        key = (task_result.company_id, task_result.period, task_result.metric_id)
        calculation = calculations[key]
        verification = verifications[task_result.verification_id]
        evidence = evidence_items[task_result.evidence_id]
        assert task_result.period == "2023Q1"
        assert verification.calculation_id == calculation.calculation_id
        assert verification.status == "passed"
        assert evidence.calculation_id == calculation.calculation_id
        assert evidence.verification_result_id == verification.verification_id
        assert evidence.period == calculation.period
        # Every evidence source must resolve back to a real data source.
        resolved_sources = service.get_sources(evidence.source_ids)
        assert len(resolved_sources) == len(set(evidence.source_ids))
        assert len(resolved_sources) > 0
        for source in resolved_sources:
            assert source.company_id == "600519"

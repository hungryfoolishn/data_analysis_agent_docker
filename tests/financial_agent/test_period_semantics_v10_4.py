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
from langgraph_langchain.runtime.financial.period import (
    PeriodBasisType,
    PeriodComparisonType,
    PeriodNormalizer,
)
from langgraph_langchain.runtime.financial_agent import (
    ExecutionPlanBuilder,
    ExecutionPlanStatus,
    ExecutionStatus,
    FinancialRuntimeExecutor,
    FinancialSemanticResolver,
    FinancialTaskUnderstandingBuilder,
    PlanningReadiness,
    RuntimeExecutionStatus,
    RuntimeIntegrationError,
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
    "2022Q2": {
        "total_assets": 380.0, "total_liabilities": 160.0, "total_equity": 190.0,
        "cash": 125.0, "accounts_receivable": 8.0, "inventory": 15.0,
        "fixed_assets": 92.0, "short_term_debt": 42.0, "long_term_debt": 42.0,
        "current_assets": 185.0, "current_liabilities": 92.0,
    },
    "2022Q2": {
        "total_assets": 420.0, "total_liabilities": 195.0, "total_equity": 220.0,
        "cash": 115.0, "accounts_receivable": 12.0, "inventory": 22.0,
        "fixed_assets": 91.0, "short_term_debt": 41.0, "long_term_debt": 41.0,
        "current_assets": 175.0, "current_liabilities": 88.0,
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


_GROWTH_INTENT_REVENUE = {
    "2022Q1": 80.0,
    "2022Q2": 180.0,
    "2023Q1": 100.0,
    "2023Q2": 220.0,
}


def _growth_intent_service() -> FinancialDataService:
    return FinancialDataService(
        companies=[_company()],
        income_statements=[
            _income(
                period,
                revenue=revenue,
                cost_of_revenue=revenue * 0.3,
                gross_profit=revenue * 0.7,
                operating_profit=revenue * 0.2,
                net_profit=revenue * 0.2,
                net_profit_attributable=revenue * 0.19,
            )
            for period, revenue in _GROWTH_INTENT_REVENUE.items()
        ],
        balance_sheets=[_balance_for(period) for period in _GROWTH_INTENT_REVENUE],
        cash_flows=[_cash(period) for period in _GROWTH_INTENT_REVENUE],
    )


def test_growth_intent_maps_natural_language_to_period_basis():
    builder = FinancialTaskUnderstandingBuilder(["贵州茅台"])
    expected = {
        "分析贵州茅台 2023Q2 收入同比增长率": (
            PeriodComparisonType.YEAR_OVER_YEAR,
            PeriodBasisType.SINGLE_PERIOD,
        ),
        "分析贵州茅台 2023Q2 收入环比增长率": (
            PeriodComparisonType.SEQUENTIAL,
            PeriodBasisType.SINGLE_PERIOD,
        ),
        "分析贵州茅台 2023Q2 累计收入同比增长率": (
            PeriodComparisonType.YEAR_OVER_YEAR,
            PeriodBasisType.CUMULATIVE,
        ),
    }
    for question, (comparison_type, period_basis) in expected.items():
        understanding = builder.build(question)
        assert understanding.growth_comparison_type == comparison_type
        assert understanding.growth_period_basis == period_basis


def test_growth_intent_end_to_end_uses_auditable_comparison_contract():
    service = _growth_intent_service()
    expected = {
        "分析贵州茅台 2023Q2 收入同比增长率": {
            "comparison_type": PeriodComparisonType.YEAR_OVER_YEAR,
            "period_basis": PeriodBasisType.SINGLE_PERIOD,
            "previous_period": "2022Q2",
            "result": 20.0,
            "formula_label": "同比/单期",
            "source_ids": {
                "income_2023Q2",
                "income_2023Q1",
                "income_2022Q2",
                "income_2022Q1",
            },
        },
        "分析贵州茅台 2023Q2 收入环比增长率": {
            "comparison_type": PeriodComparisonType.SEQUENTIAL,
            "period_basis": PeriodBasisType.SINGLE_PERIOD,
            "previous_period": "2023Q1",
            "result": 20.0,
            "formula_label": "环比/单期",
            "source_ids": {
                "income_2023Q2",
                "income_2023Q1",
            },
        },
        "分析贵州茅台 2023Q2 累计收入同比增长率": {
            "comparison_type": PeriodComparisonType.YEAR_OVER_YEAR,
            "period_basis": PeriodBasisType.CUMULATIVE,
            "previous_period": "2022Q2",
            "result": 22.222222,
            "formula_label": "同比/累计",
            "source_ids": {
                "income_2023Q2",
                "income_2022Q2",
            },
        },
    }

    for question, expected_values in expected.items():
        understanding = FinancialTaskUnderstandingBuilder(["贵州茅台"]).build(
            question
        )
        assert understanding.growth_comparison_type == expected_values[
            "comparison_type"
        ]
        assert understanding.growth_period_basis == expected_values["period_basis"]

        plan = ExecutionPlanBuilder(
            semantic_resolver=FinancialSemanticResolver(service),
        ).build(understanding, plan_id=f"plan_{understanding.query_id}")

        assert plan.metadata["growth_comparison_type"] == expected_values[
            "comparison_type"
        ].value
        assert plan.metadata["growth_period_basis"] == expected_values[
            "period_basis"
        ].value

        growth_task = next(
            task for task in plan.tasks if task.metric_id == "revenue_growth"
        )
        assert growth_task.inputs["previous_period"] == expected_values[
            "previous_period"
        ]
        assert growth_task.inputs["growth_comparison_type"] == expected_values[
            "comparison_type"
        ].value
        assert growth_task.inputs["growth_period_basis"] == expected_values[
            "period_basis"
        ].value

        result = FinancialRuntimeExecutor(
            FinancialAnalysisWorkflow(service),
        ).execute_plan(plan, understanding=understanding)
        assert result.status == RuntimeExecutionStatus.SUCCEEDED

        growth_result = next(
            item for item in result.task_results
            if item.metric_id == "revenue_growth"
        )
        assert growth_result.status == ExecutionStatus.SUCCEEDED
        assert growth_result.metadata["growth_comparison_type"] == expected_values[
            "comparison_type"
        ].value
        assert growth_result.metadata["growth_period_basis"] == expected_values[
            "period_basis"
        ].value
        assert growth_result.metadata["comparison_period"] == expected_values[
            "previous_period"
        ]

        calculation = next(
            item for item in result.analysis_result.calculations
            if item.metric_id == "revenue_growth"
        )
        assert calculation.result == pytest.approx(expected_values["result"])
        assert expected_values["formula_label"] in calculation.formula
        assert expected_values["source_ids"].issubset(set(calculation.source_ids))

        evidence = next(
            item for item in result.analysis_result.evidence
            if item.metric_id == "revenue_growth"
        )
        assert evidence.formula == calculation.formula
        assert evidence.source_ids == calculation.source_ids


def test_simultaneous_yoy_and_qoq_create_independent_growth_tasks():
    service = _growth_intent_service()
    question = "分析贵州茅台 2023Q2 收入同比和环比增长率"
    understanding = FinancialTaskUnderstandingBuilder(["贵州茅台"]).build(question)

    assert understanding.growth_intent_source == "explicit"
    assert [
        (item.comparison_type, item.period_basis)
        for item in understanding.growth_intents
    ] == [
        (PeriodComparisonType.YEAR_OVER_YEAR, PeriodBasisType.SINGLE_PERIOD),
        (PeriodComparisonType.SEQUENTIAL, PeriodBasisType.SINGLE_PERIOD),
    ]

    plan = ExecutionPlanBuilder(
        semantic_resolver=FinancialSemanticResolver(service),
    ).build(understanding, plan_id="plan_growth_multi_intent")

    assert plan.metadata["growth_intent_source"] == "explicit"
    assert len(plan.metadata["growth_intents"]) == 2
    growth_tasks = [
        task for task in plan.tasks if task.metric_id == "revenue_growth"
    ]
    assert len(growth_tasks) == 2
    assert {
        task.inputs["growth_comparison_variant"]
        for task in growth_tasks
    } == {
        "year_over_year:single_period",
        "sequential:single_period",
    }
    assert all(
        task.inputs["previous_period"] == "2022Q2"
        for task in growth_tasks
        if task.inputs["growth_comparison_type"] == "year_over_year"
    )
    assert all(
        task.inputs["previous_period"] == "2023Q1"
        for task in growth_tasks
        if task.inputs["growth_comparison_type"] == "sequential"
    )

    result = FinancialRuntimeExecutor(
        FinancialAnalysisWorkflow(service),
    ).execute_plan(plan, understanding=understanding)

    assert result.status == RuntimeExecutionStatus.SUCCEEDED
    growth_results = [
        item for item in result.task_results if item.metric_id == "revenue_growth"
    ]
    assert len(growth_results) == 2
    assert {
        item.metadata["growth_comparison_variant"]
        for item in growth_results
    } == {
        "year_over_year:single_period",
        "sequential:single_period",
    }
    assert all(item.status == ExecutionStatus.SUCCEEDED for item in growth_results)
    growth_calculations = [
        item.result
        for item in result.analysis_result.calculations
        if item.metric_id == "revenue_growth"
    ]
    assert len(growth_calculations) == 2
    assert all(value == pytest.approx(20.0) for value in growth_calculations)


def test_growth_intent_default_is_explicit_and_recorded():
    understanding = FinancialTaskUnderstandingBuilder(["贵州茅台"]).build(
        "分析贵州茅台 2023Q2 收入增长情况"
    )
    assert understanding.growth_intent_source == "default"
    assert understanding.growth_comparison_type == PeriodComparisonType.YEAR_OVER_YEAR
    assert understanding.growth_period_basis == PeriodBasisType.SINGLE_PERIOD

    plan = ExecutionPlanBuilder(
        semantic_resolver=FinancialSemanticResolver(_growth_intent_service()),
    ).build(understanding, plan_id="plan_growth_default")
    assert plan.metadata["growth_intent_source"] == "default"
    assert plan.metadata["growth_intents"] == [{
        "comparison_type": "year_over_year",
        "period_basis": "single_period",
        "source": "default",
    }]


def test_conflicting_growth_comparison_requires_clarification():
    understanding = FinancialTaskUnderstandingBuilder(["贵州茅台"]).build(
        "分析贵州茅台 2023Q2 收入增长率，同比和环比二选一"
    )
    assert "GROWTH_COMPARISON_CONFLICT" in understanding.growth_intent_diagnostics
    assert understanding.growth_intent_source == "conflict"


def test_ambiguous_growth_comparison_requires_clarification():
    understanding = FinancialTaskUnderstandingBuilder(["贵州茅台"]).build(
        "分析贵州茅台 2023Q2 收入增长率，同比还是环比"
    )
    assert "GROWTH_COMPARISON_AMBIGUOUS" in understanding.growth_intent_diagnostics
    assert understanding.growth_intent_source == "ambiguous"
    assert understanding.growth_intents == []


def test_growth_comparison_period_boundaries_are_explicit():
    normalizer = PeriodNormalizer()
    expected = {
        ("2023Q1", PeriodComparisonType.YEAR_OVER_YEAR): "2022Q1",
        ("2023Q1", PeriodComparisonType.SEQUENTIAL): "2022Q4",
        ("2023Q2", PeriodComparisonType.YEAR_OVER_YEAR): "2022Q2",
        ("2023Q2", PeriodComparisonType.SEQUENTIAL): "2023Q1",
        ("2023Q3", PeriodComparisonType.YEAR_OVER_YEAR): "2022Q3",
        ("2023Q3", PeriodComparisonType.SEQUENTIAL): "2023Q2",
        ("2023Q4", PeriodComparisonType.YEAR_OVER_YEAR): "2022Q4",
        ("2023Q4", PeriodComparisonType.SEQUENTIAL): "2023Q3",
        ("2023H1", PeriodComparisonType.YEAR_OVER_YEAR): "2022H1",
        ("2023H2", PeriodComparisonType.YEAR_OVER_YEAR): "2022H2",
        ("2023H2", PeriodComparisonType.SEQUENTIAL): "2023H1",
    }
    for (period, comparison_type), expected_period in expected.items():
        parsed = normalizer.parse(period)
        assert parsed.comparison_period(comparison_type).normalized_period == (
            expected_period
        )


def test_growth_intent_conflict_blocks_plan_execution():
    understanding = FinancialTaskUnderstandingBuilder(["贵州茅台"]).build(
        "分析贵州茅台 2023Q2 收入增长率，同比和环比二选一"
    )
    assert understanding.planning_readiness == PlanningReadiness.NEEDS_CLARIFICATION
    assert understanding.growth_intent_source == "conflict"
    assert understanding.growth_intents == []
    assert "GROWTH_COMPARISON_CONFLICT" in understanding.growth_intent_diagnostics

    plan = ExecutionPlanBuilder(
        semantic_resolver=FinancialSemanticResolver(_growth_intent_service()),
    ).build(understanding, plan_id="plan_growth_conflict")

    assert plan.status == ExecutionPlanStatus.BLOCKED
    assert plan.tasks == []
    assert "GROWTH_COMPARISON_CONFLICT" in plan.diagnostics
    assert "GROWTH_COMPARISON_REQUIRES_CLARIFICATION" in plan.diagnostics
    assert plan.metadata["planning_readiness"] == "NEEDS_CLARIFICATION"
    assert plan.metadata["growth_intent_source"] == "conflict"

    result = FinancialRuntimeExecutor(
        FinancialAnalysisWorkflow(_growth_intent_service()),
    ).execute_plan(plan, understanding=understanding)
    _assert_structured_blocked_result(result, plan, "conflict")


def test_growth_intent_ambiguity_blocks_plan_execution():
    understanding = FinancialTaskUnderstandingBuilder(["贵州茅台"]).build(
        "分析贵州茅台 2023Q2 收入增长率，同比还是环比"
    )
    assert understanding.planning_readiness == PlanningReadiness.NEEDS_CLARIFICATION
    assert understanding.growth_intent_source == "ambiguous"
    assert understanding.growth_intents == []
    assert "GROWTH_COMPARISON_AMBIGUOUS" in understanding.growth_intent_diagnostics

    plan = ExecutionPlanBuilder(
        semantic_resolver=FinancialSemanticResolver(_growth_intent_service()),
    ).build(understanding, plan_id="plan_growth_ambiguous")

    assert plan.status == ExecutionPlanStatus.BLOCKED
    assert plan.tasks == []
    assert "GROWTH_COMPARISON_AMBIGUOUS" in plan.diagnostics
    assert "GROWTH_COMPARISON_REQUIRES_CLARIFICATION" in plan.diagnostics
    assert plan.metadata["planning_readiness"] == "NEEDS_CLARIFICATION"
    assert plan.metadata["growth_intent_source"] == "ambiguous"

    result = FinancialRuntimeExecutor(
        FinancialAnalysisWorkflow(_growth_intent_service()),
    ).execute_plan(plan, understanding=understanding)
    _assert_structured_blocked_result(result, plan, "ambiguous")


def test_cumulative_yoy_and_qoq_generate_only_cumulative_intents():
    understanding = FinancialTaskUnderstandingBuilder(["贵州茅台"]).build(
        "分析贵州茅台 2023Q2 累计收入同比和环比"
    )

    assert [
        (item.comparison_type, item.period_basis)
        for item in understanding.growth_intents
    ] == [
        (PeriodComparisonType.YEAR_OVER_YEAR, PeriodBasisType.CUMULATIVE),
        (PeriodComparisonType.SEQUENTIAL, PeriodBasisType.CUMULATIVE),
    ]

    plan = ExecutionPlanBuilder(
        semantic_resolver=FinancialSemanticResolver(_growth_intent_service()),
    ).build(understanding, plan_id="plan_cumulative_multi_intent")
    growth_tasks = [
        task for task in plan.tasks if task.metric_id == "revenue_growth"
    ]
    assert len(growth_tasks) == 2
    assert {
        task.inputs["growth_comparison_variant"]
        for task in growth_tasks
    } == {
        "year_over_year:cumulative",
        "sequential:cumulative",
    }
    assert all(
        task.inputs["growth_period_basis"] == "cumulative"
        for task in growth_tasks
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

def _assert_structured_blocked_result(result, plan, expected_intent_source):
    """Phase-5 contract: blocked plans produce structured results, not raises."""
    assert result.status == RuntimeExecutionStatus.BLOCKED
    assert result.task_results == []
    assert result.succeeded_count == 0
    assert result.unavailable_count == 0
    assert result.failed_count == 0
    assert result.blocked_count == 0
    assert result.analysis_result is None
    assert result.metadata["blocked_before_execution"] is True
    assert result.metadata["growth_intent_source"] == expected_intent_source
    assert "GROWTH_COMPARISON_REQUIRES_CLARIFICATION" in (
        result.metadata["blocking_diagnostics"]
    )
    assert plan.status == ExecutionPlanStatus.BLOCKED
    assert plan.finished_at is not None
    # The blocked report must explain itself and never fake a conclusion.
    assert "财务分析已阻断" in result.report_markdown
    assert "未生成任何财务计算结果" in result.report_markdown
    assert "同比" in result.report_markdown and "环比" in result.report_markdown


def test_blocked_intent_structured_result_in_both_execution_modes():
    understanding = FinancialTaskUnderstandingBuilder(["贵州茅台"]).build(
        "分析贵州茅台 2023Q2 收入增长率，同比和环比二选一"
    )
    plan = ExecutionPlanBuilder(
        semantic_resolver=FinancialSemanticResolver(_growth_intent_service()),
    ).build(understanding, plan_id="plan_growth_conflict_structured")
    workflow_obj = FinancialAnalysisWorkflow(_growth_intent_service())

    for execution_mode in ("dag", "workflow"):
        plan_copy = plan.model_copy(deep=True)
        plan_copy.status = ExecutionPlanStatus.BLOCKED
        result = FinancialRuntimeExecutor(
            workflow_obj,
            execution_mode=execution_mode,
        ).execute_plan(plan_copy, understanding=understanding)
        _assert_structured_blocked_result(result, plan_copy, "conflict")


# ---------------------------------------------------------------------------
# V10.4 阶段 5：增长率基期、累计口径与多意图证据一致性验收
# ---------------------------------------------------------------------------

# Cumulative revenue values. Single-quarter values are derived:
# 2022Q1=250, 2022Q2=350, 2023Q1=300, 2023Q2=500.
_GROWTH_BASIS_REVENUE = {
    "2022Q1": 250.0,
    "2022Q2": 600.0,
    "2023Q1": 300.0,
    "2023Q2": 800.0,
}


def _growth_basis_service() -> FinancialDataService:
    return FinancialDataService(
        companies=[_company()],
        income_statements=[
            _income(
                period,
                revenue=revenue,
                cost_of_revenue=revenue * 0.3,
                gross_profit=revenue * 0.7,
                operating_profit=revenue * 0.2,
                net_profit=revenue * 0.2,
                net_profit_attributable=revenue * 0.19,
            )
            for period, revenue in _GROWTH_BASIS_REVENUE.items()
        ],
        balance_sheets=[_balance_for(period) for period in _GROWTH_BASIS_REVENUE],
        cash_flows=[_cash(period) for period in _GROWTH_BASIS_REVENUE],
    )


def _growth_results_by_variant(result, *, plan_index=0):
    """Map executed growth outputs by their comparison variant."""
    outputs = {}
    calculations = {
        item.calculation_id: item
        for item in result.analysis_result.calculations
    }
    verifications = {
        item.verification_id: item
        for item in result.analysis_result.verifications
    }
    evidence_items = {
        item.evidence_id: item
        for item in result.analysis_result.evidence
    }
    for task_result in result.task_results:
        if task_result.metric_id != "revenue_growth":
            continue
        calculation = calculations[task_result.calculation_id]
        # Formulas end with the comparison label, e.g. "（同比/累计）".
        variant_label = calculation.formula.rsplit("（", 1)[-1].rstrip("）")
        outputs[variant_label] = {
            "task_id": f"plan{plan_index}:{task_result.execution_task_id}",
            "task_result": task_result,
            "calculation": calculation,
            "verification": verifications[task_result.verification_id],
            "evidence": evidence_items[task_result.evidence_id],
        }
    return outputs


def _build_growth_variant_plans(service, plan_suffix):
    """Build single-period and cumulative multi-intent growth plans."""
    outputs = []
    for index, question in enumerate([
        "分析贵州茅台 2023Q2 收入同比和环比增长率",
        "分析贵州茅台 2023Q2 累计收入同比和环比增长率",
    ]):
        understanding = FinancialTaskUnderstandingBuilder(["贵州茅台"]).build(
            question
        )
        plan = ExecutionPlanBuilder(
            semantic_resolver=FinancialSemanticResolver(service),
        ).build(
            understanding,
            plan_id=f"plan_{plan_suffix}_{index}",
        )
        outputs.append((understanding, plan))
    return outputs


def test_growth_comparison_matrix_golden_values():
    """Cumulative and single-period yoy/qoq golden values, hand-computed.

    Cumulative revenue data: 2022Q1=250, 2022Q2=600, 2023Q1=300, 2023Q2=800.
    Derived single-quarter revenue: 2022Q1=250, 2022Q2=350,
    2023Q1=300, 2023Q2=500.
    """
    service = _growth_basis_service()
    workflow_obj = FinancialAnalysisWorkflow(service)
    # Hand-computed golden values (percent):
    #   yoy cumulative    = (800 - 600) / 600 = 33.333333%
    #   qoq cumulative    = (800 - 300) / 300 = 166.666667%
    #   yoy single-period = (500 - 350) / 350 = 42.857143%
    #   qoq single-period = (500 - 300) / 300 = 66.666667%
    expected = {
        "同比/累计": 33.333333,
        "环比/累计": 166.666667,
        "同比/单期": 42.857143,
        "环比/单期": 66.666667,
    }
    merged_outputs = {}
    for plan_index, (understanding, plan) in enumerate(
        _build_growth_variant_plans(service, "matrix_dag")
    ):
        result = FinancialRuntimeExecutor(workflow_obj).execute_plan(
            plan,
            understanding=understanding,
        )
        assert result.status == RuntimeExecutionStatus.SUCCEEDED
        merged_outputs.update(
            _growth_results_by_variant(result, plan_index=plan_index)
        )
    assert set(merged_outputs) == set(expected)
    for formula_label, expected_value in expected.items():
        calculation = merged_outputs[formula_label]["calculation"]
        verification = merged_outputs[formula_label]["verification"]
        assert calculation.result == pytest.approx(expected_value), formula_label
        assert verification.passed is True
        assert verification.actual_value == pytest.approx(expected_value)

    # Boundary: the legacy workflow mode refuses multi-intent plans explicitly
    # instead of silently picking one comparison semantics.
    multi_intent_understanding, multi_intent_plan = _build_growth_variant_plans(
        service, "matrix_workflow_rejected"
    )[0]
    workflow_plan = multi_intent_plan.model_copy(deep=True)
    workflow_plan.status = ExecutionPlanStatus.PENDING
    workflow_result = FinancialRuntimeExecutor(
        workflow_obj,
        execution_mode="workflow",
    ).execute_plan(workflow_plan, understanding=multi_intent_understanding)
    assert workflow_result.status == RuntimeExecutionStatus.FAILED
    assert all(
        "Multiple growth comparison intents require DAG execution" in (
            item.error or ""
        )
        for item in workflow_result.task_results
    )

    # Engine parity: a single-intent plan still computes identically in both
    # execution modes (cumulative yoy golden value).
    single_understanding = FinancialTaskUnderstandingBuilder(["贵州茅台"]).build(
        "分析贵州茅台 2023Q2 累计收入同比增长率"
    )
    single_plan = ExecutionPlanBuilder(
        semantic_resolver=FinancialSemanticResolver(service),
    ).build(
        single_understanding,
        plan_id="plan_matrix_single_intent",
    )
    dag_single = FinancialRuntimeExecutor(workflow_obj).execute_plan(
        single_plan,
        understanding=single_understanding,
    )
    workflow_single_plan = single_plan.model_copy(deep=True)
    workflow_single_plan.status = ExecutionPlanStatus.PENDING
    workflow_single = FinancialRuntimeExecutor(
        workflow_obj,
        execution_mode="workflow",
    ).execute_plan(workflow_single_plan, understanding=single_understanding)
    for mode_result in (dag_single, workflow_single):
        assert mode_result.status == RuntimeExecutionStatus.SUCCEEDED
        outputs = _growth_results_by_variant(mode_result)
        calculation = outputs["同比/累计"]["calculation"]
        assert calculation.result == pytest.approx(33.333333)


def test_cumulative_and_single_period_use_correct_comparison_bases():
    """The four growth variants must compare against different baselines."""
    service = _growth_basis_service()
    merged_outputs = {}
    merged_plan_tasks = {}
    for plan_index, (understanding, plan) in enumerate(
        _build_growth_variant_plans(service, "bases")
    ):
        result = FinancialRuntimeExecutor(
            FinancialAnalysisWorkflow(service),
        ).execute_plan(plan, understanding=understanding)
        assert result.status == RuntimeExecutionStatus.SUCCEEDED
        merged_outputs.update(
            _growth_results_by_variant(result, plan_index=plan_index)
        )
        for task in plan.tasks:
            if task.metric_id == "revenue_growth":
                merged_plan_tasks[
                    f"plan{plan_index}:{task.task_id}"
                ] = task

    # Baseline expectations from cumulative revenue data:
    # yoy cumulative    -> 2022Q2 cumulative (600)
    # qoq cumulative    -> 2023Q1 cumulative (300)
    # yoy single-period -> 2022Q2 single quarter (600 - 250 = 350)
    # qoq single-period -> 2023Q1 single quarter (300)
    expected_variants = {
        "同比/累计": {
            "previous_period": "2022Q2",
            "source_ids": {"income_2023Q2", "income_2022Q2"},
            "current": 800.0,
            "previous": 600.0,
        },
        "环比/累计": {
            "previous_period": "2023Q1",
            "source_ids": {"income_2023Q2", "income_2023Q1"},
            "current": 800.0,
            "previous": 300.0,
        },
        "同比/单期": {
            "previous_period": "2022Q2",
            "source_ids": {
                "income_2023Q2", "income_2023Q1", "income_2022Q2", "income_2022Q1",
            },
            "current": 500.0,
            "previous": 350.0,
        },
        "环比/单期": {
            "previous_period": "2023Q1",
            "source_ids": {"income_2023Q2", "income_2023Q1"},
            "current": 500.0,
            "previous": 300.0,
        },
    }
    task_ids = set()
    evidence_ids = set()
    calculation_ids = set()
    for formula_label, expected_values in expected_variants.items():
        item = merged_outputs[formula_label]
        task_result = item["task_result"]
        calculation = item["calculation"]
        evidence = item["evidence"]
        verification = item["verification"]

        task_ids.add(item["task_id"])
        evidence_ids.add(evidence.evidence_id)
        calculation_ids.add(calculation.calculation_id)

        plan_task = merged_plan_tasks[item["task_id"]]
        assert plan_task.inputs["previous_period"] == (
            expected_values["previous_period"]
        ), formula_label
        assert set(calculation.source_ids) == expected_values["source_ids"]
        assert calculation.inputs["current"] == pytest.approx(
            expected_values["current"]
        ), formula_label
        assert calculation.inputs["previous"] == pytest.approx(
            expected_values["previous"]
        ), formula_label
        assert verification.calculation_id == calculation.calculation_id
        assert evidence.calculation_id == calculation.calculation_id
        assert evidence.period == "2023Q2"
        assert evidence.formula == calculation.formula

    # Every growth intent is an independent task with independent evidence.
    assert len(task_ids) == 4
    assert len(evidence_ids) == 4
    assert len(calculation_ids) == 4


def test_missing_growth_base_period_matrix_is_unavailable():
    """Annual, quarterly, and half-year growth without baselines is unavailable."""
    missing_base_revenue = {
        "2023Q1": 300.0,
        "2023Q2": 800.0,
        "2023H1": 460.0,
        "2023": 1500.0,
    }
    service = FinancialDataService(
        companies=[_company()],
        income_statements=[
            _income(
                period,
                revenue=revenue,
                cost_of_revenue=revenue * 0.3,
                gross_profit=revenue * 0.7,
                operating_profit=revenue * 0.2,
                net_profit=revenue * 0.2,
                net_profit_attributable=revenue * 0.19,
            )
            for period, revenue in missing_base_revenue.items()
        ],
        balance_sheets=[_balance_for(period) for period in missing_base_revenue],
        cash_flows=[_cash(period) for period in missing_base_revenue],
    )
    workflow_obj = FinancialAnalysisWorkflow(service)
    executor = TaskLevelFinancialExecutor(workflow_obj, TaskOutputStore())
    cases = [
        ("2023", "year_over_year", "cumulative"),
        ("2023Q1", "year_over_year", "single_period"),
        ("2023Q1", "sequential", "cumulative"),
        ("2023H1", "year_over_year", "cumulative"),
    ]
    for task_number, (period, comparison_type, period_basis) in enumerate(
        cases, start=1
    ):
        definition = financial_metric_registry.get("revenue_growth")
        task = ExecutionTask(
            task_id=f"exec_v105_{task_number:04d}",
            tool_id="financial_metric_engine",
            tool_version="v10.4.0",
            company_id="600519",
            company_name="贵州茅台",
            period=period,
            metric_id="revenue_growth",
            metric_name=definition.name,
            inputs={
                "growth_comparison_type": comparison_type,
                "growth_period_basis": period_basis,
            },
        )
        outcome = executor(task)
        assert outcome.status == ExecutionStatus.UNAVAILABLE, (
            f"{period}/{comparison_type}/{period_basis}: {outcome.status_reason}"
        )
        assert outcome.status_reason == "growth_previous_period_missing"

    # Control: 2023Q2 sequential cumulative has a baseline (2023Q1).
    definition = financial_metric_registry.get("revenue_growth")
    control_task = ExecutionTask(
        task_id="exec_v105_0009",
        tool_id="financial_metric_engine",
        tool_version="v10.4.0",
        company_id="600519",
        company_name="贵州茅台",
        period="2023Q2",
        metric_id="revenue_growth",
        metric_name=definition.name,
        inputs={
            "growth_comparison_type": "sequential",
            "growth_period_basis": "cumulative",
        },
    )
    control_outcome = executor(control_task)
    assert control_outcome.status == ExecutionStatus.SUCCEEDED
    # (800 - 300) / 300 = 166.666667%
    assert control_outcome.output.calculation.result == pytest.approx(166.666667)

def test_negated_cumulative_keyword_resolves_to_single_period():
    """Negated cumulative wording must never be read as cumulative basis."""
    builder = FinancialTaskUnderstandingBuilder(["贵州茅台"])
    cases = [
        (
            "分析贵州茅台 2023Q2 单季度收入同比增长率，不要累计",
            {PeriodComparisonType.YEAR_OVER_YEAR},
        ),
        (
            "分析贵州茅台 2023Q2 收入环比增长率，不是累计口径，按单季度计算",
            {PeriodComparisonType.SEQUENTIAL},
        ),
        (
            "分析贵州茅台 2023Q2 非累计收入同比和环比增长率",
            {PeriodComparisonType.YEAR_OVER_YEAR, PeriodComparisonType.SEQUENTIAL},
        ),
    ]
    for question, expected_comparisons in cases:
        understanding = builder.build(question)
        assert understanding.growth_intent_source == "explicit", question
        assert {
            (item.comparison_type, item.period_basis)
            for item in understanding.growth_intents
        } == {
            (comparison, PeriodBasisType.SINGLE_PERIOD)
            for comparison in expected_comparisons
        }, question


def test_negated_single_period_keyword_resolves_to_cumulative():
    """Negated single-period wording resolves to the cumulative basis."""
    understanding = FinancialTaskUnderstandingBuilder(["贵州茅台"]).build(
        "分析贵州茅台 2023Q2 收入同比增长率，不要单季度口径，按累计计算"
    )
    assert understanding.growth_intent_source == "explicit"
    assert [
        (item.comparison_type, item.period_basis)
        for item in understanding.growth_intents
    ] == [(PeriodComparisonType.YEAR_OVER_YEAR, PeriodBasisType.CUMULATIVE)]


def test_negated_cumulative_intent_executes_single_period_golden_value():
    """A negated-cumulative question computes the single-quarter golden value."""
    understanding = FinancialTaskUnderstandingBuilder(["贵州茅台"]).build(
        "分析贵州茅台 2023Q2 单季度收入同比增长率，不要累计"
    )
    plan = ExecutionPlanBuilder(
        semantic_resolver=FinancialSemanticResolver(_growth_basis_service()),
    ).build(understanding, plan_id="plan_negated_cumulative")
    growth_tasks = [
        task for task in plan.tasks if task.metric_id == "revenue_growth"
    ]
    assert len(growth_tasks) == 1
    assert growth_tasks[0].inputs["growth_period_basis"] == "single_period"
    assert growth_tasks[0].inputs["previous_period"] == "2022Q2"

    result = FinancialRuntimeExecutor(
        FinancialAnalysisWorkflow(_growth_basis_service()),
    ).execute_plan(plan, understanding=understanding)
    assert result.status == RuntimeExecutionStatus.SUCCEEDED
    outputs = _growth_results_by_variant(result)
    # yoy single-period = (500 - 350) / 350 = 42.857143%
    calculation = outputs["同比/单期"]["calculation"]
    assert calculation.result == pytest.approx(42.857143)
    assert calculation.inputs["current"] == pytest.approx(500.0)
    assert calculation.inputs["previous"] == pytest.approx(350.0)


def test_contradictory_period_basis_wording_requires_clarification():
    """Contradictory basis wording must block the plan, not guess."""
    understanding = FinancialTaskUnderstandingBuilder(["贵州茅台"]).build(
        "分析贵州茅台 2023Q2 收入同比增长率，不要累计也不要单季"
    )
    assert understanding.planning_readiness == PlanningReadiness.NEEDS_CLARIFICATION
    assert understanding.growth_intent_source == "ambiguous"
    assert "GROWTH_PERIOD_BASIS_AMBIGUOUS" in understanding.growth_intent_diagnostics

    plan = ExecutionPlanBuilder(
        semantic_resolver=FinancialSemanticResolver(_growth_intent_service()),
    ).build(understanding, plan_id="plan_basis_ambiguous")
    assert plan.status == ExecutionPlanStatus.BLOCKED
    assert plan.tasks == []
    assert "GROWTH_PERIOD_BASIS_AMBIGUOUS" in plan.diagnostics

    result = FinancialRuntimeExecutor(
        FinancialAnalysisWorkflow(_growth_intent_service()),
    ).execute_plan(plan, understanding=understanding)
    _assert_structured_blocked_result(result, plan, "ambiguous")

def test_expanded_conflict_phrases_block_plan_execution():
    """Additional conflict phrasings must block instead of multi-intent."""
    builder = FinancialTaskUnderstandingBuilder(["贵州茅台"])
    conflict_questions = [
        "分析贵州茅台 2023Q2 收入增长率，不要同时计算同比和环比。",
        "分析贵州茅台 2023Q2 收入增长率，同比、环比只能保留一种。",
        "分析贵州茅台 2023Q2 收入增长率，同比和环比只保留一种。",
    ]
    for question in conflict_questions:
        understanding = builder.build(question)
        assert understanding.growth_intent_source == "conflict", question
        assert "GROWTH_COMPARISON_CONFLICT" in (
            understanding.growth_intent_diagnostics
        ), question
        assert understanding.growth_intents == [], question
        assert understanding.planning_readiness == (
            PlanningReadiness.NEEDS_CLARIFICATION
        ), question

        plan = ExecutionPlanBuilder(
            semantic_resolver=FinancialSemanticResolver(_growth_intent_service()),
        ).build(understanding, plan_id=f"plan_conflict_{abs(hash(question)) % 10000}")
        assert plan.status == ExecutionPlanStatus.BLOCKED, question
        assert plan.tasks == [], question

        result = FinancialRuntimeExecutor(
            FinancialAnalysisWorkflow(_growth_intent_service()),
        ).execute_plan(plan, understanding=understanding)
        _assert_structured_blocked_result(result, plan, "conflict")


def test_unresolved_comparison_wording_requires_clarification():
    """"还没确定"-style wording enters the clarification flow, not execution."""
    understanding = FinancialTaskUnderstandingBuilder(["贵州茅台"]).build(
        "我还没确定是同比还是环比，先分析贵州茅台 2023Q2 收入。"
    )
    assert understanding.growth_intent_source == "ambiguous"
    assert "GROWTH_COMPARISON_AMBIGUOUS" in understanding.growth_intent_diagnostics
    assert understanding.growth_intents == []

    plan = ExecutionPlanBuilder(
        semantic_resolver=FinancialSemanticResolver(_growth_intent_service()),
    ).build(understanding, plan_id="plan_unresolved_comparison")
    assert plan.status == ExecutionPlanStatus.BLOCKED
    assert plan.tasks == []

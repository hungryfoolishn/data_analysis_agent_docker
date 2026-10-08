"""Runtime V10.1 semantic resolver tests."""

from __future__ import annotations

from copy import deepcopy

import pytest

from langgraph_langchain.runtime.financial.models import (
    BalanceSheetStatement,
    CashFlowStatement,
    Company,
    IncomeStatement,
)
from langgraph_langchain.runtime.financial import FinancialDataService
from langgraph_langchain.runtime.financial_agent import (
    FINANCIAL_TOOL_REGISTRY,
    FinancialPlanTaskType,
    FinancialSemanticResolver,
    FinancialTaskPlanner,
    FinancialTaskUnderstandingBuilder,
    PlanStatus,
    PlanningReadiness,
)

PERIODS = ["2021", "2022", "2023", "2024", "2025"]


def _company(
    company_id: str = "600519",
    stock_code: str = "600519",
    company_name: str = "贵州茅台",
) -> Company:
    return Company(
        company_id=company_id,
        stock_code=stock_code,
        company_name=company_name,
        exchange="SSE",
        industry_id="baijiu",
    )


def _income(company_id: str, period: str, **overrides) -> IncomeStatement:
    values = {
        "company_id": company_id,
        "period": period,
        "revenue": 100.0 + int(period),
        "cost_of_revenue": 30.0,
        "gross_profit": 70.0,
        "operating_profit": 40.0,
        "net_profit": 25.0,
        "net_profit_attributable": 24.0,
        "source_id": f"income_{company_id}_{period}",
    }
    values.update(overrides)
    return IncomeStatement(**values)


def _balance(company_id: str, period: str, **overrides) -> BalanceSheetStatement:
    values = {
        "company_id": company_id,
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
        "source_id": f"balance_{company_id}_{period}",
    }
    values.update(overrides)
    return BalanceSheetStatement(**values)


def _cash(company_id: str, period: str, **overrides) -> CashFlowStatement:
    values = {
        "company_id": company_id,
        "period": period,
        "operating_cash_flow": 35.0,
        "investing_cash_flow": -10.0,
        "financing_cash_flow": -5.0,
        "capital_expenditure": 12.0,
        "free_cash_flow": 23.0,
        "source_id": f"cash_{company_id}_{period}",
    }
    values.update(overrides)
    return CashFlowStatement(**values)


def _service(
    *,
    revenue: float | None = 100.0,
    gross_profit: float | None = 70.0,
    periods: list[str] | None = None,
) -> FinancialDataService:
    company = _company()
    selected = periods or PERIODS
    income = [
        _income(company.company_id, period, revenue=revenue, gross_profit=gross_profit)
        for period in selected
    ]
    balance = [_balance(company.company_id, period) for period in selected]
    cash = [_cash(company.company_id, period) for period in selected]
    return FinancialDataService(
        companies=[company],
        income_statements=income,
        balance_sheets=balance,
        cash_flows=cash,
    )


@pytest.fixture()
def resolver():
    return FinancialSemanticResolver(_service())


def _understanding(question: str):
    builder = FinancialTaskUnderstandingBuilder(["贵州茅台"])
    return builder.build(question)


def test_company_registry_supports_exact_name_alias_and_stock_code():
    registry = FinancialSemanticResolver(_service()).company_registry

    exact = registry.resolve("分析贵州茅台")
    assert exact.matches[0].match_type.value == "EXACT_NAME"
    assert exact.resolved[0].company_id == "600519"

    alias = registry.resolve("Analyze Moutai")
    assert alias.matches[0].match_type.value == "ALIAS"
    assert alias.resolved[0].company_name == "贵州茅台"

    stock = registry.resolve("分析 600519")
    assert stock.matches[0].match_type.value == "STOCK_CODE"
    assert stock.resolved[0].company_id == "600519"


def test_company_registry_uses_stock_code_boundary():
    registry = FinancialSemanticResolver(_service()).company_registry

    matched = registry.resolve("报告代码 600519")
    assert matched.resolved[0].company_id == "600519"

    unmatched = registry.resolve("报告代码 1600519")
    assert unmatched.resolved == []


def test_semantic_resolver_reports_unresolved_company():
    understanding = _understanding("分析贵州茅台 2021-2025 年收入")
    understanding.companies[0].company_id = "unknown_company"
    resolution = FinancialSemanticResolver(_service()).resolve(understanding)

    assert resolution.companies.resolved[0].company_id == "600519"
    assert resolution.companies.unresolved_terms == ["unknown_company"]
    assert "SEMANTIC_UNRESOLVED_COMPANY:unknown_company" in resolution.companies.diagnostics
    assert "SEMANTIC_COMPANY_NOT_RESOLVED" in resolution.blockers
    assert not resolution.ready_for_planning


def test_period_resolver_selects_available_years_and_reports_missing():
    service = _service(periods=["2021", "2022", "2024", "2025"])
    understanding = _understanding("分析贵州茅台 2021-2025 年收入")
    resolution = FinancialSemanticResolver(service).resolve(understanding)

    coverage = resolution.periods[0]
    assert coverage.available_periods == ["2021", "2022", "2024", "2025"]
    assert coverage.selected_periods == ["2021", "2022", "2024", "2025"]
    assert coverage.missing_periods == ["2023"]
    assert coverage.status == "PARTIAL"
    assert coverage.ready_for_execution is True
    assert "SEMANTIC_PERIOD_NOT_AVAILABLE" not in resolution.blockers


def test_metric_resolver_maps_registered_and_unknown_metrics():
    understanding = _understanding("分析贵州茅台 2021-2025 年收入和现金流")
    # Ensure an unknown metric is represented deterministically.
    understanding.required_metrics.append("unknown_metric")
    resolution = FinancialSemanticResolver(_service()).resolve(understanding)

    resolved_ids = {item.metric_id for item in resolution.metrics.resolved}
    assert {"revenue", "revenue_growth", "operating_cash_flow"} <= resolved_ids
    assert "unknown_metric" in resolution.metrics.unresolved_metrics
    assert "SEMANTIC_UNKNOWN_METRIC:unknown_metric" in resolution.metrics.diagnostics
    assert "SEMANTIC_METRIC_NOT_RESOLVED" in resolution.blockers


def test_data_requirements_include_current_and_previous_sources():
    understanding = _understanding("分析贵州茅台 2021-2025 年收入增长和ROE")
    resolution = FinancialSemanticResolver(_service()).resolve(understanding)
    requirements = {
        item.metric_id: item
        for item in resolution.metrics.resolved
    }

    growth = next(
        item for item in resolution.metrics.resolved
        if item.metric_id == "revenue_growth"
    )
    roe = next(
        item for item in resolution.metrics.resolved
        if item.metric_id == "roe"
    )

    assert "income_statement.revenue" in growth.source_fields
    assert "balance_sheet.total_equity" in roe.source_fields
    assert requirements


def test_coverage_treats_missing_fields_as_warnings_not_planning_blockers():
    understanding = _understanding(
        "分析贵州茅台 2021-2025 年盈利能力和经营现金流"
    )
    resolution = FinancialSemanticResolver(_service(gross_profit=None)).resolve(
        understanding
    )

    coverage = resolution.data_coverage.companies[0]
    assert coverage.blockers == []
    assert coverage.warnings
    assert coverage.status == "COMPLETE_WITH_WARNINGS"
    assert any(item.status == "MISSING_CURRENT_DATA" for item in coverage.metric_coverage)
    assert any(item.status == "AVAILABLE" for item in coverage.metric_coverage)
    assert "SEMANTIC_DATA_NOT_AVAILABLE" not in resolution.blockers
    assert resolution.ready_for_planning is True


def test_coverage_reports_missing_previous_period_as_warning():
    understanding = _understanding("分析贵州茅台 2021-2025 年收入增长")
    resolution = FinancialSemanticResolver(_service()).resolve(understanding)

    coverage = resolution.data_coverage.companies[0]
    first_revenue = next(
        item for item in coverage.metric_coverage
        if item.period == "2021" and item.metric_id == "revenue_growth"
    )
    assert first_revenue.status == "MISSING_PREVIOUS_DATA"
    assert first_revenue.previous_period is None
    assert any(
        gap.period == "2021" and gap.scope == "PREVIOUS"
        for gap in coverage.warnings
    )
    assert resolution.ready_for_planning is True


def test_no_usable_metric_data_blocks_semantic_planning():
    understanding = _understanding("分析贵州茅台 2021-2025 年收入")
    resolution = FinancialSemanticResolver(_service(revenue=None)).resolve(
        understanding
    )

    coverage = resolution.data_coverage.companies[0]
    assert all(
        item.status == "MISSING_CURRENT_DATA"
        for item in coverage.metric_coverage
    )
    assert "SEMANTIC_DATA_NOT_AVAILABLE" in resolution.blockers
    assert resolution.ready_for_planning is False
    assert resolution.clarifications


def test_planner_integrates_semantic_resolution():
    service = _service()
    understanding = _understanding(
        "分析贵州茅台 2021-2025 年收入、盈利能力和现金流"
    )
    plan = FinancialTaskPlanner(
        semantic_resolver=FinancialSemanticResolver(service)
    ).build(understanding)

    assert plan.status == PlanStatus.VALIDATED
    assert plan.planning_readiness == PlanningReadiness.READY
    assert plan.validation_errors == []
    assert plan.metadata["semantic_resolution_version"] == "v10.1.0"
    assert plan.metadata["semantic_resolution"]["ready_for_planning"] is True
    assert plan.metadata["semantic_resolution"]["companies"]["resolved"][0][
        "company_id"
    ] == "600519"


def test_planner_blocks_on_semantic_resolution_without_changing_input_readiness():
    service = _service(revenue=None)
    understanding = _understanding("分析贵州茅台 2021-2025 年收入")
    plan = FinancialTaskPlanner(
        semantic_resolver=FinancialSemanticResolver(service)
    ).build(understanding)

    assert plan.status == PlanStatus.BLOCKED
    assert plan.planning_readiness == PlanningReadiness.READY
    assert "SEMANTIC_DATA_NOT_AVAILABLE" in plan.planning_diagnostics
    assert plan.metadata["blocked_by"] == "semantic_resolution"
    assert not plan.tasks


def test_plan_status_is_independent_from_planning_readiness():
    understanding = _understanding("分析贵州茅台 2021-2025 年收入")
    tool_registry = deepcopy(FINANCIAL_TOOL_REGISTRY)
    tool_registry.pop("financial_metric_engine")

    plan = FinancialTaskPlanner(
        tool_registry=tool_registry,
        semantic_resolver=FinancialSemanticResolver(_service()),
    ).build(understanding)

    assert plan.status == PlanStatus.INVALID
    assert plan.planning_readiness == PlanningReadiness.READY
    assert any("no registered tool" in error for error in plan.validation_errors)

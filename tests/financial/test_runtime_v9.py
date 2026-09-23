"""Runtime V9 financial analysis tests."""

from __future__ import annotations

import pytest

from langgraph_langchain.runtime.financial import (
    FinancialAnalysisWorkflow,
    FinancialDataService,
    FinancialMetricRegistry,
    FinancialQuery,
    FinancialTaskClassifier,
    FinancialTaskType,
    IncomeStatement,
    BalanceSheetStatement,
    CashFlowStatement,
    Company,
    financial_metric_registry,
)
from langgraph_langchain.runtime.financial.risk_detector import FinancialRiskDetector


@pytest.fixture
def data_service() -> FinancialDataService:
    company = Company(
        company_id="company_maotai",
        stock_code="600519",
        company_name="贵州茅台",
        exchange="SSE",
        industry_id="baijiu",
        listing_date="2001-08-27",
    )
    peer = Company(
        company_id="company_wuliangye",
        stock_code="000858",
        company_name="五粮液",
        exchange="SZSE",
        industry_id="baijiu",
        currency="CNY",
    )
    companies = [company, peer]
    income = []
    balance = []
    cash = []
    base_revenue = {"2024": 1_000_000.0, "2025": 1_100_000.0}
    for item in companies:
        for period, revenue in base_revenue.items():
            gross_profit = revenue * 0.9
            operating_profit = gross_profit * 0.7
            net_profit = operating_profit * 0.8
            income.append(IncomeStatement(
                company_id=item.company_id,
                period=period,
                revenue=revenue,
                cost_of_revenue=revenue - gross_profit,
                gross_profit=gross_profit,
                operating_profit=operating_profit,
                net_profit=net_profit,
                net_profit_attributable=net_profit * 0.98,
                eps=net_profit / 100_000,
            ))
            balance.append(BalanceSheetStatement(
                company_id=item.company_id,
                period=period,
                total_assets=net_profit * 10,
                total_liabilities=net_profit * 4,
                total_equity=net_profit * 6,
                cash=net_profit * 2,
                accounts_receivable=revenue * 0.05,
                inventory=revenue * 0.08,
                fixed_assets=net_profit * 3,
                short_term_debt=net_profit * 1,
                long_term_debt=net_profit * 1,
                current_assets=net_profit * 3,
                current_liabilities=net_profit * 1.5,
            ))
            cash.append(CashFlowStatement(
                company_id=item.company_id,
                period=period,
                operating_cash_flow=net_profit * 1.2,
                investing_cash_flow=-net_profit * 0.4,
                financing_cash_flow=-net_profit * 0.3,
                capital_expenditure=net_profit * 0.5,
                free_cash_flow=net_profit * 0.7,
            ))
    return FinancialDataService(
        companies=companies,
        income_statements=income,
        balance_sheets=balance,
        cash_flows=cash,
    )


def test_metric_registry_has_at_least_twenty_definitions():
    assert len(financial_metric_registry.list_metrics()) >= 20
    net_margin = financial_metric_registry.get("net_margin")
    assert net_margin.numerator == "net_profit"
    assert net_margin.denominator == "revenue"
    assert net_margin.source_fields


def test_task_classifier_routes_financial_questions(data_service):
    classifier = FinancialTaskClassifier()
    known = [item.company_name for item in data_service.list_companies()]

    assert classifier.classify("分析贵州茅台2021年至2025年的盈利能力") == FinancialTaskType.PROFITABILITY_ANALYSIS
    assert classifier.classify("比较茅台和五粮液的收入增长") == FinancialTaskType.PEER_COMPARISON
    assert classifier.classify("分析经营现金流质量") == FinancialTaskType.CASHFLOW_ANALYSIS

    query = classifier.build_query(
        "比较贵州茅台和五粮液2024年至2025年的收入增长",
        known,
    )
    assert query.task_type == FinancialTaskType.PEER_COMPARISON
    assert set(query.company_names) == {"贵州茅台", "五粮液"}
    assert (query.start_year, query.end_year) == (2024, 2025)


def test_financial_workflow_computes_metrics_and_calculations(data_service):
    workflow = FinancialAnalysisWorkflow(data_service)
    result = workflow.run("分析贵州茅台2024年至2025年的盈利能力")

    assert result.task_type == FinancialTaskType.PROFITABILITY_ANALYSIS
    assert result.observations
    assert result.calculations
    assert {"gross_margin", "operating_margin", "net_margin", "roe"} <= set(result.metrics)

    revenue_observation = next(
        item for item in result.observations
        if item.company_name == "贵州茅台" and item.period == "2025" and item.metric_id == "revenue"
    )
    assert revenue_observation.value == 1_100_000

    growth_observation = next(
        item for item in result.observations
        if item.company_name == "贵州茅台" and item.period == "2025" and item.metric_id == "revenue_growth"
    )
    assert growth_observation.value == pytest.approx(10.0)

    net_margin_observation = next(
        item for item in result.observations
        if item.company_name == "贵州茅台" and item.period == "2025" and item.metric_id == "net_margin"
    )
    assert net_margin_observation.value == pytest.approx(50.4)

    assert result.report_markdown
    assert "## 核心指标" in result.report_markdown
    assert "## 计算过程" in result.report_markdown
    assert "不构成投资建议" in result.report_markdown


def test_financial_risk_detector_flags_rule_based_signals():
    detector = FinancialRiskDetector()
    risks = detector.detect([
        {"company_name": "贵州茅台", "period": "2025", "metric_id": "revenue_growth", "value": -8},
        {"company_name": "贵州茅台", "period": "2025", "metric_id": "debt_to_asset", "value": 82},
        {"company_name": "贵州茅台", "period": "2024", "metric_id": "net_profit_growth", "value": 12},
    ])
    assert len(risks) == 2
    assert {item.metric_id for item in risks} == {"revenue_growth", "debt_to_asset"}
    assert risks[0].severity in {"medium", "high"}

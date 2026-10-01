"""Runtime V10.0 task understanding tests."""

from __future__ import annotations

import pytest

from langgraph_langchain.runtime.financial.models import Company
from langgraph_langchain.runtime.financial_agent import (
    FinancialTaskUnderstandingBuilder,
)


KNOWN_COMPANIES = ["贵州茅台", "五粮液", "泸州老窖"]

CASES = [
    ("分析贵州茅台 2021-2025 年营业收入变化", "REVENUE_ANALYSIS", {"revenue_trend"}),
    ("分析五粮液 2021-2025 年净利润变化", "PROFIT_ANALYSIS", {"profit_trend"}),
    ("分析泸州老窖 2021-2025 年盈利能力", "PROFITABILITY_ANALYSIS", {"profitability"}),
    ("分析贵州茅台 2021-2025 年经营现金流", "CASHFLOW_ANALYSIS", {"cashflow"}),
    ("分析五粮液 2021-2025 年偿债能力", "SOLVENCY_ANALYSIS", {"solvency"}),
    ("分析泸州老窖 2021-2025 年营运效率", "OPERATING_ANALYSIS", {"operating_efficiency"}),
    ("分析贵州茅台 2021-2025 年财务风险", "RISK_ANALYSIS", {"risk"}),
    ("比较贵州茅台和五粮液 2021-2025 年收入", "COMPREHENSIVE_ANALYSIS", {"revenue_trend", "peer_comparison"}),
    ("分析贵州茅台 2021-2025 年综合基本面", "COMPREHENSIVE_ANALYSIS", {"revenue_trend", "profit_trend", "profitability", "cashflow"}),
    ("Analyze Guizhou Moutai revenue from 2021 to 2025", "REVENUE_ANALYSIS", {"revenue_trend"}),
    ("分析五粮液 2021至2025年ROE和ROA", "PROFITABILITY_ANALYSIS", {"profitability"}),
    ("分析泸州老窖近五年自由现金流", "CASHFLOW_ANALYSIS", {"cashflow"}),
    ("对比贵州茅台和五粮液 2021-2025 年净利润", "COMPREHENSIVE_ANALYSIS", {"profit_trend", "peer_comparison"}),
    ("分析贵州茅台 2021-2025 年经营情况", "COMPREHENSIVE_ANALYSIS", {"revenue_trend", "profit_trend", "profitability", "cashflow"}),
    ("分析五粮液 2021-2025 年 cash flow quality", "CASHFLOW_ANALYSIS", {"cashflow"}),
    ("分析泸州老窖 2021-2025 年资产负债率", "SOLVENCY_ANALYSIS", {"solvency"}),
    ("Analyze 贵州茅台 2021-2025 risk signals", "RISK_ANALYSIS", {"risk"}),
    ("分析五粮液 2021-2025 年存货周转率", "OPERATING_ANALYSIS", {"operating_efficiency"}),
    ("Analyze 泸州老窖 2021-2025 revenue and net profit", "COMPREHENSIVE_ANALYSIS", {"revenue_trend", "profit_trend"}),
    ("分析贵州茅台 2021-2025 年自由现金流和现金流质量", "CASHFLOW_ANALYSIS", {"cashflow"}),
]


@pytest.mark.parametrize(
    ("question", "task_type", "objectives"),
    CASES,
    ids=[f"question_{index:02d}" for index in range(1, len(CASES) + 1)],
)
def test_task_understanding_generates_valid_tasks(
    question: str,
    task_type: str,
    objectives: set[str],
):
    understanding = FinancialTaskUnderstandingBuilder(KNOWN_COMPANIES).build(question)

    assert understanding.task_type == task_type
    assert objectives <= set(understanding.objectives)
    assert understanding.companies
    assert understanding.period_range is not None
    assert understanding.required_metrics
    assert understanding.missing_information == []
    assert understanding.query_id.startswith("query_")
    assert 0 <= understanding.confidence <= 1


def test_comprehensive_peer_example_has_expected_understanding():
    builder = FinancialTaskUnderstandingBuilder(KNOWN_COMPANIES, reference_year=2025)
    understanding = builder.build(
        "分析贵州茅台 2021—2025 年的收入、利润、盈利能力和现金流变化，并与五粮液比较。"
    )

    assert understanding.task_type == "COMPREHENSIVE_ANALYSIS"
    assert [item.company_name for item in understanding.companies] == [
        "贵州茅台", "五粮液"
    ]
    assert understanding.period_range.start_year == 2021
    assert understanding.period_range.end_year == 2025
    assert understanding.comparison_enabled is True
    assert {
        "revenue_trend", "profit_trend", "profitability", "cashflow",
        "peer_comparison",
    } <= set(understanding.objectives)
    assert {
        "revenue", "revenue_growth", "net_profit", "net_profit_growth",
        "net_margin", "roe", "operating_cash_flow", "free_cash_flow",
    } <= set(understanding.required_metrics)


def test_relative_period_uses_reference_year():
    builder = FinancialTaskUnderstandingBuilder(KNOWN_COMPANIES, reference_year=2025)
    understanding = builder.build("分析贵州茅台过去五年的经营情况")

    assert understanding.period_range.start_year == 2021
    assert understanding.period_range.end_year == 2025
    assert "relative_period_resolved_with_reference_year" in understanding.ambiguities


def test_company_object_and_stock_code_are_supported():
    company = Company(
        company_id="600519",
        stock_code="600519",
        company_name="贵州茅台",
        exchange="SSE",
        industry_id="baijiu",
    )
    builder = FinancialTaskUnderstandingBuilder([company])
    understanding = builder.build("分析 600519 2021-2025 年收入")

    assert [item.company_name for item in understanding.companies] == ["贵州茅台"]
    assert understanding.companies[0].company_id == "600519"
    assert understanding.companies[0].stock_code == "600519"


def test_missing_company_and_period_are_recorded():
    builder = FinancialTaskUnderstandingBuilder(KNOWN_COMPANIES)
    understanding = builder.build("分析盈利能力")

    assert understanding.companies == []
    assert understanding.period_range is None
    assert set(understanding.missing_information) == {
        "company_names_unresolved", "period_range_unresolved"
    }


def test_query_id_is_stable_for_same_question():
    builder = FinancialTaskUnderstandingBuilder(KNOWN_COMPANIES)
    first = builder.build("分析贵州茅台 2021-2025 年收入")
    second = builder.build("分析贵州茅台 2021-2025 年收入")

    assert first.query_id == second.query_id

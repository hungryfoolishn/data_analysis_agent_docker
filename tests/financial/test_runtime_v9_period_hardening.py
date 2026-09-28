"""Regression tests for V9.3 end-to-end period semantics."""

from __future__ import annotations

from datetime import date

from langgraph_langchain.runtime.financial import (
    FinancialAnalysisWorkflow,
    FinancialDataService,
    FinancialPeriod,
    FinancialQuery,
    FormulaGoldenCase,
    FormulaGoldenRunner,
    PeriodNormalizer,
    PeriodType,
)
from langgraph_langchain.runtime.financial.models import (
    BalanceSheetStatement,
    CashFlowStatement,
    Company,
    IncomeStatement,
)


PERIODS = [
    "2024年度",
    "2025Q1",
    "2025-Q2",
    "2025H1",
    "2025Q3",
    "2025-Q4",
    "FY2025",
]


def _company() -> Company:
    return Company(
        company_id="period_test",
        stock_code="000001",
        company_name="期间测试",
        exchange="SZSE",
        industry_id="test",
    )


def _statement_values(period: str, revenue: float, equity: float) -> tuple[
    IncomeStatement,
    BalanceSheetStatement,
    CashFlowStatement,
]:
    return (
        IncomeStatement(
            company_id="period_test",
            period=period,
            revenue=revenue,
            cost_of_revenue=revenue * 0.4,
            gross_profit=revenue * 0.6,
            operating_profit=revenue * 0.25,
            net_profit=revenue * 0.18,
            net_profit_attributable=revenue * 0.17,
            source_id=f"income_{period}",
        ),
        BalanceSheetStatement(
            company_id="period_test",
            period=period,
            total_assets=equity * 1.5,
            total_liabilities=equity * 0.5,
            total_equity=equity,
            cash=equity * 0.35,
            accounts_receivable=revenue * 0.04,
            inventory=revenue * 0.06,
            fixed_assets=equity * 0.3,
            short_term_debt=equity * 0.12,
            long_term_debt=equity * 0.18,
            current_assets=equity * 0.8,
            current_liabilities=equity * 0.3,
            source_id=f"balance_{period}",
        ),
        CashFlowStatement(
            company_id="period_test",
            period=period,
            operating_cash_flow=revenue * 0.21,
            investing_cash_flow=-revenue * 0.08,
            financing_cash_flow=-revenue * 0.03,
            capital_expenditure=revenue * 0.05,
            free_cash_flow=revenue * 0.16,
            source_id=f"cash_{period}",
        ),
    )


def _build_service() -> FinancialDataService:
    company = _company()
    revenues = {
        "2024年度": 100.0,
        "2025Q1": 20.0,
        "2025-Q2": 22.0,
        "2025H1": 42.0,
        "2025Q3": 24.0,
        "2025-Q4": 26.0,
        "FY2025": 92.0,
    }
    income: list[IncomeStatement] = []
    balance: list[BalanceSheetStatement] = []
    cash: list[CashFlowStatement] = []
    for period in PERIODS:
        revenue = revenues[period]
        income_statement, balance_statement, cash_flow_statement = _statement_values(
            period,
            revenue,
            revenue * 4,
        )
        income.append(income_statement)
        balance.append(balance_statement)
        cash.append(cash_flow_statement)

    return FinancialDataService(
        companies=[company],
        income_statements=income,
        balance_sheets=balance,
        cash_flows=cash,
    )


def test_period_previous_uses_correct_quarter_dates():
    normalizer = PeriodNormalizer()
    q1_previous = normalizer.parse("2025Q1").previous()
    q4_previous = normalizer.parse("2025Q4").previous()

    assert q1_previous.normalized_period == "2024Q4"
    assert (q1_previous.start_date, q1_previous.end_date) == (
        date(2024, 10, 1),
        date(2024, 12, 31),
    )
    assert q4_previous.normalized_period == "2025Q3"
    assert (q4_previous.start_date, q4_previous.end_date) == (
        date(2025, 7, 1),
        date(2025, 9, 30),
    )
    assert q1_previous.order_key < q4_previous.order_key


def test_period_ordering_is_semantic_not_lexical():
    normalizer = PeriodNormalizer()
    periods = [
        "FY2025",
        "2025-Q4",
        "2025H1",
        "2025Q3",
        "2025-Q2",
        "2025Q1",
    ]

    ordered = sorted(periods, key=normalizer.order_key)

    assert ordered == [
        "2025Q1",
        "2025-Q2",
        "2025H1",
        "2025Q3",
        "2025-Q4",
        "FY2025",
    ]


def test_data_service_canonicalizes_raw_annual_periods():
    service = FinancialDataService(
        companies=[_company()],
        income_statements=[
            IncomeStatement(
                company_id="period_test",
                period="2025年度",
                revenue=120.0,
                net_profit=20.0,
                source_id="income_raw",
            )
        ],
        balance_sheets=[
            BalanceSheetStatement(
                company_id="period_test",
                period="2025年度",
                total_assets=300.0,
                total_liabilities=100.0,
                total_equity=200.0,
                source_id="balance_raw",
            )
        ],
        cash_flows=[
            CashFlowStatement(
                company_id="period_test",
                period="2025年度",
                operating_cash_flow=30.0,
                capital_expenditure=10.0,
                source_id="cash_raw",
            )
        ],
    )

    assert service.periods_for("period_test") == ["2025"]
    assert service.previous_period("period_test", "FY2025") is None

    income, balance, cash_flow, _ = service.statements("period_test", "FY2025")

    assert income.period == "2025年度"
    assert income.source_id == "income_raw"
    assert balance.source_id == "balance_raw"
    assert cash_flow.source_id == "cash_raw"

    source = service.get_source("income_raw")
    assert source.report_period == "2025"


def test_data_service_supports_mixed_quarter_half_year_and_fy_periods():
    service = _build_service()

    assert service.periods_for("period_test") == [
        "2024",
        "2025Q1",
        "2025Q2",
        "2025H1",
        "2025Q3",
        "2025Q4",
        "2025",
    ]
    assert service.previous_period("period_test", "2025Q2") == "2025Q1"
    assert service.previous_period("period_test", "2025Q3") == "2025Q2"
    assert service.previous_period("period_test", "2025Q4") == "2025Q3"
    assert service.previous_period("period_test", "2025H1") is None
    assert service.previous_period("period_test", "2025") == "2024"

    income, _, _, previous_income = service.statements(
        "period_test",
        "2025年度",
        "2024年度",
    )
    assert income.revenue == 92.0
    assert previous_income.revenue == 100.0


def test_formula_golden_runner_accepts_raw_period_statement():
    service = FinancialDataService(
        companies=[_company()],
        income_statements=[
            IncomeStatement(
                company_id="period_test",
                period="2025年度",
                revenue=120.0,
                net_profit=20.0,
                source_id="income_raw",
            )
        ],
        balance_sheets=[
            BalanceSheetStatement(
                company_id="period_test",
                period="2025年度",
                total_assets=300.0,
                total_liabilities=100.0,
                total_equity=200.0,
                source_id="balance_raw",
            )
        ],
        cash_flows=[
            CashFlowStatement(
                company_id="period_test",
                period="2025年度",
                operating_cash_flow=30.0,
                capital_expenditure=10.0,
                source_id="cash_raw",
            )
        ],
    )
    golden_case = FormulaGoldenCase(
        case_id="raw_period_revenue",
        metric_id="revenue",
        company_id="period_test",
        period="FY2025",
        expected=120.0,
        unit="CNY",
        formula="reported:income_statement.revenue",
        formula_version="revenue-reported-v1",
        inputs={"value": 120.0},
    )

    summary = FormulaGoldenRunner(service).run([golden_case])

    assert summary.passed_cases == 1
    assert summary.failures == []


def test_workflow_filters_mixed_periods_by_query_year_and_orders_summary():
    workflow = FinancialAnalysisWorkflow(_build_service())
    result = workflow.run(FinancialQuery(
        question="期间测试 2025 年收入分析",
        task_type="REVENUE_ANALYSIS",
        company_names=["期间测试"],
        metrics=["revenue"],
        start_year=2025,
        end_year=2025,
    ))

    selected_periods = {item.period for item in result.observations}
    assert selected_periods == {
        "2025Q1",
        "2025Q2",
        "2025H1",
        "2025Q3",
        "2025Q4",
        "2025",
    }
    assert "2024" not in selected_periods
    assert "期间测试 2025 " in result.summary
    latest_summary_period = max(
        selected_periods,
        key=PeriodNormalizer().order_key,
    )
    assert result.summary.startswith(f"期间测试 {latest_summary_period} ")


def test_financial_period_normalized_types():
    period = FinancialPeriod(
        raw_period="2025-Q4",
        year=2025,
        period_type=PeriodType.Q4,
        start_date=date(2025, 10, 1),
        end_date=date(2025, 12, 31),
    )

    assert period.normalized_period == "2025Q4"
    assert not period.is_fiscal_year
    assert period.order_key == (2025, date(2025, 12, 31).toordinal(), 4.0)

"""Runtime V9.2 financial semantics and independent verification tests."""

from __future__ import annotations

from pathlib import Path

from langgraph_langchain.runtime.financial import (
    CALCULATED,
    INVALID,
    UNAVAILABLE,
    FinancialAnalysisWorkflow,
    FinancialAnomalyEngine,
    FinancialCalculation,
    FinancialDataService,
    FinancialObservation,
    FinancialQuery,
    FinancialTaskType,
    FinancialVerificationEngine,
    compute_metric,
)
from langgraph_langchain.runtime.financial.evaluation import FinancialEvaluationAdapter

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "tests" / "financial" / "data" / "financial"


def build_service() -> FinancialDataService:
    return FinancialDataService.from_csv_directory(DATA_DIR)


def build_workflow() -> FinancialAnalysisWorkflow:
    return FinancialAnalysisWorkflow(build_service())


def test_compute_metric_distinguishes_missing_previous_balance_from_zero():
    service = build_service()
    company = service.resolve_company("白酒样本01")
    income, balance, cash_flow, _ = service.statements(company.company_id, "2021")

    computation = compute_metric(
        "roe",
        income=income,
        balance=balance,
        cash_flow=cash_flow,
        previous_balance=None,
    )

    assert computation.status == UNAVAILABLE
    assert computation.value is None
    assert "previous_balance" in computation.missing_fields
    assert computation.reason == "average_balance_previous_period_missing"


def test_compute_metric_marks_zero_denominator_invalid():
    service = build_service()
    company = service.resolve_company("白酒样本01")
    income, balance, cash_flow, _ = service.statements(company.company_id, "2021")
    zero_balance = balance.model_copy(update={"total_equity": 0.0})

    computation = compute_metric(
        "roe",
        income=income,
        balance=zero_balance,
        cash_flow=cash_flow,
        previous_balance=zero_balance,
    )

    assert computation.status == INVALID
    assert computation.value is None
    assert computation.reason == "average_denominator_zero"


def test_workflow_does_not_create_evidence_for_unavailable_metric():
    result = build_workflow().run("白酒样本01 2021 年盈利能力分析")

    unavailable = [
        item for item in result.calculations
        if item.metric_id == "roe" and item.status == UNAVAILABLE
    ]
    assert unavailable
    assert all(item.result is None for item in unavailable)
    assert all(item.status_reason == "average_balance_previous_period_missing" for item in unavailable)
    assert all(item.metric_id != "roe" for item in result.observations)
    assert all(item.metric_id != "roe" for item in result.evidence)
    assert all(item.metric_id != "roe" for item in result.findings)
    assert "## 数据可用性" in result.report_markdown
    assert "average_balance_previous_period_missing" in result.report_markdown


def test_independent_verification_engine_rejects_inconsistent_result():
    service = build_service()
    company = service.resolve_company("白酒样本01")
    period = "2025"
    previous_period = service.previous_period(company.company_id, period)
    previous_balance = service.previous_balance(company.company_id, period)
    income, balance, cash_flow, previous_income = service.statements(
        company.company_id,
        period,
        previous_period,
    )
    calculation = FinancialCalculation(
        metric_id="roe",
        company_id=company.company_id,
        company_name=company.company_name,
        period=period,
        formula="net_profit / average(total_equity)",
        inputs={},
        result=99.0,
        unit="%",
        status=CALCULATED,
    )

    verification = FinancialVerificationEngine().verify(
        calculation=calculation,
        metric_id="roe",
        income=income,
        balance=balance,
        cash_flow=cash_flow,
        previous_income=previous_income,
        previous_balance=previous_balance,
    )

    assert verification.status == "failed"
    assert verification.passed is False
    assert verification.expected_value == 99.0
    assert verification.actual_value != 99.0


def test_evidence_is_linked_to_financial_data_source_entities():
    result = build_workflow().run("白酒样本01 2025 年盈利能力分析")
    sources = {item.source_id: item for item in result.data_sources}

    assert result.evidence
    assert sources
    for evidence in result.evidence:
        assert evidence.source_ids
        assert set(evidence.source_ids) <= set(sources)
        for source_id in evidence.source_ids:
            source = sources[source_id]
            assert source.company_id == evidence.company_id
            assert source.source_hash
            # Growth and average-balance metrics intentionally include the
            # prior-period statement source, so report_period may be prior.
            assert source.report_period in {"2024", "2025"}
    expected_sources = build_service().get_sources(
        list({
            source_id
            for evidence in result.evidence
            for source_id in evidence.source_ids
        })
    )
    assert {item.source_id for item in result.data_sources} == {
        item.source_id for item in expected_sources
    }


def test_anomaly_engine_uses_historical_baseline_not_fixed_threshold():
    def observation(company: str, period: str, value: float) -> FinancialObservation:
        calculation = FinancialCalculation(
            metric_id="revenue_growth",
            formula="growth",
            inputs={},
            result=value,
            unit="%",
        )
        return FinancialObservation(
            company_id=f"company_{company}",
            company_name=company,
            stock_code="000000",
            period=period,
            metric_id="revenue_growth",
            metric_name="营业收入增长率",
            value=value,
            unit="%",
            formula="growth",
            fact=f"{company} {period}",
            calculation=calculation,
            source_period=period,
        )

    observations = [
        observation("stable", str(period), value)
        for period, value in zip(range(2021, 2026), [10, 11, 10, 11, 10])
    ]
    observations.extend(
        observation("abnormal", str(period), value)
        for period, value in zip(range(2021, 2026), [5, 6, 4, 5, 30])
    )
    anomalies = FinancialAnomalyEngine().detect(observations)

    assert [item.company_name for item in anomalies] == ["abnormal"]
    anomaly = anomalies[0]
    assert anomaly.period == "2025"
    assert anomaly.historical_mean == 5.0
    assert anomaly.historical_std > 0
    assert abs(anomaly.z_score) >= 2


def test_comparison_is_routed_only_for_explicit_tasks():
    workflow = build_workflow()
    companies = ["白酒样本01", "白酒样本02"]

    overview = workflow.run(FinancialQuery(
        question="两家公司 2025 年经营情况",
        task_type=FinancialTaskType.COMPANY_OVERVIEW,
        company_names=companies,
        start_year=2025,
        end_year=2025,
    ))
    peer = workflow.run(FinancialQuery(
        question="比较两家公司 2025 年收入增长和净利率",
        task_type=FinancialTaskType.PEER_COMPARISON,
        company_names=companies,
        start_year=2025,
        end_year=2025,
    ))

    assert overview.comparisons == []
    assert peer.comparisons
    assert all(item.period == "2025" for item in peer.comparisons)


def test_adapter_reports_v9_2_semantics():
    adapter = FinancialEvaluationAdapter(build_workflow())
    result = build_workflow().run("白酒样本01 2025 年盈利能力分析")
    from langgraph_langchain.runtime.evaluation import GoldenCaseLoader

    case = GoldenCaseLoader(REPO_ROOT / "tests" / "financial" / "golden_cases").load()[0]
    candidate = adapter.run_case(case.model_copy(update={"question": result.query.question}))

    assert adapter.SKILL_VERSION == "9.2.0"
    assert candidate.metadata["evidence_count"] == len(result.evidence)
    assert candidate.metadata["verified_evidence_count"] == len(result.evidence)
    assert candidate.metadata["data_source_count"] == len(result.data_sources)
    assert candidate.metadata["unavailable_calculation_count"] == 0
    assert candidate.metadata["invalid_calculation_count"] == 0

def test_nan_is_invalid():
    service = build_service()
    company = service.resolve_company("白酒样本01")
    income, balance, cash_flow, _ = service.statements(company.company_id, "2025")
    computation = compute_metric(
        "revenue",
        income=income.model_copy(update={"revenue": float("nan")}),
        balance=balance,
        cash_flow=cash_flow,
    )

    assert computation.status == INVALID
    assert computation.value is None
    assert computation.reason == "reported_value_not_finite"


def test_positive_infinity_is_invalid():
    service = build_service()
    company = service.resolve_company("白酒样本01")
    income, balance, cash_flow, _ = service.statements(company.company_id, "2025")
    computation = compute_metric(
        "revenue",
        income=income.model_copy(update={"revenue": float("inf")}),
        balance=balance,
        cash_flow=cash_flow,
    )

    assert computation.status == INVALID
    assert computation.value is None
    assert computation.reason == "reported_value_not_finite"


def test_negative_infinity_is_invalid():
    service = build_service()
    company = service.resolve_company("白酒样本01")
    income, balance, cash_flow, _ = service.statements(company.company_id, "2025")
    computation = compute_metric(
        "revenue",
        income=income.model_copy(update={"revenue": float("-inf")}),
        balance=balance,
        cash_flow=cash_flow,
    )

    assert computation.status == INVALID
    assert computation.value is None
    assert computation.reason == "reported_value_not_finite"


def test_non_finite_ratio_is_invalid():
    service = build_service()
    company = service.resolve_company("白酒样本01")
    income, balance, cash_flow, _ = service.statements(company.company_id, "2025")
    computation = compute_metric(
        "debt_to_asset",
        income=income,
        balance=balance.model_copy(update={"total_liabilities": float("nan")}),
        cash_flow=cash_flow,
    )

    assert computation.status == INVALID
    assert computation.value is None
    assert computation.reason == "ratio_value_not_finite"


def test_non_finite_growth_is_invalid():
    service = build_service()
    company = service.resolve_company("白酒样本01")
    income, balance, cash_flow, previous_income = service.statements(
        company.company_id,
        "2025",
        service.previous_period(company.company_id, "2025"),
    )
    previous_balance = service.previous_balance(company.company_id, "2025")
    computation = compute_metric(
        "revenue_growth",
        income=income,
        balance=balance,
        cash_flow=cash_flow,
        previous_income=previous_income.model_copy(update={"revenue": float("inf")}),
        previous_balance=previous_balance,
    )

    assert computation.status == INVALID
    assert computation.value is None
    assert computation.reason == "growth_value_not_finite"


def test_verification_engine_marks_non_finite_actual_result_invalid():
    service = build_service()
    company = service.resolve_company("白酒样本01")
    period = "2025"
    previous_period = service.previous_period(company.company_id, period)
    previous_balance = service.previous_balance(company.company_id, period)
    income, balance, cash_flow, previous_income = service.statements(
        company.company_id,
        period,
        previous_period,
    )
    calculation = FinancialCalculation(
        metric_id="revenue",
        company_id=company.company_id,
        company_name=company.company_name,
        period=period,
        formula="reported:income.revenue",
        inputs={},
        result=100.0,
        unit="CNY",
        status=CALCULATED,
    )

    verification = FinancialVerificationEngine().verify(
        calculation=calculation,
        metric_id="revenue",
        income=income.model_copy(update={"revenue": float("nan")}),
        balance=balance,
        cash_flow=cash_flow,
        previous_income=previous_income,
        previous_balance=previous_balance,
    )

    assert verification.status == INVALID
    assert verification.passed is False
    assert verification.actual_value is None
    assert verification.message == "独立公式验证结果是非有限值，判定为 INVALID。"

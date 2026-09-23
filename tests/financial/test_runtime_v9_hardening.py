"""Runtime V9.1 financial hardening regression tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from langgraph_langchain.runtime.financial import (
    FinancialAnalysisWorkflow,
    FinancialDataService,
    calculate_metric,
)
from langgraph_langchain.runtime.financial.evaluation import FinancialEvaluationAdapter
from langgraph_langchain.runtime.financial.risk_detector import FinancialRiskDetector

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "tests" / "financial" / "data" / "financial"
CASES_DIR = REPO_ROOT / "tests" / "financial" / "golden_cases"


def build_workflow() -> FinancialAnalysisWorkflow:
    service = FinancialDataService.from_csv_directory(DATA_DIR)
    return FinancialAnalysisWorkflow(service)


def test_average_balance_metrics_use_current_and_previous_balance():
    service = FinancialDataService.from_csv_directory(DATA_DIR)
    company = service.resolve_company("白酒样本01")
    income, balance, cash_flow, _ = service.statements(company.company_id, "2025")
    previous_balance = service.previous_balance(company.company_id, "2025")

    value, inputs = calculate_metric(
        "roe",
        income=income,
        balance=balance,
        cash_flow=cash_flow,
        previous_balance=previous_balance,
    )
    expected_denominator = (
        balance.total_equity + previous_balance.total_equity
    ) / 2
    assert inputs["average_denominator"] == expected_denominator
    assert inputs["current_denominator"] == balance.total_equity
    assert inputs["previous_denominator"] == previous_balance.total_equity
    assert value == pytest.approx((income.net_profit / expected_denominator) * 100)


def test_workflow_builds_verified_evidence_and_findings():
    result = build_workflow().run("白酒样本01 2025 年盈利能力分析")

    assert result.observations
    assert len(result.evidence) == len(result.observations)
    assert len(result.verifications) == len(result.observations)
    assert all(item.verification_status == "verified" for item in result.evidence)
    assert all(item.source_ids for item in result.evidence)
    assert all(item.source_fields for item in result.evidence)
    assert all(item.passed for item in result.verifications)
    assert result.findings
    evidence_ids = {item.evidence_id for item in result.evidence}
    for finding in result.findings:
        assert finding.evidence_ids
        assert set(finding.evidence_ids) <= evidence_ids
    assert all(item.evidence_ids for item in result.findings)


def test_workflow_performs_ranked_peer_comparison():
    result = build_workflow().run("比较白酒样本01和白酒样本02 2025 年的收入增长和净利率")

    assert result.comparisons
    assert {"revenue", "revenue_growth", "net_margin"} <= {
        item.metric_id for item in result.comparisons
    }
    comparison = next(item for item in result.comparisons if item.metric_id == "revenue")
    assert [entity.rank for entity in comparison.entities] == [1, 2]
    assert comparison.leader_name != comparison.laggard_name
    assert comparison.difference == pytest.approx(
        comparison.leader_value - comparison.laggard_value,
    )
    assert comparison.statement
    assert any(
        finding.finding_type == "peer_comparison"
        and finding.metric_id == "revenue"
        for finding in result.findings
    )


def test_risk_detector_uses_ocf_ratio_machine_value():
    risks = FinancialRiskDetector().detect([
        {"company_name": "A", "period": "2025", "metric_id": "ocf_to_net_income", "value": 0.42},
        {"company_name": "B", "period": "2025", "metric_id": "ocf_to_net_income", "value": 1.2},
    ])
    assert len(risks) == 1
    assert risks[0].company_name == "A"
    assert risks[0].unit == "x"
    assert risks[0].display_value == "0.42x"


def test_financial_adapter_counts_real_evidence_not_calculations_as_proxy():
    workflow = build_workflow()
    result = workflow.run("白酒样本01 2025 年盈利能力分析")
    adapter = FinancialEvaluationAdapter(workflow)
    candidate = adapter.run_case(
        __import__("langgraph_langchain.runtime.evaluation", fromlist=["GoldenCaseLoader"])
        .GoldenCaseLoader(CASES_DIR)
        .load()[0].model_copy(update={"question": result.query.question})
    )

    assert candidate.evidence_count == len(result.evidence)
    assert candidate.verified_evidence_count == len(result.evidence)
    assert candidate.finding_count == len(result.findings)
    assert candidate.status == "succeeded"
    assert candidate.metadata["calculation_count"] == len(result.calculations)
    assert candidate.metadata["evidence_count"] == len(result.evidence)
    assert candidate.metadata["verified_evidence_count"] == len(result.evidence)

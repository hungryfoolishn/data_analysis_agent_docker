"""Runtime V9 financial golden evaluation acceptance tests."""

from __future__ import annotations

from pathlib import Path

from langgraph_langchain.runtime.evaluation import GoldenCaseLoader
from langgraph_langchain.runtime.financial import (
    FinancialAnalysisWorkflow,
    FinancialDataService,
    financial_metric_registry,
)
from langgraph_langchain.runtime.financial.evaluation import FinancialEvaluationAdapter

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "tests" / "financial" / "data" / "financial"
CASES_DIR = REPO_ROOT / "tests" / "financial" / "golden_cases"


def build_adapter() -> FinancialEvaluationAdapter:
    service = FinancialDataService.from_csv_directory(DATA_DIR)
    return FinancialEvaluationAdapter(FinancialAnalysisWorkflow(service))


def test_financial_metric_layer_has_core_semantics():
    assert len(financial_metric_registry) >= 30
    for metric_id in (
        "revenue", "revenue_growth", "gross_margin", "net_margin", "roe",
        "debt_to_asset", "operating_cash_flow", "free_cash_flow",
        "ocf_to_net_income",
    ):
        definition = financial_metric_registry.get(metric_id)
        assert definition.formula
        assert definition.source_fields


def test_financial_golden_fixture_has_domain_coverage():
    service = FinancialDataService.from_csv_directory(DATA_DIR)
    companies = service.list_companies()
    assert len(companies) >= 20
    assert len({item.industry_id for item in companies}) >= 5
    for company in companies:
        assert len(service.periods_for(company.company_id)) == 5


def test_financial_golden_dataset_has_fifty_valid_cases():
    loader = GoldenCaseLoader(CASES_DIR)
    cases = loader.load()
    assert len(cases) >= 50
    assert len({case.case_id for case in cases}) == len(cases)
    assert loader.validate() == []
    tags = {tag for case in cases for tag in case.tags}
    assert {
        "basic", "trend", "revenue", "profit", "profitability",
        "cashflow", "solvency", "operating", "peer", "anomaly",
        "comprehensive", "risk",
    } <= tags
    assert sum(case.critical for case in cases) >= 3


def test_financial_golden_adapter_passes_all_cases():
    loader = GoldenCaseLoader(CASES_DIR)
    cases = loader.load()
    assert loader.validate() == []

    run = build_adapter().run_dataset(cases, run_id="financial_golden_v9_test")
    assert run.total_cases == len(cases)
    assert run.passed_cases == len(cases)
    assert run.failed_cases == 0
    assert run.pass_rate == 1.0
    assert not run.failures
    assert run.by_skill_id == {FinancialEvaluationAdapter.SKILL_ID: 1.0}
    assert run.by_skill_hash == {build_adapter().skill_hash: 1.0}

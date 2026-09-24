"""Real financial data loader conformance tests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from dataclasses import replace

from langgraph_langchain.runtime.financial import (
    FormulaGoldenLoader,
    FormulaGoldenRunner,
    MetricComputation,
    RealFinancialDataLoader,
    compute_metric,
)
from langgraph_langchain.runtime.financial.real_data import RealFinancialDataLoader


COMPANIES = [
    {
        "company_id": "600519",
        "company_name": "贵州茅台",
        "stock_code": "600519",
        "exchange": "SSE",
        "industry_id": "baijiu",
    },
    {
        "company_id": "000858",
        "company_name": "五粮液",
        "stock_code": "000858",
        "exchange": "SZSE",
        "industry_id": "baijiu",
    },
    {
        "company_id": "000568",
        "company_name": "泸州老窖",
        "stock_code": "000568",
        "exchange": "SZSE",
        "industry_id": "baijiu",
    },
]
PERIODS = ["2021", "2022", "2023", "2024", "2025"]
CORE_METRICS = [
    "revenue",
    "cost_of_revenue",
    "gross_profit",
    "operating_profit",
    "net_profit",
    "net_profit_attributable",
    "total_assets",
    "total_liabilities",
    "total_equity",
    "operating_cash_flow",
]


def _write_loader_fixture(root: Path) -> Path:
    raw_dir = root / "raw"
    raw_dir.mkdir(parents=True)
    companies = []
    periods = []
    sources = []

    for company_index, company in enumerate(COMPANIES):
        companies.append(company)
        for period_index, raw_period in enumerate(PERIODS):
            normalized_period = f"{raw_period}年度"
            source_id = f"{company['company_id']}_{raw_period}_annual_report"
            raw_file = f"raw/{source_id}.json"
            value_base = 1_000_000.0 + company_index * 100_000 + period_index * 50_000
            facts = [
                {
                    "company_id": company["company_id"],
                    "source_id": source_id,
                    "original_period": normalized_period,
                    "metric_id": metric_id,
                    "value": value_base + offset,
                    "unit": "CNY",
                    "original_metric_name": metric_id,
                    "source_label": f"annual_report.{metric_id}",
                }
                for offset, metric_id in enumerate(CORE_METRICS)
            ]
            raw_path = root / raw_file
            raw_path.write_text(
                json.dumps({"facts": facts}, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            digest = hashlib.sha256(raw_path.read_bytes()).hexdigest()
            sources.append({
                "source_id": source_id,
                "source_type": "annual_report",
                "company_id": company["company_id"],
                "report_period": raw_period,
                "document_name": f"{company['company_name']}{raw_period}年度报告",
                "document_url": "https://example.com/annual-report.pdf",
                "raw_file": raw_file,
                "source_hash": f"sha256:{digest}",
            })
            periods.append(raw_period)

    manifest = {
        "dataset_version": "loader-conformance-v1",
        "companies": companies,
        "periods": PERIODS,
        "sources": sources,
    }
    manifest_path = root / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest_path


def test_real_financial_loader_builds_verified_statements(tmp_path: Path):
    _write_loader_fixture(tmp_path)
    dataset = RealFinancialDataLoader(tmp_path).load(strict=True)

    assert dataset.manifest.dataset_version == "loader-conformance-v1"
    assert len(dataset.manifest.companies) == 3
    assert len(dataset.manifest.sources) == 15
    assert dataset.validation_errors == []

    company_ids = {item.company_id for item in dataset.manifest.companies}
    for company_id in company_ids:
        assert dataset.data_service.periods_for(company_id) == PERIODS
        previous = dataset.data_service.previous_period(company_id, "2025")
        assert previous == "2024"

    service = dataset.data_service
    company = service.resolve_company("贵州茅台")
    income, balance, cash_flow, previous_income = service.statements(
        company.company_id,
        "2025",
        "2024",
    )

    assert income.source_id == "600519_2025_annual_report"
    assert income.revenue is not None
    assert balance.total_assets is not None
    assert cash_flow.operating_cash_flow is not None
    assert previous_income.revenue is not None

    source = service.get_source(income.source_id)
    assert source.source_type == "annual_report"
    assert source.report_period == "2025"
    assert source.source_hash.startswith("sha256:")

    revenue_fact = next(
        item for item in dataset.facts
        if item.company_id == company.company_id
        and item.period == "2025"
        and item.metric_id == "revenue"
    )
    assert revenue_fact.status == "VALID"
    assert revenue_fact.original_period == "2025年度"
    assert revenue_fact.normalized_unit == "CNY"


def test_real_financial_loader_rejects_source_hash_mismatch(tmp_path: Path):
    manifest_path = _write_loader_fixture(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["sources"][0]["source_hash"] = "sha256:" + "0" * 64
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="source hash mismatch"):
        RealFinancialDataLoader(tmp_path).load(strict=True)


def test_real_financial_loader_requires_three_companies_and_five_periods(tmp_path: Path):
    _write_loader_fixture(tmp_path)
    manifest_path = tmp_path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["companies"] = manifest["companies"][:2]
    manifest["periods"] = manifest["periods"][:4]
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    dataset = RealFinancialDataLoader(tmp_path).load(strict=False)
    assert any("requires >=3 companies" in item for item in dataset.validation_errors)
    assert any("requires >=5 periods" in item for item in dataset.validation_errors)


def _write_separated_loader_fixture(root: Path) -> Path:
    raw_dir = root / "raw"
    facts_dir = root / "facts"
    raw_dir.mkdir(parents=True)
    facts_dir.mkdir(parents=True)
    companies = []
    sources = []

    for company_index, company in enumerate(COMPANIES):
        companies.append(company)
        for period_index, raw_period in enumerate(PERIODS):
            source_id = f"{company['company_id']}_{raw_period}_annual_report"
            raw_file = f"raw/{source_id}.json"
            facts_file = f"facts/{source_id}.json"
            value_base = 1_000_000.0 + company_index * 100_000 + period_index * 50_000
            facts = [
                {
                    "company_id": company["company_id"],
                    "source_id": source_id,
                    "original_period": f"{raw_period}年度",
                    "metric_id": metric_id,
                    "value": value_base + offset,
                    "unit": "CNY",
                    "original_metric_name": metric_id,
                    "source_label": f"annual_report.{metric_id}",
                }
                for offset, metric_id in enumerate(CORE_METRICS)
            ]
            raw_path = root / raw_file
            raw_path.write_text(
                json.dumps({
                    "provider": "test-provider",
                    "report_period": f"{raw_period}年度",
                    "responses": {},
                }, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            facts_path = root / facts_file
            facts_path.write_text(
                json.dumps({"facts": facts}, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            sources.append({
                "source_id": source_id,
                "source_type": "annual_report_api_snapshot",
                "company_id": company["company_id"],
                "report_period": raw_period,
                "document_name": f"{company['company_name']}{raw_period}年度报告",
                "document_url": "https://example.com/annual-report",
                "raw_file": raw_file,
                "facts_file": facts_file,
                "source_hash": f"sha256-canonical-text:{hashlib.sha256(raw_path.read_text(encoding='utf-8').replace(chr(13)+chr(10), chr(10)).encode()).hexdigest()}",
                "facts_file_hash": f"sha256-canonical-text:{hashlib.sha256(facts_path.read_text(encoding='utf-8').replace(chr(13)+chr(10), chr(10)).encode()).hexdigest()}",
            })

    manifest = {
        "dataset_version": "loader-separated-facts-v1",
        "companies": companies,
        "periods": PERIODS,
        "sources": sources,
    }
    manifest_path = root / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest_path


def test_real_financial_loader_separates_raw_source_and_facts(tmp_path: Path):
    _write_separated_loader_fixture(tmp_path)
    dataset = RealFinancialDataLoader(tmp_path).load(strict=True)

    assert dataset.validation_errors == []
    assert len(dataset.manifest.sources) == 15
    assert len(dataset.facts) == 15 * len(CORE_METRICS)
    source = dataset.manifest.sources[0]
    assert source.raw_file != source.facts_file
    assert source.source_hash.startswith("sha256-canonical-text:")
    assert source.facts_file_hash.startswith("sha256-canonical-text:")
    assert all(item.source_id == source.source_id for item in dataset.facts if item.company_id == source.company_id and item.period == "2021")


def test_real_financial_loader_rejects_facts_hash_mismatch(tmp_path: Path):
    manifest_path = _write_separated_loader_fixture(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["sources"][0]["facts_file_hash"] = "sha256-canonical-text:" + "0" * 64
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="facts file hash mismatch"):
        RealFinancialDataLoader(tmp_path).load(strict=True)


REAL_DATA_DIR = Path(__file__).resolve().parents[1] / "financial" / "real_data"
REAL_GOLDEN_DIR = REAL_DATA_DIR / "formula_golden"


def test_versioned_real_financial_dataset_loads_with_provenance():
    dataset = RealFinancialDataLoader(REAL_DATA_DIR).load(strict=True)

    assert dataset.manifest.dataset_version == "financial-real-v1"
    assert len(dataset.manifest.companies) == 3
    assert dataset.manifest.periods == ["2021", "2022", "2023", "2024", "2025"]
    assert len(dataset.manifest.sources) == 15
    assert len(dataset.facts) == 273
    assert dataset.validation_errors == []

    for source in dataset.manifest.sources:
        assert source.source_type == "annual_report_api_snapshot"
        assert source.document_url.startswith("https://")
        assert source.source_hash.startswith("sha256-canonical-text:")
        assert source.facts_file_hash.startswith("sha256-canonical-text:")
        assert source.published_at

    service = dataset.data_service
    income, balance, cash_flow, previous_income = service.statements(
        "600519", "2025", "2024"
    )
    assert income.revenue == 172_054_171_890.91
    assert income.net_profit == 85_310_324_833.67
    assert balance.total_assets == 303_834_844_021.44
    assert balance.total_equity == 253_959_253_909.07
    assert cash_flow.operating_cash_flow == 61_522_204_989.35
    assert previous_income.revenue == 174_144_069_958.25


def test_versioned_real_financial_formula_golden_passes():
    dataset = RealFinancialDataLoader(REAL_DATA_DIR).load(strict=True)
    cases = FormulaGoldenLoader(REAL_GOLDEN_DIR).load()
    summary = FormulaGoldenRunner(dataset.data_service).run(cases)

    assert len(cases) == 153
    assert summary.total_cases == 153
    assert summary.passed_cases == 153
    assert summary.failed_cases == 0
    assert summary.pass_rate == 1.0
    assert not summary.failures


def test_real_formula_golden_detects_wrong_metric_result():
    dataset = RealFinancialDataLoader(REAL_DATA_DIR).load(strict=True)
    cases = FormulaGoldenLoader(REAL_GOLDEN_DIR).load()
    revenue_case = next(
        item for item in cases
        if item.company_id == "600519" and item.period == "2025" and item.metric_id == "revenue"
    )

    def wrong_result(metric_id: str, **kwargs):
        computation = compute_metric(metric_id, **kwargs)
        return replace(computation, value=computation.value + 1)

    summary = FormulaGoldenRunner(
        dataset.data_service,
        metric_computer=wrong_result,
    ).run([revenue_case])

    assert summary.failed_cases == 1
    assert summary.failures[0].status == "failed"
    assert summary.failures[0].expected == revenue_case.expected
    assert summary.failures[0].actual == revenue_case.expected + 1


def test_real_formula_golden_detects_roe_ending_equity_mutation():
    dataset = RealFinancialDataLoader(REAL_DATA_DIR).load(strict=True)
    cases = FormulaGoldenLoader(REAL_GOLDEN_DIR).load()
    roe_case = next(
        item for item in cases
        if item.company_id == "600519" and item.period == "2025" and item.metric_id == "roe"
    )

    def mutated_roe(metric_id: str, **kwargs):
        computation = compute_metric(metric_id, **kwargs)
        if metric_id != "roe":
            return computation
        income = kwargs["income"]
        balance = kwargs["balance"]
        return MetricComputation(
            metric_id=metric_id,
            status="calculated",
            value=income.net_profit / balance.total_equity * 100,
            inputs=computation.inputs,
        )

    summary = FormulaGoldenRunner(
        dataset.data_service,
        metric_computer=mutated_roe,
    ).run([roe_case])

    assert summary.failed_cases == 1
    assert summary.failures[0].case_id == roe_case.case_id
    assert summary.failures[0].status == "failed"
    assert summary.failures[0].message.startswith("MetricEngine differs by")

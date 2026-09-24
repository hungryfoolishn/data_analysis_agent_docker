"""Real financial data loader conformance tests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

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

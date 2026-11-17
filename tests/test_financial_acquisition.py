"""B10.5: B8-integrated SEC and China financial filing adapters."""

from __future__ import annotations

from decimal import Decimal

import pytest

from langgraph_langchain.data.adapters.base import (
    AdapterQualityError,
    AdapterRequestError,
    FetchRequest,
)
from langgraph_langchain.data.adapters.registry import AdapterRegistry
from langgraph_langchain.data.acquisition import AcquisitionService
from langgraph_langchain.runtime.context import AnalysisRuntime
from langgraph_langchain.runtime.financial.china import ChinaCompanyFilingsAdapter
from langgraph_langchain.runtime.financial.sec import SecCompanyFactsAdapter


def _row(**overrides):
    fields = {
        "market": "us",
        "entity_id": "ent_us",
        "canonical_concept": "revenue",
        "statement_type": "income",
        "period_type": "duration",
        "period_start": "2024-01-01",
        "period_end": "2024-12-31",
        "fiscal_year": 2024,
        "fiscal_period": "FY",
        "reporting_basis": "consolidated",
        "currency": "USD",
        "value": Decimal("1234.56"),
        "statistic_kind": "absolute_amount",
        "unit": "USD",
        "accession_number": "0000000000-24-000001",
        "filing_date": "2025-02-01",
        "source_document_id": "doc-1",
    }
    fields.update(overrides)
    return fields


def test_sec_adapter_normalizes_and_validates_fixture_facts():
    adapter = SecCompanyFactsAdapter(transport=lambda url, params: [_row()])
    result = adapter.fetch(
        FetchRequest(
            dataset_id="sec_company_facts",
            parameters={"cik": "0000320193", "canonical_concept": "Revenues"},
        )
    )
    assert result.quality_report.status == "passed"
    assert len(result.dataframe) == 1
    assert result.dataframe.iloc[0]["value"] == "1234.56"
    assert result.dataframe.iloc[0]["accession_number"] == "0000000000-24-000001"
    assert result.source_metadata["adapter_id"] == "financial_sec_company_facts"


def test_sec_adapter_rejects_missing_traceability():
    adapter = SecCompanyFactsAdapter(
        transport=lambda url, params: [_row(source_document_id="")]
    )
    with pytest.raises(AdapterQualityError, match="source_document_id"):
        adapter.fetch(
            FetchRequest(
                dataset_id="sec_company_facts",
                parameters={"cik": "0000320193", "canonical_concept": "Revenues"},
            )
        )


def test_sec_adapter_requires_request_parameters():
    adapter = SecCompanyFactsAdapter(transport=lambda url, params: [])
    with pytest.raises(AdapterRequestError, match="cik"):
        adapter.fetch(FetchRequest(dataset_id="sec_company_facts", parameters={}))


def test_china_adapter_normalizes_and_validates_fixture_filings():
    adapter = ChinaCompanyFilingsAdapter(
        transport=lambda url, params: [_row(market="cn", currency="CNY", unit="CNY")]
    )
    result = adapter.fetch(
        FetchRequest(
            dataset_id="cn_company_filings",
            parameters={"stock_code": "600000"},
        )
    )
    assert result.quality_report.status == "passed"
    assert result.dataframe.iloc[0]["market"] == "cn"
    assert result.dataframe.iloc[0]["currency"] == "CNY"
    assert result.dataframe.iloc[0]["value"] == "1234.56"


def test_china_adapter_rejects_missing_filing_date():
    adapter = ChinaCompanyFilingsAdapter(
        transport=lambda url, params: [_row(market="cn", filing_date="")]
    )
    with pytest.raises(AdapterQualityError, match="filing_date"):
        adapter.fetch(
            FetchRequest(dataset_id="cn_company_filings", parameters={"stock_code": "600000"})
        )


def test_financial_adapters_register_into_existing_b8_registry():
    registry = AdapterRegistry()
    registry.register(SecCompanyFactsAdapter())
    registry.register(ChinaCompanyFilingsAdapter())
    ids = {adapter.describe().adapter_id for adapter in registry.adapters()}
    assert "financial_sec_company_facts" in ids
    assert "financial_cn_company_filings" in ids
    assert len(registry.catalog()) == 3


def test_adapter_deduplicates_identical_filing_rows():
    adapter = SecCompanyFactsAdapter(transport=lambda url, params: [_row(), _row()])
    result = adapter.fetch(
        FetchRequest(
            dataset_id="sec_company_facts",
            parameters={"cik": "0000320193", "canonical_concept": "Revenues"},
        )
    )
    assert len(result.dataframe) == 1


def test_sec_adapter_acquires_through_existing_b8_service(tmp_path):
    runtime = AnalysisRuntime(
        workspace_dir=tmp_path / "workspace",
        session_id="financial-acq-test",
        question="load SEC filing fixture",
    )
    registry = AdapterRegistry()
    registry.register(SecCompanyFactsAdapter(transport=lambda url, params: [_row()]))
    service = AcquisitionService(
        runtime=runtime,
        registry=registry,
        history_dir=tmp_path / "history",
    )
    outcome = service.acquire(
        FetchRequest(
            dataset_id="sec_company_facts",
            parameters={"cik": "0000320193", "canonical_concept": "Revenues"},
        )
    )
    asset = outcome.asset
    assert asset.source_metadata["dataset_id"] == "sec_company_facts"
    assert asset.source_metadata["source_id"] == "financial_sec_company_facts"
    assert asset.source_metadata["snapshot_reference"]["snapshot_id"]

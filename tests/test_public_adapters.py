from __future__ import annotations

import pytest

from langgraph_langchain.data.adapters.base import (
    AdapterQualityError,
    AdapterRequestError,
    AdapterTemporaryError,
    FetchRequest,
)
from langgraph_langchain.data.adapters.public import (
    BEAAdapter,
    BLSAdapter,
    FREDAdapter,
    SECAdapter,
    default_adapter_registry,
)


def _result(rows):
    return {"rows": rows}


def params_check(actual, expected):
    return all(actual.get(key) == value for key, value in expected.items())


def test_registry_contains_four_adapters_and_eight_datasets():
    registry = default_adapter_registry()
    assert len(registry.adapters()) == 4
    catalog = registry.catalog()
    assert len(catalog) == 8
    assert len({entry.adapter_id for entry in catalog}) == 4


def test_fred_adapter_fetches_and_normalizes(tmp_path, monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "test-key")
    adapter = FREDAdapter(cache=None)
    calls = []
    def transport(url, params):
        calls.append((url, params))
        return {
            "observations": [
                {"date": "2026-01-01", "value": "100.5", "series_id": "GDP"},
                {"date": "2026-01-02", "value": "101.5", "series_id": "GDP"},
            ]
        }
    adapter._transport = transport
    request = FetchRequest(dataset_id="fred_gdp", start="2026-01-01", end="2026-01-02")
    result = adapter.fetch(request)

    assert result.adapter_id == "fred"
    assert result.dataset_id == "fred_gdp"
    assert result.dataframe["value"].tolist() == [100.5, 101.5]
    assert result.quality_report.status == "passed"
    assert result.content_hash
    assert result.fetch_metadata["fetched_at"]
    assert params_check(calls[-1][1], {"series_id": "GDP", "file_type": "json"})
    assert result.dataframe.columns.tolist() == ["date", "value", "series_id"]


def params_ok(actual, expected):
    return all(actual.get(key) == value for key, value in expected.items())


def test_bls_adapter_fetches_two_datasets():
    adapter = BLSAdapter()
    payloads = {
        "bls_employment": {
            "Results": {"series": [{"seriesID": "CES0000000001", "data": [{"period": "M01", "value": "150000"}]}]}
        },
        "bls_cpi": {
            "Results": {"series": [{"seriesID": "CUUR0000SA0", "data": [{"period": "M01", "value": "300.5"}]}]}
        },
    }
    def transport(url, params):
        return payloads["bls_employment" if "CES" in url else "bls_cpi"]
    adapter._transport = transport

    first = adapter.fetch(FetchRequest(dataset_id="bls_employment"))
    second = adapter.fetch(FetchRequest(dataset_id="bls_cpi"))

    assert first.dataframe["value"].iloc[0] == 150000
    assert second.dataframe["value"].iloc[0] == 300.5
    assert first.quality_report.is_usable
    assert second.quality_report.is_usable


def test_bea_adapter_fetches_and_normalizes():
    adapter = BEAAdapter()
    def transport(url, params):
        return {
            "BEAAPI": {"Results": {"Data": [{"TimePeriod": "2025", "DataValue": "25,000.0", "LineDescription": "Gross domestic product"}]}}
        }
    adapter._transport = transport
    result = adapter.fetch(FetchRequest(dataset_id="bea_gdp", parameters={"year": "ALL"}))
    assert result.dataframe["value"].tolist() == [25000.0]
    assert result.dataframe["line_description"].iloc[0] == "Gross domestic product"


def test_sec_adapter_fetches_two_concepts():
    adapter = SECAdapter()
    payloads = {
        "sec_company_revenues": {
            "units": {"USD": [{"start": "2025-01-01", "end": "2025-03-31", "val": "1000", "form": "10-Q", "accn": "0001"}]}
        },
        "sec_company_net_income": {
            "units": {"USD": [{"start": "2025-01-01", "end": "2025-03-31", "val": "200", "form": "10-Q", "accn": "0002"}]}
        },
    }
    def transport(url, params):
        return payloads["sec_company_revenues" if "Revenues" in url else "sec_company_net_income"]
    adapter._transport = transport

    revenues = adapter.fetch(FetchRequest(dataset_id="sec_company_revenues", parameters={"cik": "0000320193"}))
    income = adapter.fetch(FetchRequest(dataset_id="sec_company_net_income", parameters={"cik": "0000320193"}))

    assert revenues.dataframe["value"].iloc[0] == 1000
    assert income.dataframe["value"].iloc[0] == 200
    assert revenues.quality_report.is_usable
    assert income.quality_report.is_usable


def test_adapter_cache_and_refresh(tmp_path, monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "test-key")
    adapter = FREDAdapter()
    monkey_payload = {
        "observations": [{"date": "2026-01-01", "value": "100.5", "series_id": "GDP"}]
    }
    calls = []
    def transport(url, params):
        calls.append(url)
        return monkey_payload
    adapter._transport = transport
    request = FetchRequest(dataset_id="fred_gdp")
    first = adapter.fetch(request)
    second = adapter.fetch(request)
    refreshed = adapter.fetch(request, refresh=True)

    assert len(calls) == 2
    assert first.content_hash == second.content_hash
    assert first.content_hash == refreshed.content_hash


def test_adapter_retry_and_quality_failure(tmp_path):
    adapter = BLSAdapter()
    good = {"Results": {"series": [{"seriesID": "CES0000000001", "data": [{"period": "M01", "value": "150000"}]}]}}
    calls = []
    def flaky(url, params):
        calls.append(url)
        if len(calls) == 1:
            raise ConnectionError("temporary")
        return good
    adapter._transport = flaky
    result = adapter.fetch(FetchRequest(dataset_id="bls_employment"))
    assert len(calls) == 2

    empty = {"Results": {"series": [{"seriesID": "CES0000000001", "data": []}]}}
    adapter._transport = lambda url, params: empty
    with pytest.raises(AdapterQualityError):
        adapter.fetch(FetchRequest(dataset_id="bls_employment"), refresh=True)


def test_invalid_requests_are_rejected(tmp_path):
    adapter = SECAdapter()
    with pytest.raises(AdapterRequestError):
        adapter.fetch(FetchRequest(dataset_id="sec_company_revenues", parameters={}))
    with pytest.raises(AdapterRequestError):
        adapter.fetch(FetchRequest(dataset_id="unknown_dataset"))

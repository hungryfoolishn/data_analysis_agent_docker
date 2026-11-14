"""B8.8: acquisition contract and asset consistency tests (offline)."""

from __future__ import annotations

import pytest

from langgraph_langchain.data.adapters.base import (
    AdapterRequestError,
    FetchRequest,
)
from langgraph_langchain.data.adapters.public import (
    BEAAdapter,
    BLSAdapter,
    FREDAdapter,
    NBSAdapter,
    SECAdapter,
)
from langgraph_langchain.data.acquisition import AcquisitionService
from langgraph_langchain.data.adapters.registry import AdapterRegistry
from langgraph_langchain.runtime.context import AnalysisRuntime


def _fred_adapter(tag: str = "1") -> FREDAdapter:
    adapter = FREDAdapter(cache=None)
    adapter._transport = lambda url, params: {
        "observations": [
            {"date": "2026-03-01", "value": f"102.{tag}", "series_id": "GDP"},
            {"date": "2026-01-01", "value": "100.5", "series_id": "GDP"},
            {"date": "2026-02-01", "value": "101.5", "series_id": "GDP"},
        ]
    }
    adapter._credentials = lambda: {"api_key": "test-key"}
    return adapter


def _fred_request() -> FetchRequest:
    return FetchRequest(dataset_id="fred_gdp", start="2026-01-01", end="2026-03-31")


def _runtime(tmp_path, name: str) -> AnalysisRuntime:
    return AnalysisRuntime(
        workspace_dir=tmp_path / name, session_id=name, question="acquire data"
    )


def _service(tmp_path, runtime) -> AcquisitionService:
    registry = AdapterRegistry()
    registry.register(_fred_adapter())
    return AcquisitionService(
        runtime=runtime,
        registry=registry,
        history_dir=runtime.workspace_dir / ".data_acquisition",
    )


def test_fred_normalize_is_sorted_deduped_and_deterministic():
    adapter = _fred_adapter()
    frame = adapter.normalize(
        adapter._transport("", {}),
        _fred_request(),
        FREDAdapter.datasets["fred_gdp"],
    )
    assert frame["date"].dt.strftime("%Y-%m-%d").tolist() == [
        "2026-01-01",
        "2026-02-01",
        "2026-03-01",
    ]
    assert frame["value"].tolist() == [100.5, 101.5, 102.1]


def test_bls_period_keeps_observation_year():
    adapter = BLSAdapter(cache=None)
    adapter._transport = lambda url, params: {
        "Results": {
            "series": [
                {
                    "seriesID": "LNS14000000",
                    "data": [
                        {"year": "2024", "period": "M01", "value": "3.7"},
                        {"year": "2023", "period": "M01", "value": "3.4"},
                        {"year": "2024", "period": "M01", "value": "3.7"},
                    ],
                }
            ]
        }
    }
    frame = adapter.normalize(
        adapter._transport("", {}),
        FetchRequest(dataset_id="bls_unemployment"),
        BLSAdapter.datasets["bls_unemployment"],
    )
    assert frame["period"].tolist() == ["2023-M01", "2024-M01"]
    assert frame["value"].tolist() == [3.4, 3.7]


def test_bea_requires_api_key_before_transport_call(monkeypatch):
    monkeypatch.delenv("BEA_API_KEY", raising=False)
    adapter = BEAAdapter(cache=None)
    adapter._transport = lambda url, params: pytest.fail("transport must not be called")
    with pytest.raises(AdapterRequestError):
        adapter.fetch(FetchRequest(dataset_id="bea_gdp"))


def test_bea_normalize_is_sorted_and_deduped():
    adapter = BEAAdapter(cache=None)
    adapter._transport = lambda url, params: {
        "BEAAPI": {
            "Results": {
                "Data": [
                    {"TimePeriod": "2023", "DataValue": "1,000.5", "LineDescription": "Gross domestic product"},
                    {"TimePeriod": "2022", "DataValue": "900.0", "LineDescription": "Gross domestic product"},
                    {"TimePeriod": "2023", "DataValue": "1,000.5", "LineDescription": "Gross domestic product"},
                ]
            }
        }
    }
    frame = adapter.normalize(
        adapter._transport("", {}),
        FetchRequest(dataset_id="bea_gdp"),
        BEAAdapter.datasets["bea_gdp"],
    )
    assert frame["period"].tolist() == ["2022", "2023"]
    assert frame["value"].tolist() == [900.0, 1000.5]


def test_sec_keeps_distinct_revisions_and_collapses_exact_duplicates():
    adapter = SECAdapter(cache=None)
    adapter._transport = lambda url, params: {
        "units": {
            "USD": [
                {"start": "2024-01-01", "end": "2024-03-31", "val": "100", "form": "10-Q", "accn": "0001"},
                {"start": "2024-01-01", "end": "2024-03-31", "val": "100", "form": "10-Q", "accn": "0001"},
                {"start": "2024-01-01", "end": "2024-03-31", "val": "110", "form": "10-K", "accn": "0002"},
            ]
        }
    }
    frame = adapter.normalize(
        adapter._transport("", {}),
        FetchRequest(dataset_id="sec_company_revenues", parameters={"cik": "0000320193"}),
        SECAdapter.datasets["sec_company_revenues"],
    )
    assert len(frame) == 2
    assert sorted(frame["form"].tolist()) == ["10-K", "10-Q"]


def test_nbs_dedup_is_independent_of_payload_order():
    adapter = NBSAdapter(cache=None)
    payload = {
        "returncode": 200,
        "ret": [
            {"datanodes": [
                {"data": {"sj": "2016", "data": 744127.0, "code": "A010101"}},
                {"data": {"sj": "2015", "data": 613619.0, "code": "A010101"}},
                {"data": {"sj": "2015", "data": 613618.0, "code": "A010101"}},
            ]}
        ],
    }
    frame = adapter.normalize(
        payload, FetchRequest(dataset_id="nbs_gdp"), NBSAdapter.datasets["nbs_gdp"]
    )
    assert frame["period"].tolist() == ["2015", "2016"]
    assert frame["value"].tolist() == [613618.0, 744127.0]


def test_fetch_result_contract_keeps_unknown_metadata_unknown(tmp_path):
    runtime = _runtime(tmp_path, "contract")
    service = _service(tmp_path, runtime)
    outcome = service.acquire(_fred_request())
    metadata = outcome.asset.source_metadata
    assert metadata["release_date"] is None
    assert metadata["revision"] is None
    assert metadata["observation_period"] == ["2026-01-01", "2026-03-01"]
    assert metadata["fetched_at"]
    assert metadata["unit"] == "billions_of_chained_2017_dollars"


def test_register_fetch_result_does_not_return_asset_with_missing_snapshot(tmp_path):
    runtime = _runtime(tmp_path, "dedup")
    service = _service(tmp_path, runtime)
    outcome = service.acquire(_fred_request())
    asset = outcome.asset
    location = asset.source_metadata["snapshot_reference"]["snapshot_location"]
    (runtime.snapshot_store.root / location).unlink()

    # Same content, but the dedup candidate is no longer restorable:
    # the bridge must register a fresh snapshot-backed asset.
    fresh = service.registry.get("fred").fetch(_fred_request(), refresh=True)
    second = runtime.register_fetch_result(fresh)
    assert second.asset_id != asset.asset_id
    resolved = runtime.asset_resolver.resolve(second.asset_id)
    assert resolved.dataframe["value"].tolist() == [100.5, 101.5, 102.1]

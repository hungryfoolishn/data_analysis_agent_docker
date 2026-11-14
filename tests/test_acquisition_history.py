"""B8.9: acquisition version history and lineage tests (offline)."""

from __future__ import annotations

import json

import pytest

from langgraph_langchain.data.acquisition import (
    AcquisitionHistory,
    AcquisitionHistoryCorruptionError,
    AcquisitionService,
)
from langgraph_langchain.data.adapters.base import AdapterDescriptor, FetchRequest
from langgraph_langchain.data.adapters.public import FREDAdapter
from langgraph_langchain.data.adapters.registry import AdapterRegistry
from langgraph_langchain.runtime.context import AnalysisRuntime


def _rows(tag: str):
    return {
        "observations": [
            {"date": "2026-01-01", "value": "100.5", "series_id": "GDP"},
            {"date": "2026-02-01", "value": "101.5", "series_id": "GDP"},
            {"date": "2026-03-01", "value": f"102.{tag}", "series_id": "GDP"},
        ]
    }


def _service(tmp_path, name: str) -> AcquisitionService:
    runtime = AnalysisRuntime(
        workspace_dir=tmp_path / name, session_id=name, question="acquire data"
    )
    adapter = FREDAdapter(cache=None)
    adapter._transport = lambda url, params: _rows("1")
    adapter._credentials = lambda: {"api_key": "test-key"}
    registry = AdapterRegistry()
    registry.register(adapter)
    return AcquisitionService(
        runtime=runtime,
        registry=registry,
        history_dir=runtime.workspace_dir / ".data_acquisition",
    )


def _request(**overrides):
    payload = {"dataset_id": "fred_gdp", "start": "2026-01-01", "end": "2026-03-31"}
    payload.update(overrides)
    return FetchRequest(**payload)


def test_record_is_atomic_and_schema_versioned(tmp_path):
    history = AcquisitionHistory(tmp_path / "history")
    history.record(
        {"dataset_id": "fred_gdp", "content_hash": "a" * 64, "asset_id": "asset_1"}
    )
    path = tmp_path / "history" / "fred_gdp.json"
    assert path.is_file()
    assert not list((tmp_path / "history").glob("*.tmp"))
    entries = json.loads(path.read_text(encoding="utf-8"))
    assert entries[0]["schema_version"] == 1
    assert entries[0]["content_version"] == "v1"


def test_repeat_content_reuses_content_version_without_version_change(tmp_path):
    service = _service(tmp_path, "repeat")
    first = service.acquire(_request())
    second = service.acquire(_request())
    assert second.version_changed is False
    assert second.asset.asset_id == first.asset.asset_id
    versions = service.history.content_versions("fred_gdp")
    assert len(versions) == 1
    assert versions[0]["content_version"] == "v1"


def test_changed_content_creates_new_distinct_content_version(tmp_path):
    service = _service(tmp_path, "changed")
    first = service.acquire(_request())
    service.registry.get("fred")._transport = lambda url, params: _rows("9")
    changed = service.refresh(_request())
    assert changed.version_changed is True
    assert changed.previous_content_hash == first.fetch_result.content_hash
    entries = service.history.versions("fred_gdp")
    assert [entry["content_version"] for entry in entries] == ["v1", "v2"]
    assert len(service.history.content_versions("fred_gdp")) == 2


def test_revision_missing_but_content_changed_is_detected(tmp_path):
    service = _service(tmp_path, "revision")
    service.acquire(_request())
    service.registry.get("fred")._transport = lambda url, params: _rows("5")
    changed = service.refresh(_request())
    assert changed.version_changed is True
    entry = changed.asset
    # FetchResult carries no fabricated revision when upstream does not provide it.
    assert entry.source_metadata["revision"] is None


def test_failed_acquisition_does_not_create_history_entry(tmp_path):
    service = _service(tmp_path, "failure")
    acquired = service.acquire(_request())

    def _boom(url, params):
        raise RuntimeError("upstream down")

    service.registry.get("fred")._transport = _boom
    with pytest.raises(Exception):
        service.refresh(_request())
    assert len(service.history.versions("fred_gdp")) == 1
    assert service.history.latest("fred_gdp")["asset_id"] == acquired.asset.asset_id
    # One failure degrades the adapter; it does not fabricate a history version.
    assert service.health.status("fred") == "degraded"


def test_history_survives_process_restart(tmp_path):
    service = _service(tmp_path, "restart")
    acquired = service.acquire(_request())
    reopened = AcquisitionHistory(service.history._dir)
    assert reopened.latest("fred_gdp")["asset_id"] == acquired.asset.asset_id
    assert reopened.versions("fred_gdp") == service.history.versions("fred_gdp")


def test_corrupted_history_is_detected_not_silently_reset(tmp_path):
    history = AcquisitionHistory(tmp_path / "history")
    history.record({"dataset_id": "fred_gdp", "content_hash": "b" * 64})
    path = tmp_path / "history" / "fred_gdp.json"
    path.write_text("{not-json", encoding="utf-8")
    with pytest.raises(AcquisitionHistoryCorruptionError):
        history.history("fred_gdp")
    with pytest.raises(AcquisitionHistoryCorruptionError):
        history.record({"dataset_id": "fred_gdp", "content_hash": "c" * 64})


def test_unsafe_dataset_id_is_rejected(tmp_path):
    history = AcquisitionHistory(tmp_path / "history")
    with pytest.raises(ValueError):
        history.record({"dataset_id": "../escape", "content_hash": "d" * 64})
    with pytest.raises(ValueError):
        history.history("../../etc/passwd")


def test_queries_answer_lineage_questions(tmp_path):
    service = _service(tmp_path, "queries")
    first = service.acquire(_request())
    service.registry.get("fred")._transport = lambda url, params: _rows("7")
    second = service.refresh(_request())

    latest = service.history.latest("fred_gdp")
    assert latest["content_hash"] == second.asset.content_hash
    assert latest["fetched_at"]
    assert latest["observation_period"] == ["2026-01-01", "2026-03-01"]

    assert service.history.find_by_asset_id(first.asset.asset_id)["content_version"] == "v1"
    assert service.history.find_by_asset_id(second.asset.asset_id)["content_version"] == "v2"
    assert service.history.find_by_asset_id("asset_missing") is None

    # DataAsset → acquisition record: metadata hash matches the history entry.
    record = service.history.find_by_asset_id(second.asset.asset_id)
    assert second.asset.content_hash == record["content_hash"]

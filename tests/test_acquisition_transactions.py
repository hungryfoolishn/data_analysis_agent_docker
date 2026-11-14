"""B8.11.1: acquisition transaction consistency and history query semantics."""

from __future__ import annotations

import pytest

from langgraph_langchain.data.acquisition import (
    AcquisitionService,
    AcquisitionTransactionError,
)
from langgraph_langchain.data.adapters.base import FetchRequest
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


def _runtime(tmp_path, name):
    return AnalysisRuntime(
        workspace_dir=tmp_path / name, session_id=name, question="acquire data"
    )


def _request(**overrides):
    payload = {"dataset_id": "fred_gdp", "start": "2026-01-01", "end": "2026-03-31"}
    payload.update(overrides)
    return FetchRequest(**payload)


def _service(tmp_path, name, *, tag="1", history_dir=None):
    runtime = _runtime(tmp_path, name)
    adapter = FREDAdapter(cache=None)
    adapter._transport = lambda url, params: _rows(tag)
    adapter._credentials = lambda: {"api_key": "test-key"}
    registry = AdapterRegistry()
    registry.register(adapter)
    return AcquisitionService(
        runtime=runtime,
        registry=registry,
        history_dir=history_dir or (runtime.workspace_dir / ".data_acquisition"),
    )


def test_history_write_failure_does_not_leave_untracked_asset(tmp_path):
    service = _service(tmp_path, "txfail")
    original_record = service.history.record
    failures = {"count": 0}

    def failing_record(record):
        failures["count"] += 1
        raise RuntimeError("history disk failure")

    service.history.record = failing_record
    with pytest.raises(RuntimeError, match="history disk failure"):
        service.acquire(_request())
    service.history.record = original_record

    # The snapshot + DataAsset are committed, but no committed history event
    # exists yet and the transaction is recoverable — not silently "successful".
    txs = service.transactions.pending()
    assert len(txs) == 1
    assert txs[0]["status"] == "asset_registered"
    assert service.history.history("fred_gdp") == []
    asset_id = txs[0]["asset_id"]
    assert asset_id in service.runtime.assets

    # Saga recovery commits the missing history event idempotently.
    summary = service.recover_pending_transactions()
    assert summary == {"abandoned": 0, "completed": 1}
    entries = service.history.history("fred_gdp")
    assert len(entries) == 1
    assert entries[0]["asset_id"] == asset_id
    assert entries[0]["recovered"] is True
    assert service.transactions.pending() == []
    # The registered asset is untouched and still restorable.
    resolved = service.runtime.asset_resolver.resolve(asset_id)
    assert len(resolved.dataframe) == 3


def test_history_write_failure_preserves_existing_asset(tmp_path):
    service = _service(tmp_path, "preserve", tag="1")
    first = service.acquire(_request())

    service.registry.get("fred")._transport = lambda url, params: _rows("9")
    original_record = service.history.record

    def failing_record(record):
        raise RuntimeError("history disk failure")

    service.history.record = failing_record
    with pytest.raises(RuntimeError):
        service.refresh(_request())
    service.history.record = original_record

    # The previous version's asset + history remain intact and restorable.
    assert len(service.history.history("fred_gdp")) == 1
    resolved = service.runtime.asset_resolver.resolve(first.asset.asset_id)
    assert resolved.dataframe["value"].iloc[-1] == 102.1

    summary = service.recover_pending_transactions()
    assert summary == {"abandoned": 0, "completed": 1}
    entries = service.history.history("fred_gdp")
    assert len(entries) == 2
    assert entries[0]["content_version"] == "v1"
    assert entries[1]["content_version"] == "v2"
    assert entries[1]["version_changed"] is True


def test_history_write_failure_preserves_shared_snapshot(tmp_path):
    """Same content re-acquired (dedup): the shared asset is never deleted."""
    service = _service(tmp_path, "shared", tag="1")
    first = service.acquire(_request())
    original_record = service.history.record

    def failing_record(record):
        raise RuntimeError("history disk failure")

    service.history.record = failing_record
    # Identical content: register_fetch_result returns the existing asset.
    with pytest.raises(RuntimeError):
        service.acquire(_request())
    service.history.record = original_record

    pending = service.transactions.pending()
    assert len(pending) == 1
    assert pending[0]["asset_id"] == first.asset.asset_id
    assert pending[0]["asset_created"] is False

    service.recover_pending_transactions()
    entries = service.history.history("fred_gdp")
    assert len(entries) == 2
    assert entries[0]["asset_id"] == first.asset.asset_id
    assert entries[1]["asset_id"] == first.asset.asset_id
    # One content version, two acquisition events.
    assert len(service.history.content_versions("fred_gdp")) == 1
    resolved = service.runtime.asset_resolver.resolve(first.asset.asset_id)
    assert len(resolved.dataframe) == 3


def test_acquisition_transaction_retry_is_idempotent(tmp_path):
    service = _service(tmp_path, "retry", tag="5")
    original_record = service.history.record

    def failing_record(record):
        raise RuntimeError("history disk failure")

    service.history.record = failing_record
    with pytest.raises(RuntimeError):
        service.acquire(_request())
    service.history.record = original_record

    first_summary = service.recover_pending_transactions()
    second_summary = service.recover_pending_transactions()
    assert first_summary == {"abandoned": 0, "completed": 1}
    assert second_summary == {"abandoned": 0, "completed": 0}
    entries = service.history.history("fred_gdp")
    assert len(entries) == 1
    # A manual duplicate recovery of the same transaction adds no event.
    tx = service.transactions.get(entries[0]["transaction_id"])
    assert tx["status"] == "completed"
    service.history.record(dict(entries[0]))
    assert len(service.history.history("fred_gdp")) == 1


def test_pending_acquisition_recovery_after_process_restart(tmp_path, monkeypatch):
    history_dir = tmp_path / "shared_history"
    service = _service(tmp_path, "crash", history_dir=history_dir)
    original_record = service.history.record

    def failing_record(record):
        raise RuntimeError("history disk failure")

    service.history.record = failing_record
    with pytest.raises(RuntimeError):
        service.acquire(_request())
    service.history.record = original_record

    # A brand-new service instance (simulated process restart) performs the
    # recovery automatically at construction time.
    runtime = _runtime(tmp_path, "restart")
    restarted = AcquisitionService(
        runtime=runtime,
        registry=service.registry,
        history_dir=history_dir,
    )
    entries = restarted.history.history("fred_gdp")
    assert len(entries) == 1
    assert entries[0]["asset_id"] in service.runtime.assets
    assert restarted.transactions.pending() == []


def test_recovery_is_idempotent_and_never_rolls_back_committed_history(tmp_path):
    service = _service(tmp_path, "idem")
    outcome = service.acquire(_request())
    before = [dict(entry) for entry in service.history.history("fred_gdp")]

    summary = service.recover_pending_transactions()
    assert summary == {"abandoned": 0, "completed": 0}
    summary = service.recover_pending_transactions()
    assert summary == {"abandoned": 0, "completed": 0}
    assert service.history.history("fred_gdp") == before
    assert outcome.asset.asset_id in service.runtime.assets


def test_failed_acquisition_does_not_create_committed_version(tmp_path):
    service = _service(tmp_path, "failfetch", tag="1")
    service.acquire(_request())

    def _boom(url, params):
        raise RuntimeError("upstream down")

    service.registry.get("fred")._transport = _boom
    with pytest.raises(RuntimeError, match="upstream down"):
        service.refresh(_request())

    assert len(service.history.history("fred_gdp")) == 1
    pending = service.transactions.pending()
    assert pending == []
    abandoned = [
        tx for tx in [
            service.transactions.get(tx_id)
            for tx_id in [
                path.stem
                for path in (service.transactions._dir).glob("acqtx_*.json")
            ]
        ]
        if tx["status"] == "abandoned"
    ]
    assert len(abandoned) == 1


def test_started_transaction_without_asset_is_abandoned_on_recovery(tmp_path):
    from langgraph_langchain.data.acquisition import new_transaction_id

    service = _service(tmp_path, "started")
    tx_id = new_transaction_id()
    service.transactions.start(tx_id, {"dataset_id": "fred_gdp"})

    summary = service.recover_pending_transactions()
    assert summary == {"abandoned": 1, "completed": 0}
    assert service.transactions.get(tx_id)["status"] == "abandoned"
    assert service.history.history("fred_gdp") == []


def test_corrupted_transaction_journal_is_reported(tmp_path):
    from langgraph_langchain.data.acquisition import (
        AcquisitionHistoryCorruptionError,
        new_transaction_id,
    )

    service = _service(tmp_path, "corrupttx")
    tx_id = new_transaction_id()
    service.transactions.start(tx_id, {"dataset_id": "fred_gdp"})
    path = service.transactions._dir / f"{tx_id}.json"
    path.write_text("{corrupt", encoding="utf-8")
    with pytest.raises(AcquisitionHistoryCorruptionError):
        service.recover_pending_transactions()


# ---------------------------------------------------------------------------
# B8.11.1-B: asset id ↔ acquisition event query semantics
# ---------------------------------------------------------------------------


def test_find_by_asset_id_returns_latest_committed_event(tmp_path):
    service = _service(tmp_path, "latest", tag="1")
    outcome = service.acquire(_request())
    first_event = service.history.find_by_asset_id(outcome.asset.asset_id)
    assert first_event["transaction_id"] == outcome.transaction_id

    # Identical content re-acquisition reuses the asset and creates a new event.
    second = service.acquire(_request())
    assert second.asset.asset_id == outcome.asset.asset_id
    latest = service.history.find_by_asset_id(outcome.asset.asset_id)
    assert latest["transaction_id"] == second.transaction_id
    assert latest["fetched_at"] >= first_event["fetched_at"]


def test_find_acquisitions_by_asset_id_returns_all_events(tmp_path):
    service = _service(tmp_path, "allevts", tag="1")
    first = service.acquire(_request())
    second = service.acquire(_request())
    third = service.acquire(_request())  # still identical content
    events = service.history.find_acquisitions_by_asset_id(first.asset.asset_id)
    assert len(events) == 3
    assert [e["transaction_id"] for e in events] == [
        first.transaction_id,
        second.transaction_id,
        third.transaction_id,
    ]
    fetched = [str(e.get("fetched_at")) for e in events]
    assert fetched == sorted(fetched)


def test_same_content_hash_creates_no_new_content_version(tmp_path):
    service = _service(tmp_path, "nocv", tag="1")
    first = service.acquire(_request())
    second = service.acquire(_request())
    assert second.version_changed is False
    assert second.asset.asset_id == first.asset.asset_id
    assert len(service.history.content_versions("fred_gdp")) == 1
    assert service.history.content_versions("fred_gdp")[0]["content_version"] == "v1"


def test_same_content_hash_preserves_multiple_acquisition_events(tmp_path):
    service = _service(tmp_path, "evts", tag="1")
    service.acquire(_request())
    service.acquire(_request())
    entries = service.history.versions("fred_gdp")
    assert len(entries) == 2
    assert [e["content_version"] for e in entries] == ["v1", "v1"]


def test_pending_events_are_not_visible_as_committed_history(tmp_path):
    from langgraph_langchain.data.acquisition import new_transaction_id

    service = _service(tmp_path, "invisible", tag="1")
    outcome = service.acquire(_request())
    before = service.history.history("fred_gdp")

    # Pending transactions live in the journal, never in the committed history.
    pending_id = new_transaction_id()
    service.transactions.start(pending_id, {"dataset_id": "fred_gdp"})
    service.transactions.update(
        pending_id, status="asset_registered", asset_id="asset_fake"
    )
    assert service.history.history("fred_gdp") == before
    assert service.history.latest("fred_gdp")["asset_id"] == outcome.asset.asset_id
    assert service.history.find_acquisitions_by_asset_id("asset_fake") == []
    assert service.history.find_by_asset_id("asset_fake") is None
    assert service.history.content_versions("fred_gdp")[0]["asset_id"] == (
        outcome.asset.asset_id
    )

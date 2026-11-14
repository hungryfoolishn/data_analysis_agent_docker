"""B8.10: acquisition operations governance tests (offline, transport-injected)."""

from __future__ import annotations

import time

import pandas as pd
import pytest

from langgraph_langchain.data.acquisition import (
    AcquisitionService,
    AdapterHealthTracker,
)
from langgraph_langchain.data.adapters.base import (
    AdapterAuthError,
    AdapterDescriptor,
    AdapterRateLimitError,
    AdapterRequestError,
    AdapterTemporaryError,
    FetchRequest,
    HttpJsonAdapter,
)
from langgraph_langchain.data.adapters.public import FREDAdapter
from langgraph_langchain.data.adapters.registry import AdapterRegistry
from langgraph_langchain.runtime.context import AnalysisRuntime


def _rows(tag: str = "1"):
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


def _adapter(transport):
    adapter = FREDAdapter(cache=None, max_retries=2, retry_backoff_seconds=0.01)
    adapter._transport = transport
    adapter._credentials = lambda: {"api_key": "test-key"}
    return adapter


def _service(tmp_path, runtime, adapter):
    registry = AdapterRegistry()
    registry.register(adapter)
    return AcquisitionService(
        runtime=runtime,
        registry=registry,
        history_dir=runtime.workspace_dir / ".data_acquisition",
    )


def test_quality_and_health_are_independent(tmp_path):
    """A data gap degrades the QualityReport, never the adapter health."""

    class _GapAdapter(FREDAdapter):
        descriptor = AdapterDescriptor(
            adapter_id="fred_gap",
            version="1.0.0",
            source_type="fred",
            description="gap adapter",
        )

    runtime = _runtime(tmp_path, "gap")
    adapter = _GapAdapter(cache=None)
    adapter._transport = lambda url, params: {
        "observations": [
            {"date": "2026-01-01", "value": "100.5", "series_id": "GDP"},
            {"date": "2026-03-01", "value": "102.5", "series_id": "GDP"},
        ]
    }
    adapter._credentials = lambda: {"api_key": "test-key"}
    service = _service(tmp_path, runtime, adapter)
    # fred_cpi is monthly; a missing February must be a data-quality warning.
    outcome = service.acquire(_request(dataset_id="fred_cpi"))
    assert outcome.fetch_result.quality_report.status == "warning"
    assert any("observation gaps" in w for w in outcome.fetch_result.warnings)
    assert service.health.status("fred_gap") == "healthy"


def test_health_state_machine_transitions():
    health = AdapterHealthTracker(failure_threshold=2)
    assert health.status("fred") == "unknown"
    health.record_failure("fred", "timeout", error_type="transient")
    assert health.status("fred") == "degraded"
    health.record_failure("fred", "5xx", error_type="transient")
    assert health.status("fred") == "failing"
    health.record_success("fred")
    assert health.status("fred") == "healthy"
    health.record_failure("fred", "bad key", error_type="auth")
    assert health.status("fred") == "failing"
    health.record_success("fred")
    assert health.status("fred") == "healthy"


def test_auth_failure_is_not_retried(tmp_path):
    calls = {"count": 0}

    def transport(url, params):
        calls["count"] += 1
        raise AdapterAuthError("Upstream rejected credentials")

    runtime = _runtime(tmp_path, "auth")
    service = _service(tmp_path, runtime, _adapter(transport))
    with pytest.raises(AdapterAuthError):
        service.acquire(_request())
    assert calls["count"] == 1
    assert service.health.status("fred") == "failing"


def test_rate_limit_error_is_retried_and_honors_retry_after(tmp_path):
    calls = {"count": 0}

    def transport(url, params):
        calls["count"] += 1
        if calls["count"] <= 2:
            raise AdapterRateLimitError("429", retry_after=0.02)
        return _rows()

    runtime = _runtime(tmp_path, "rl")
    service = _service(tmp_path, runtime, _adapter(transport))
    outcome = service.acquire(_request())
    assert calls["count"] == 3
    assert outcome.asset is not None


def test_retry_after_wait_is_bounded(tmp_path):
    def transport(url, params):
        raise AdapterRateLimitError("429", retry_after=600.0)

    adapter = _adapter(transport)
    adapter.retry_after_cap_seconds = 0.05
    start = time.monotonic()
    with pytest.raises(AdapterTemporaryError):
        adapter.fetch(_request())
    elapsed = time.monotonic() - start
    assert elapsed < 2.0


def test_refresh_bypasses_cache_but_still_respects_rate_limit(tmp_path):
    class _SlowAdapter(FREDAdapter):
        descriptor = AdapterDescriptor(
            adapter_id="fred_slow",
            version="1.0.0",
            source_type="fred",
            description="slow",
            rate_limit_seconds=0.12,
        )

    calls = {"count": 0}

    def transport(url, params):
        calls["count"] += 1
        return _rows()

    adapter = _SlowAdapter(cache=None)
    adapter._transport = transport
    adapter._credentials = lambda: {"api_key": "test-key"}

    first = adapter.fetch(_request())
    start = time.monotonic()
    second = adapter.fetch(_request())
    cached_elapsed = time.monotonic() - start
    # Cache hit: no upstream request, no rate-limit sleep.
    assert second.content_hash == first.content_hash
    assert calls["count"] == 1

    start = time.monotonic()
    adapter.fetch(_request(), refresh=True)
    refreshed_elapsed = time.monotonic() - start
    # refresh bypasses the cache: it issues a real upstream request and waits
    # for at least the remainder of the declared interval.
    assert calls["count"] == 2
    assert refreshed_elapsed >= max(0.0, 0.12 - cached_elapsed) - 0.05


def test_request_metadata_and_cache_keys_never_contain_credentials(tmp_path):
    runtime = _runtime(tmp_path, "creds")
    service = _service(tmp_path, runtime, _adapter(lambda url, params: _rows()))
    outcome = service.acquire(_request())
    request_metadata = outcome.asset.source_metadata["request_parameters"]
    assert "api_key" not in json_keys(request_metadata)
    # The cache key is a hash over the normalized request only.
    key = _request().cache_key("fred")
    assert key and "test-key" not in key


def json_keys(mapping):
    return list(mapping.keys())


def test_http_get_classifies_errors_without_leaking_query_params(monkeypatch):
    class _FakeResponse:
        def __init__(self, status, headers=None):
            self.status_code = status
            self.headers = headers or {}

        def json(self):
            return {}

    class _FakeRequests:
        @staticmethod
        def get(url, params=None, timeout=None):
            assert params.get("api_key") == "secret-key"
            return _FakeResponse(401)

    import sys

    fake = _FakeRequests()
    monkeypatch.setitem(sys.modules, "requests", fake)
    adapter = FREDAdapter(cache=None)
    adapter._credentials = lambda: {"api_key": "secret-key"}
    with pytest.raises(AdapterAuthError) as excinfo:
        adapter._http_get("https://api.stlouisfed.org/fred/series/observations", {
            "series_id": "GDP",
            "api_key": "secret-key",
        })
    message = str(excinfo.value)
    assert "secret-key" not in message
    assert "?" not in message


def test_rate_limit_is_thread_safe_under_concurrent_fetches():
    class _SlowAdapter(FREDAdapter):
        descriptor = AdapterDescriptor(
            adapter_id="fred_conc",
            version="1.0.0",
            source_type="fred",
            description="concurrent",
            rate_limit_seconds=0.1,
        )

    adapter = _SlowAdapter(cache=None, max_retries=0)
    adapter._transport = lambda url, params: _rows()
    adapter._credentials = lambda: {"api_key": "test-key"}
    import threading

    start = time.monotonic()
    threads = [
        threading.Thread(
            target=adapter.fetch,
            args=(FetchRequest(dataset_id=f"fred_gdp", parameters={"i": i}),),
        )
        for i in range(3)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    elapsed = time.monotonic() - start
    # Three upstream requests must be separated by the declared interval.
    assert elapsed >= 0.2


def test_snapshot_write_failure_rolls_back_registration(tmp_path):
    runtime = _runtime(tmp_path, "rollback")
    service = _service(tmp_path, runtime, _adapter(lambda url, params: _rows()))
    outcome = service.acquire(_request())
    asset_count = len(runtime.assets)
    linked = list(runtime.task.input_asset_ids)

    from langgraph_langchain.data.snapshots import SnapshotWriteError

    def _boom(*args, **kwargs):
        raise SnapshotWriteError("disk full")

    original_save = runtime.snapshot_store.save
    runtime.snapshot_store.save = _boom
    try:
        service.registry.get("fred")._transport = lambda url, params: _rows("9")
        with pytest.raises(SnapshotWriteError):
            service.refresh(_request())
    finally:
        runtime.snapshot_store.save = original_save

    assert len(runtime.assets) == asset_count
    assert runtime.task.input_asset_ids == linked
    # The previous version is intact and restorable.
    resolved = runtime.asset_resolver.resolve(outcome.asset.asset_id)
    assert len(resolved.dataframe) == 3


def test_quality_report_keeps_evidence_and_does_not_upgrade_warnings(tmp_path):
    adapter = _adapter(lambda url, params: _rows())
    frame = pd.DataFrame(
        {
            "date": pd.to_datetime(
                ["2026-01-01", "2026-01-01", "2026-03-01"]
            ),
            "value": [1.0, 1.0, 3.0],
            "series_id": ["GDP", "GDP", "GDP"],
        }
    )
    spec = FREDAdapter.datasets["fred_gdp"]
    report = adapter.validate(frame, spec)
    assert report.status == "warning"
    assert report.issues == ()
    assert any("Duplicate time values" in warning for warning in report.warnings)

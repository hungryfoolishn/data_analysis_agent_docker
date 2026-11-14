"""Data Acquisition Platform bridge (B8).

Connects the Adapter layer to the analysis runtime without letting adapters
call Runtime directly:

    Dataset Request → AdapterRegistry → Fetch → Normalize → Validate
      → FetchResult → AcquisitionService.acquire() → DataAsset
      → SnapshotStore persistence → Runtime V2 consumption

Also provides the dataset version history (update records) and adapter
health tracking required by toDo.md Phase 2A/2C.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from langgraph_langchain.data.adapters.base import FetchRequest, FetchResult
from langgraph_langchain.data.adapters.registry import AdapterRegistry


class AcquisitionHistory:
    """Persistent per-dataset fetch/version records.

    Layout: ``<history_dir>/<dataset_id>.json`` holding an ordered list of
    version records — the update log required by Phase 2A ("数据版本和更新记录").
    """

    def __init__(self, history_dir: Path) -> None:
        self._dir = Path(history_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, dataset_id: str) -> Path:
        return self._dir / f"{dataset_id}.json"

    def record(self, record: dict[str, Any]) -> None:
        dataset_id = str(record.get("dataset_id", "unknown"))
        path = self._path(dataset_id)
        entries: list[dict[str, Any]] = []
        if path.is_file():
            try:
                entries = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                entries = []
        entries.append(record)
        path.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")

    def history(self, dataset_id: str) -> list[dict[str, Any]]:
        path = self._path(dataset_id)
        if not path.is_file():
            return []
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return []

    def latest(self, dataset_id: str) -> dict[str, Any] | None:
        entries = self.history(dataset_id)
        return entries[-1] if entries else None


class AdapterHealthTracker:
    """In-process adapter health status (Phase 2C: adapter health state)."""

    def __init__(self, *, failure_threshold: int = 2) -> None:
        self._failure_threshold = failure_threshold
        self._stats: dict[str, dict[str, Any]] = {}

    def record_success(self, adapter_id: str) -> None:
        stats = self._stats.setdefault(
            adapter_id,
            {"consecutive_failures": 0, "last_status": None, "last_error": None},
        )
        stats["consecutive_failures"] = 0
        stats["last_status"] = "healthy"

    def record_failure(self, adapter_id: str, error: str) -> None:
        stats = self._stats.setdefault(
            adapter_id,
            {"consecutive_failures": 0, "last_status": None, "last_error": None},
        )
        stats["consecutive_failures"] = int(stats["consecutive_failures"]) + 1
        stats["last_status"] = "failing"
        stats["last_error"] = error

    def status(self, adapter_id: str) -> str:
        stats = self._stats.get(adapter_id)
        if not stats:
            return "unknown"
        if stats["consecutive_failures"] >= self._failure_threshold:
            return "failing"
        if stats["last_status"] == "healthy":
            return "healthy"
        return "degraded"


class AcquisitionOutcome:
    """Result of one acquisition cycle: fetch + registration + bookkeeping."""

    def __init__(
        self,
        *,
        asset,
        fetch_result: FetchResult,
        version_changed: bool,
        previous_content_hash: str | None,
        asset_created: bool,
    ) -> None:
        self.asset = asset
        self.fetch_result = fetch_result
        self.version_changed = version_changed
        self.previous_content_hash = previous_content_hash
        self.asset_created = asset_created


class AcquisitionService:
    """Fetch a dataset through the registry and register it with the runtime."""

    def __init__(
        self,
        *,
        runtime,
        registry: AdapterRegistry,
        history_dir: Path,
        health: AdapterHealthTracker | None = None,
    ) -> None:
        self.runtime = runtime
        self.registry = registry
        self.history = AcquisitionHistory(history_dir)
        self.health = health or AdapterHealthTracker()

    def acquire(
        self,
        request: FetchRequest,
        *,
        refresh: bool = False,
    ) -> AcquisitionOutcome:
        previous = self.history.latest(request.dataset_id)
        previous_hash = previous.get("content_hash") if previous else None
        try:
            result = self.registry.fetch(request, refresh=refresh)
        except Exception as exc:
            self.health.record_failure(
                self._adapter_for_dataset(request.dataset_id), str(exc)
            )
            raise
        self.health.record_success(result.adapter_id)
        asset = self.runtime.register_fetch_result(result)
        # register_fetch_result dedups identical (dataset_id, content_hash);
        # a *new* asset id means the dataset content changed (version update).
        asset_created = all(
            entry.get("asset_id") != asset.asset_id
            for entry in self.history.history(request.dataset_id)
        )
        version_changed = previous_hash is not None and previous_hash != result.content_hash
        self.history.record(
            {
                "dataset_id": result.dataset_id,
                "adapter_id": result.adapter_id,
                "adapter_version": result.adapter_version,
                "content_hash": result.content_hash,
                "fetched_at": (result.fetch_metadata or {}).get("fetched_at"),
                "observation_period": (
                    list(result.observation_period) if result.observation_period else None
                ),
                "release_date": result.release_date,
                "revision": result.revision,
                "quality_status": result.quality_report.status,
                "asset_id": asset.asset_id,
                "asset_created": asset_created,
                "version_changed": version_changed,
            }
        )
        return AcquisitionOutcome(
            asset=asset,
            fetch_result=result,
            version_changed=version_changed,
            previous_content_hash=previous_hash,
            asset_created=asset_created,
        )

    def refresh(self, request: FetchRequest) -> AcquisitionOutcome:
        """Manual refresh: bypass caches and re-acquire the dataset."""
        return self.acquire(request, refresh=True)

    def _adapter_for_dataset(self, dataset_id: str) -> str:
        for entry in self.registry.catalog():
            if entry.dataset_id == dataset_id:
                return entry.adapter_id
        return "unknown"

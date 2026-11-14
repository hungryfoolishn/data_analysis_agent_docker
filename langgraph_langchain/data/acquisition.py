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
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Any, Optional

from langgraph_langchain.data.adapters.base import FetchRequest, FetchResult
from langgraph_langchain.data.adapters.registry import AdapterRegistry


class AcquisitionHistoryError(RuntimeError):
    """Base error for acquisition history persistence."""


class AcquisitionHistoryCorruptionError(AcquisitionHistoryError):
    """Raised when a history file exists but is not valid version history."""


_DATASET_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")


class AcquisitionHistory:
    """Persistent per-dataset fetch/version records.

    Layout: ``<history_dir>/<dataset_id>.json`` holding an ordered list of
    version records — the update log required by Phase 2A ("数据版本和更新记录").

    B8.9 guarantees:

    - writes are atomic (temp file + ``os.replace``), so a crash never leaves a
      partially written history file;
    - corrupted files are detected and reported instead of being silently
      discarded (which would erase version history);
    - in-process concurrent writes are serialized;
    - records carry ``schema_version`` and a deterministic ``content_version``
      label derived from the distinct content-hash sequence;
    - dataset ids are validated before being used as filenames.
    """

    SCHEMA_VERSION = 1

    def __init__(self, history_dir: Path) -> None:
        self._dir = Path(history_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _path(self, dataset_id: str) -> Path:
        if not _DATASET_ID_PATTERN.fullmatch(str(dataset_id)):
            raise ValueError(f"Unsafe dataset_id for history file: {dataset_id!r}")
        return self._dir / f"{dataset_id}.json"

    def _load(self, path: Path) -> list[dict[str, Any]]:
        if not path.is_file():
            return []
        try:
            entries = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise AcquisitionHistoryCorruptionError(
                f"Corrupted acquisition history file: {path.name}: {exc}"
            ) from exc
        if not isinstance(entries, list) or any(
            not isinstance(entry, dict) for entry in entries
        ):
            raise AcquisitionHistoryCorruptionError(
                f"Corrupted acquisition history file: {path.name}"
            )
        return entries

    def _content_version(self, entries: list[dict[str, Any]], content_hash: str) -> str:
        """Deterministic label for the distinct-content sequence of a dataset."""
        seen: list[str] = []
        for entry in entries:
            digest = entry.get("content_hash")
            if digest and digest not in seen:
                seen.append(digest)
        index = seen.index(content_hash) + 1 if content_hash in seen else len(seen) + 1
        return f"v{index}"

    def record(self, record: dict[str, Any]) -> None:
        dataset_id = str(record.get("dataset_id", "unknown"))
        path = self._path(dataset_id)
        with self._lock:
            entries = self._load(path)
            content_hash = record.get("content_hash")
            record = dict(record)
            record.setdefault("schema_version", self.SCHEMA_VERSION)
            record.setdefault("status", "acquired")
            if content_hash:
                record["content_version"] = self._content_version(entries, content_hash)
            entries.append(record)
            payload = json.dumps(entries, ensure_ascii=False, indent=2)
            fd, tmp_name = tempfile.mkstemp(
                prefix=f".{path.name}.", suffix=".tmp", dir=str(self._dir)
            )
            tmp_path = Path(tmp_name)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(tmp_path, path)
            except BaseException:
                tmp_path.unlink(missing_ok=True)
                raise

    def history(self, dataset_id: str) -> list[dict[str, Any]]:
        path = self._path(dataset_id)
        return self._load(path)

    def latest(self, dataset_id: str) -> dict[str, Any] | None:
        entries = self.history(dataset_id)
        return entries[-1] if entries else None

    # --- read-only query helpers (B8.9); never trigger acquisition ---
    def versions(self, dataset_id: str) -> list[dict[str, Any]]:
        """All acquisition records for a dataset, oldest first."""
        return self.history(dataset_id)

    def content_versions(self, dataset_id: str) -> list[dict[str, Any]]:
        """Distinct content versions observed for a dataset (oldest first)."""
        seen: dict[str, dict[str, Any]] = {}
        for entry in self.history(dataset_id):
            digest = entry.get("content_hash")
            if digest and digest not in seen:
                seen[digest] = entry
        return list(seen.values())

    def find_by_asset_id(
        self, asset_id: str, *, dataset_id: str | None = None
    ) -> dict[str, Any] | None:
        """Map a DataAsset id back to its acquisition record (read-only)."""
        dataset_ids = [dataset_id] if dataset_id else sorted(
            path.stem for path in self._dir.glob("*.json")
        )
        for candidate in dataset_ids:
            for entry in self.history(candidate):
                if entry.get("asset_id") == asset_id:
                    return entry
        return None


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
            # Acquisition failures are recorded as adapter health state only;
            # they never create a version-history entry, so a failed fetch can
            # not be mistaken for a content version or overwrite the previous
            # version (B8.9).
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
                "request_parameters": dict(
                    (result.source_metadata or {}).get("request", {})
                ),
                "source_url": (result.dataset_metadata or {}).get("source_url"),
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

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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from langgraph_langchain.data.adapters.base import FetchRequest, FetchResult
from langgraph_langchain.data.adapters.registry import AdapterRegistry


class AcquisitionHistoryError(RuntimeError):
    """Base error for acquisition history persistence."""


class AcquisitionHistoryCorruptionError(AcquisitionHistoryError):
    """Raised when a history file exists but is not valid version history."""


class AcquisitionTransactionError(AcquisitionHistoryError):
    """Raised when acquisition transaction recovery cannot complete safely."""


_DATASET_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")
_TRANSACTION_ID_PATTERN = re.compile(r"^acqtx_[0-9a-f]{32}$")


def new_transaction_id() -> str:
    """Unique, filesystem-safe acquisition transaction id (B8.11.1)."""
    from uuid import uuid4

    return f"acqtx_{uuid4().hex}"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
            # B8.11.1: recording the same transaction twice is a no-op so the
            # saga recovery can safely retry a committed history event.
            transaction_id = record.get("transaction_id")
            if transaction_id and any(
                entry.get("transaction_id") == transaction_id for entry in entries
            ):
                return
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
    """In-process adapter health status (Phase 2C / B8.10).

    Health describes source reachability only — it is independent of data
    quality. State machine:

    - a single transient failure → ``degraded``;
    - ``failure_threshold`` consecutive failures → ``failing``;
    - an authentication failure → ``failing`` immediately (the source actively
      rejected the request; retrying cannot help);
    - any successful fetch resets the adapter to ``healthy``.

    Updates are serialized for in-process concurrency; state is intentionally
    not persisted across processes (health describes "now").
    """

    def __init__(self, *, failure_threshold: int = 2) -> None:
        self._failure_threshold = max(1, int(failure_threshold))
        self._stats: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def record_success(self, adapter_id: str) -> None:
        with self._lock:
            stats = self._stats.setdefault(
                adapter_id,
                {
                    "consecutive_failures": 0,
                    "last_status": None,
                    "last_error": None,
                    "last_error_type": None,
                    "auth_failed": False,
                },
            )
            stats["consecutive_failures"] = 0
            stats["last_status"] = "healthy"
            stats["last_error"] = None
            stats["last_error_type"] = None
            stats["auth_failed"] = False

    def record_failure(
        self, adapter_id: str, error: str, *, error_type: str | None = None
    ) -> None:
        """Record one upstream failure (never a data-quality problem)."""
        with self._lock:
            stats = self._stats.setdefault(
                adapter_id,
                {
                    "consecutive_failures": 0,
                    "last_status": None,
                    "last_error": None,
                    "last_error_type": None,
                    "auth_failed": False,
                },
            )
            stats["consecutive_failures"] = int(stats["consecutive_failures"]) + 1
            stats["last_status"] = "failing"
            stats["last_error"] = str(error)
            stats["last_error_type"] = error_type
            if error_type == "auth":
                stats["auth_failed"] = True

    def status(self, adapter_id: str) -> str:
        with self._lock:
            stats = self._stats.get(adapter_id)
            if not stats:
                return "unknown"
            if stats.get("auth_failed"):
                return "failing"
            if stats["consecutive_failures"] >= self._failure_threshold:
                return "failing"
            if stats["last_status"] == "healthy":
                return "healthy"
            return "degraded"


class AcquisitionTransactionLog:
    """Persistent acquisition saga journal (B8.11.1).

    One journal file per acquisition transaction under
    ``<history_dir>/transactions/``.  States:

    - ``started``          — fetch phase; no snapshot/asset exists yet.
    - ``asset_registered`` — snapshot + DataAsset are committed in the runtime
      and may already be shared/referenced; the committed history event is
      still pending.  This is the recoverable state.
    - ``completed``        — history event committed and journal finalized
      (terminal; history entries are the authoritative record).
    - ``abandoned``        — failure or crash **before** asset registration
      (terminal; no runtime resources were created, nothing to compensate).

    Writes are atomic and corruption-checked like AcquisitionHistory.  The
    journal never blocks normal history queries: pending/abandoned
    transactions are invisible to ``AcquisitionHistory`` read APIs.
    """

    STATES = ("started", "asset_registered", "completed", "abandoned")
    ACTIVE_STATES = ("started", "asset_registered")

    def __init__(self, journal_dir: Path) -> None:
        self._dir = Path(journal_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _path(self, transaction_id: str) -> Path:
        if not _TRANSACTION_ID_PATTERN.fullmatch(str(transaction_id)):
            raise ValueError(f"Unsafe transaction id: {transaction_id!r}")
        return self._dir / f"{transaction_id}.json"

    def _load(self, path: Path) -> dict[str, Any]:
        if not path.is_file():
            raise FileNotFoundError(f"Unknown transaction: {path.name}")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise AcquisitionHistoryCorruptionError(
                f"Corrupted transaction journal file: {path.name}: {exc}"
            ) from exc
        if not isinstance(payload, dict) or "transaction_id" not in payload:
            raise AcquisitionHistoryCorruptionError(
                f"Corrupted transaction journal file: {path.name}"
            )
        return payload

    def _save(self, payload: dict[str, Any]) -> None:
        path = self._path(payload["transaction_id"])
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=str(self._dir)
        )
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False, indent=2))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, path)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise

    def start(self, transaction_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            path = self._path(transaction_id)
            if path.is_file():
                raise AcquisitionTransactionError(
                    f"Transaction already started: {transaction_id}"
                )
            record = {
                "transaction_id": transaction_id,
                "status": "started",
                "started_at": _utc_now_iso(),
                **payload,
            }
            self._save(record)
            return record

    def update(
        self, transaction_id: str, *, status: str | None = None, **fields: Any
    ) -> dict[str, Any]:
        with self._lock:
            record = self._load(self._path(transaction_id))
            if status is not None:
                if status not in self.STATES:
                    raise ValueError(f"Unknown transaction status: {status}")
                record["status"] = status
            record.update(fields)
            record["updated_at"] = _utc_now_iso()
            self._save(record)
            return record

    def abandon(self, transaction_id: str, *, error: str) -> dict[str, Any]:
        with self._lock:
            record = self._load(self._path(transaction_id))
            if record["status"] in self.ACTIVE_STATES:
                record["status"] = "abandoned"
                record["abandoned_at"] = _utc_now_iso()
                record["error"] = str(error)
                self._save(record)
            return record

    def get(self, transaction_id: str) -> dict[str, Any] | None:
        path = self._path(transaction_id)
        if not path.is_file():
            return None
        return self._load(path)

    def pending(self) -> list[dict[str, Any]]:
        """Active (started / asset_registered) transactions, oldest first."""
        records = [
            self._load(path)
            for path in sorted(self._dir.glob("acqtx_*.json"))
        ]
        active = [r for r in records if r.get("status") in self.ACTIVE_STATES]
        active.sort(key=lambda r: str(r.get("started_at") or ""))
        return active


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
        transaction_id: str | None = None,
    ) -> None:
        self.asset = asset
        self.fetch_result = fetch_result
        self.version_changed = version_changed
        self.previous_content_hash = previous_content_hash
        self.asset_created = asset_created
        self.transaction_id = transaction_id


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
        self.transactions = AcquisitionTransactionLog(history_dir / "transactions")
        self.health = health or AdapterHealthTracker()
        # B8.11.1: crash recovery runs at service startup; it is idempotent and
        # never rolls back committed history or deletes shared resources.
        self.recover_pending_transactions()

    def acquire(
        self,
        request: FetchRequest,
        *,
        refresh: bool = False,
    ) -> AcquisitionOutcome:
        transaction_id = new_transaction_id()
        self.transactions.start(
            transaction_id,
            {
                "dataset_id": request.dataset_id,
                "request_parameters": dict(request.normalized().get("parameters") or {}),
            },
        )
        previous = self.history.latest(request.dataset_id)
        previous_hash = previous.get("content_hash") if previous else None
        try:
            result = self.registry.fetch(request, refresh=refresh)
        except Exception as exc:
            # Acquisition failures are recorded as adapter health state only;
            # they never create a version-history entry, so a failed fetch can
            # not be mistaken for a content version or overwrite the previous
            # version (B8.9). B8.10 classifies the failure for the health state.
            self.transactions.abandon(transaction_id, error=str(exc))
            self.health.record_failure(
                self._adapter_for_dataset(request.dataset_id),
                str(exc),
                error_type=self._health_error_type(exc),
            )
            raise
        self.health.record_success(result.adapter_id)
        self.transactions.update(
            transaction_id,
            adapter_id=result.adapter_id,
            adapter_version=result.adapter_version,
            content_hash=result.content_hash,
            fetched_at=(result.fetch_metadata or {}).get("fetched_at"),
            observation_period=(
                list(result.observation_period) if result.observation_period else None
            ),
            release_date=result.release_date,
            revision=result.revision,
            quality_status=result.quality_report.status,
        )
        try:
            asset = self.runtime.register_fetch_result(result)
        except Exception as exc:
            # register_fetch_result already rolled back its own snapshot/asset
            # state; nothing shared exists at this point, so the transaction is
            # safely abandoned (B8.11.1 scenario B/C).
            self.transactions.abandon(transaction_id, error=str(exc))
            raise
        # register_fetch_result dedups identical (dataset_id, content_hash);
        # a *new* asset id means the dataset content changed (version update).
        asset_created = all(
            entry.get("asset_id") != asset.asset_id
            for entry in self.history.history(request.dataset_id)
        )
        version_changed = previous_hash is not None and previous_hash != result.content_hash
        self.transactions.update(
            transaction_id,
            status="asset_registered",
            asset_id=asset.asset_id,
            asset_created=asset_created,
            version_changed=version_changed,
        )
        # Scenario D (B8.11.1): if this write fails, the transaction stays in
        # ``asset_registered`` — the committed snapshot/asset is never deleted
        # here (it may already be shared). ``recover_pending_transactions``
        # commits the missing history event idempotently.
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
                "transaction_id": transaction_id,
            }
        )
        self.transactions.update(transaction_id, status="completed")
        return AcquisitionOutcome(
            asset=asset,
            fetch_result=result,
            version_changed=version_changed,
            previous_content_hash=previous_hash,
            asset_created=asset_created,
            transaction_id=transaction_id,
        )

    def refresh(self, request: FetchRequest) -> AcquisitionOutcome:
        """Manual refresh: bypass caches and re-acquire the dataset."""
        return self.acquire(request, refresh=True)

    def recover_pending_transactions(self) -> dict[str, int]:
        """Saga recovery for interrupted acquisitions (B8.11.1, idempotent).

        - ``started``: crashed before asset registration — no runtime resource
          exists, the transaction is abandoned.
        - ``asset_registered``: snapshot/asset are already committed and may be
          shared; the compensation is the **idempotent** commit of the missing
          history event, then the journal is finalized.  Committed history is
          never rolled back and shared assets are never deleted.
        """
        summary = {"abandoned": 0, "completed": 0}
        failures: list[str] = []
        for tx in self.transactions.pending():
            tx_id = tx["transaction_id"]
            status = tx["status"]
            try:
                if status == "started":
                    self.transactions.abandon(
                        tx_id,
                        error="recovered: interrupted before asset registration",
                    )
                    summary["abandoned"] += 1
                elif status == "asset_registered":
                    self._commit_history_for_transaction(tx)
                    self.transactions.update(tx_id, status="completed", recovered=True)
                    summary["completed"] += 1
            except Exception as exc:  # noqa: BLE001 — surfaced, never swallowed
                failures.append(f"{tx_id}: {exc}")
        if failures:
            raise AcquisitionTransactionError(
                "Acquisition transaction recovery failed: " + "; ".join(failures)
            )
        return summary

    def _commit_history_for_transaction(self, tx: dict[str, Any]) -> None:
        """Commit the history event for an asset_registered transaction."""
        self.history.record(
            {
                "dataset_id": tx.get("dataset_id"),
                "adapter_id": tx.get("adapter_id"),
                "adapter_version": tx.get("adapter_version"),
                "content_hash": tx.get("content_hash"),
                "fetched_at": tx.get("fetched_at"),
                "observation_period": tx.get("observation_period"),
                "release_date": tx.get("release_date"),
                "revision": tx.get("revision"),
                "quality_status": tx.get("quality_status"),
                "asset_id": tx.get("asset_id"),
                "asset_created": tx.get("asset_created"),
                "version_changed": tx.get("version_changed"),
                "request_parameters": tx.get("request_parameters"),
                "source_url": tx.get("source_url"),
                "transaction_id": tx["transaction_id"],
                "recovered": True,
            }
        )

    @staticmethod
    def _health_error_type(exc: Exception) -> str | None:
        from langgraph_langchain.data.adapters.base import (
            AdapterAuthError,
            AdapterRateLimitError,
        )

        if isinstance(exc, AdapterAuthError):
            return "auth"
        if isinstance(exc, AdapterRateLimitError):
            return "rate_limit"
        return None

    def _adapter_for_dataset(self, dataset_id: str) -> str:
        for entry in self.registry.catalog():
            if entry.dataset_id == dataset_id:
                return entry.adapter_id
        return "unknown"

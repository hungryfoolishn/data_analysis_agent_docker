"""Content-addressed, atomic dataframe cache for deterministic data operations."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

import pandas as pd
from pydantic import BaseModel, Field


class CacheEntry(BaseModel):
    key: str
    created_at: float
    expires_at: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


def cache_key(
    *,
    source_hash: str,
    adapter_version: str,
    selected_fields: list[str] | None = None,
    filters: dict[str, Any] | None = None,
    parameters: dict[str, Any] | None = None,
    code_version: str = "unknown",
) -> str:
    payload = {
        "source_hash": source_hash,
        "adapter_version": adapter_version,
        "selected_fields": selected_fields or [],
        "filters": filters or {},
        "parameters": parameters or {},
        "code_version": code_version,
    }
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


class DataCache:
    def __init__(self, root: Path, *, max_bytes: int = 512 * 1024 * 1024):
        self.root = root
        self.max_bytes = max_bytes
        self.root.mkdir(parents=True, exist_ok=True)

    def _data_path(self, key: str) -> Path:
        return self.root / f"{key}.pkl"

    def _metadata_path(self, key: str) -> Path:
        return self.root / f"{key}.json"

    def get(self, key: str) -> pd.DataFrame | None:
        metadata_path, data_path = self._metadata_path(key), self._data_path(key)
        if not metadata_path.exists() or not data_path.exists():
            return None
        try:
            entry = CacheEntry.model_validate_json(metadata_path.read_text(encoding="utf-8"))
            if entry.expires_at is not None and entry.expires_at <= time.time():
                self.remove(key)
                return None
            return pd.read_pickle(data_path)
        except Exception:
            self.remove(key)
            return None

    def put(self, key: str, dataframe: pd.DataFrame, *, ttl_seconds: int | None = None, metadata: dict[str, Any] | None = None) -> CacheEntry:
        self.evict_to_limit(required_bytes=int(dataframe.memory_usage(deep=True).sum()))
        fd, raw_temp = tempfile.mkstemp(dir=self.root, prefix=f".{key}.", suffix=".tmp")
        os.close(fd)
        temp = Path(raw_temp)
        try:
            dataframe.to_pickle(temp)
            os.replace(temp, self._data_path(key))
            entry = CacheEntry(
                key=key,
                created_at=time.time(),
                expires_at=time.time() + ttl_seconds if ttl_seconds else None,
                metadata=metadata or {},
            )
            metadata_temp = self._metadata_path(key).with_suffix(".tmp")
            metadata_temp.write_text(entry.model_dump_json(indent=2), encoding="utf-8")
            os.replace(metadata_temp, self._metadata_path(key))
            return entry
        finally:
            temp.unlink(missing_ok=True)

    def remove(self, key: str) -> None:
        self._data_path(key).unlink(missing_ok=True)
        self._metadata_path(key).unlink(missing_ok=True)

    def evict_to_limit(self, *, required_bytes: int = 0) -> None:
        entries: list[tuple[float, Path, Path]] = []
        total = 0
        for metadata_path in self.root.glob("*.json"):
            data_path = metadata_path.with_suffix(".pkl")
            if not data_path.exists():
                metadata_path.unlink(missing_ok=True)
                continue
            total += data_path.stat().st_size
            try:
                created = CacheEntry.model_validate_json(metadata_path.read_text(encoding="utf-8")).created_at
            except Exception:
                created = 0
            entries.append((created, metadata_path, data_path))
        for _, metadata_path, data_path in sorted(entries):
            if total + required_bytes <= self.max_bytes:
                break
            size = data_path.stat().st_size
            metadata_path.unlink(missing_ok=True)
            data_path.unlink(missing_ok=True)
            total -= size

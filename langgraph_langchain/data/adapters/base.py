"""External data source adapter contracts."""

from __future__ import annotations

import hashlib
import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal, Mapping, Sequence

import pandas as pd
from pydantic import BaseModel, Field


class AdapterError(RuntimeError):
    """Base error for external adapter failures."""


class AdapterRequestError(AdapterError, ValueError):
    """Raised when adapter request parameters are invalid."""


class AdapterTemporaryError(AdapterError):
    """Raised for retryable network/service failures."""


class AdapterQualityError(AdapterError):
    """Raised when fetched data fails quality validation."""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class DatasetSpec:
    dataset_id: str
    name: str
    description: str
    unit: str | None
    frequency: str | None
    source_url: str
    required_columns: tuple[str, ...]
    time_column: str | None = None
    update_policy: str = "manual_refresh"


@dataclass(frozen=True)
class AdapterDescriptor:
    adapter_id: str
    version: str
    source_type: str
    description: str
    requires_credentials: bool = False
    rate_limit_seconds: float = 0.0


@dataclass(frozen=True)
class FetchRequest:
    dataset_id: str
    start: str | None = None
    end: str | None = None
    entity: str | None = None
    frequency: str | None = None
    parameters: dict[str, Any] = field(default_factory=dict)

    def normalized(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "start": self.start,
            "end": self.end,
            "entity": self.entity,
            "frequency": self.frequency,
            "parameters": self.parameters,
        }

    def cache_key(self, adapter_id: str) -> str:
        payload = {"adapter_id": adapter_id, **self.normalized()}
        return hashlib.sha256(stable_json(payload).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class QualityReport:
    status: Literal["passed", "warning", "failed"] = "passed"
    issues: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def is_usable(self) -> bool:
        return self.status != "failed"


@dataclass(frozen=True)
class FetchResult:
    adapter_id: str
    adapter_version: str
    dataset_id: str
    dataframe: pd.DataFrame
    dataset_metadata: Mapping[str, Any]
    source_metadata: Mapping[str, Any]
    fetch_metadata: Mapping[str, Any]
    quality_report: QualityReport
    content_hash: str
    warnings: tuple[str, ...] = ()

    @classmethod
    def hash_frame(cls, frame: pd.DataFrame) -> str:
        digest = hashlib.sha256()
        digest.update(stable_json([str(column) for column in frame.columns]).encode("utf-8"))
        digest.update(str(frame.shape).encode("ascii"))
        digest.update(str([str(dtype) for dtype in frame.dtypes]).encode("ascii"))
        for row in frame.itertuples(index=False, name=None):
            digest.update(stable_json([_safe_value(value) for value in row]).encode("utf-8"))
        return digest.hexdigest()


def _safe_value(value: Any) -> Any:
    if pd.isna(value):
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray)):
        return value.hex()
    return value


@dataclass
class CacheEntry:
    key: str
    created_at: float
    expires_at: float | None
    result: FetchResult


class AdapterCache:
    """Small process-local cache with TTL and explicit refresh."""

    def __init__(self, *, default_ttl_seconds: float = 300.0) -> None:
        self.default_ttl_seconds = default_ttl_seconds
        self._entries: dict[str, CacheEntry] = {}

    def get(self, key: str) -> FetchResult | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        if entry.expires_at is not None and entry.expires_at <= time.time():
            self._entries.pop(key, None)
            return None
        return entry.result

    def put(self, key: str, result: FetchResult, *, ttl_seconds: float | None = None) -> None:
        ttl = self.default_ttl_seconds if ttl_seconds is None else ttl_seconds
        self._entries[key] = CacheEntry(
            key=key,
            created_at=time.time(),
            expires_at=time.time() + ttl if ttl > 0 else None,
            result=result,
        )

    def clear(self) -> None:
        self._entries.clear()


class DataSourceAdapter(ABC):
    """Adapter boundary; adapters never call Runtime, Planner, or Agent."""

    descriptor: AdapterDescriptor
    datasets: Mapping[str, DatasetSpec]

    @abstractmethod
    def describe(self) -> AdapterDescriptor: ...

    @abstractmethod
    def list_datasets(self) -> Sequence[DatasetSpec]: ...

    @abstractmethod
    def fetch(
        self,
        request: FetchRequest,
        *,
        refresh: bool = False,
    ) -> FetchResult: ...

    @abstractmethod
    def _fetch_raw(self, request: FetchRequest, spec: DatasetSpec) -> Any: ...

    @abstractmethod
    def normalize(
        self,
        raw: Any,
        request: FetchRequest,
        spec: DatasetSpec,
    ) -> pd.DataFrame: ...

    def validate(self, frame: pd.DataFrame, spec: DatasetSpec) -> QualityReport:
        issues: list[str] = []
        warnings: list[str] = []
        missing = [column for column in spec.required_columns if column not in frame.columns]
        if missing:
            issues.append(f"Missing required columns: {', '.join(missing)}")
        if frame.empty:
            issues.append("Fetched dataset is empty")
        for column in spec.required_columns:
            if column in frame.columns and frame[column].isna().all():
                issues.append(f"Column contains only nulls: {column}")
        if spec.time_column and spec.time_column in frame.columns:
            parsed = pd.to_datetime(frame[spec.time_column], errors="coerce")
            invalid = int((frame[spec.time_column].notna() & parsed.isna()).sum())
            if invalid:
                warnings.append(f"Invalid time values: {invalid}")
            if frame.duplicated(subset=[spec.time_column]).any():
                warnings.append("Duplicate time values found")
        if not spec.unit:
            warnings.append("Dataset unit is not declared")
        status = "failed" if issues else ("warning" if warnings else "passed")
        return QualityReport(
            status=status,
            issues=tuple(issues),
            warnings=tuple(warnings),
        )


class HttpJsonAdapter(DataSourceAdapter):
    """Base adapter with cache, bounded retry, and backoff."""

    def __init__(
        self,
        *,
        cache: AdapterCache | None = None,
        transport: Callable[[str, Mapping[str, Any]], Any] | None = None,
        max_retries: int = 2,
        retry_backoff_seconds: float = 0.2,
        ttl_seconds: float = 300.0,
    ) -> None:
        self.cache = cache or AdapterCache(default_ttl_seconds=ttl_seconds)
        self._transport = transport or self._http_get
        self.max_retries = max_retries
        self.retry_backoff_seconds = retry_backoff_seconds

    def describe(self) -> AdapterDescriptor:
        return self.descriptor

    def list_datasets(self) -> Sequence[DatasetSpec]:
        return list(self.datasets.values())

    def fetch(self, request: FetchRequest, *, refresh: bool = False) -> FetchResult:
        spec = self.datasets.get(request.dataset_id)
        if spec is None:
            raise AdapterRequestError(
                f"Unknown dataset for adapter {self.descriptor.adapter_id}: {request.dataset_id}"
            )
        self._validate_request(request, spec)
        key = request.cache_key(self.descriptor.adapter_id)
        if not refresh:
            cached = self.cache.get(key)
            if cached is not None:
                return cached
        raw = self._fetch_with_retry(request, spec)
        frame = self.normalize(raw, request, spec)
        quality = self.validate(frame, spec)
        if not quality.is_usable:
            raise AdapterQualityError(
                f"Dataset {spec.dataset_id} failed quality validation: "
                + "; ".join(quality.issues)
            )
        fetched_at = utc_now_iso()
        result = FetchResult(
            adapter_id=self.descriptor.adapter_id,
            adapter_version=self.descriptor.version,
            dataset_id=spec.dataset_id,
            dataframe=frame,
            dataset_metadata={
                "dataset_id": spec.dataset_id,
                "name": spec.name,
                "description": spec.description,
                "unit": spec.unit,
                "frequency": spec.frequency,
                "source_url": spec.source_url,
                "update_policy": spec.update_policy,
                "time_column": spec.time_column,
            },
            source_metadata={
                "source_type": self.descriptor.source_type,
                "adapter_id": self.descriptor.adapter_id,
                "adapter_version": self.descriptor.version,
                "request": request.normalized(),
            },
            fetch_metadata={
                "fetched_at": fetched_at,
                "row_count": len(frame),
                "column_count": len(frame.columns),
                "quality_status": quality.status,
                "quality_issues": list(quality.issues),
                "quality_warnings": list(quality.warnings),
            },
            quality_report=quality,
            content_hash=FetchResult.hash_frame(frame),
            warnings=quality.warnings,
        )
        self.cache.put(key, result)
        return result

    def _validate_request(self, request: FetchRequest, spec: DatasetSpec) -> None:
        if not isinstance(request.parameters, dict):
            raise AdapterRequestError("parameters must be a mapping")
        if request.start and request.end:
            try:
                start = pd.to_datetime(request.start)
                end = pd.to_datetime(request.end)
            except Exception as exc:
                raise AdapterRequestError("Invalid start/end timestamp") from exc
            if start > end:
                raise AdapterRequestError("start must be before end")

    def _fetch_with_retry(self, request: FetchRequest, spec: DatasetSpec) -> Any:
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                return self._fetch_raw(request, spec)
            except AdapterRequestError:
                raise
            except AdapterQualityError:
                raise
            except Exception as exc:
                last_error = exc
                if attempt < self.max_retries:
                    time.sleep(self.retry_backoff_seconds * (2**attempt))
        raise AdapterTemporaryError(
            f"Fetch failed after {self.max_retries + 1} attempts: {last_error}"
        ) from last_error

    def _http_get(self, url: str, params: Mapping[str, Any]) -> Any:
        import requests

        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        return response.json()

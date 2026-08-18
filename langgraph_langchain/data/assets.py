"""Domain-neutral metadata models for datasets consumed by DeepAnalyze."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def hash_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    """Return the SHA-256 digest for *path* without loading it into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


class ColumnSpec(BaseModel):
    """Observed physical metadata for one column."""

    name: str
    dtype: str
    nullable: bool = False
    null_count: int = 0
    unique_count: Optional[int] = None
    semantic_type: Optional[str] = None


class SchemaSnapshot(BaseModel):
    """Immutable observation of a dataframe schema at load time."""

    snapshot_id: str = Field(default_factory=lambda: _new_id("schema"))
    row_count: int
    column_count: int
    columns: list[ColumnSpec]
    candidate_keys: list[str] = Field(default_factory=list)
    time_columns: list[str] = Field(default_factory=list)
    observed_at: str = Field(default_factory=_utc_now)

    @classmethod
    def from_dataframe(cls, dataframe: Any) -> "SchemaSnapshot":
        columns: list[ColumnSpec] = []
        candidate_keys: list[str] = []
        time_columns: list[str] = []
        row_count = int(len(dataframe))

        for name in dataframe.columns:
            series = dataframe[name]
            null_count = int(series.isna().sum())
            unique_count = int(series.nunique(dropna=True))
            dtype = str(series.dtype)
            column_name = str(name)
            columns.append(
                ColumnSpec(
                    name=column_name,
                    dtype=dtype,
                    nullable=null_count > 0,
                    null_count=null_count,
                    unique_count=unique_count,
                )
            )
            if row_count > 0 and null_count == 0 and unique_count == row_count:
                candidate_keys.append(column_name)
            if "datetime" in dtype or dtype.startswith("date"):
                time_columns.append(column_name)

        return cls(
            row_count=row_count,
            column_count=int(len(dataframe.columns)),
            columns=columns,
            candidate_keys=candidate_keys,
            time_columns=time_columns,
        )


class DataAsset(BaseModel):
    """A dataset registered with the analysis runtime."""

    asset_id: str = Field(default_factory=lambda: _new_id("asset"))
    name: str
    source_type: str
    location: str
    content_hash: Optional[str] = None
    sheet_name: Optional[str] = None
    schema_snapshot: SchemaSnapshot
    grain: Optional[str] = None
    source_metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=_utc_now)

    @classmethod
    def from_dataframe(
        cls,
        *,
        dataframe: Any,
        source_path: Path,
        source_type: Optional[str] = None,
        sheet_name: Optional[str] = None,
        source_metadata: Optional[dict[str, Any]] = None,
    ) -> "DataAsset":
        resolved = source_path.resolve()
        return cls(
            name=resolved.name,
            source_type=source_type or resolved.suffix.lower().lstrip(".") or "unknown",
            location=str(resolved),
            content_hash=hash_file(resolved) if resolved.is_file() else None,
            sheet_name=sheet_name or None,
            schema_snapshot=SchemaSnapshot.from_dataframe(dataframe),
            source_metadata=source_metadata or {},
        )

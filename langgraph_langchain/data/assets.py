"""Domain-neutral metadata models for datasets consumed by DeepAnalyze."""

from __future__ import annotations

import hashlib
import math
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional, Sequence
from uuid import uuid4

import pandas as pd
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



def canonical_text_sha256(path: Path) -> str:
    """Return a newline-insensitive SHA-256 fingerprint for UTF-8 text.

    Golden datasets are evaluated on Windows, Linux, and CI.  Raw byte hashes
    change when Git normalises CRLF to LF, so versioned text datasets use a
    canonical newline form.  A UTF-8 BOM is ignored.
    """
    text = path.read_text(encoding="utf-8-sig")
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


class ColumnSpec(BaseModel):
    """Observed physical metadata for one column.

    ``null_count`` is ``None`` when actual data has not been scanned.
    """

    name: str
    dtype: str
    nullable: bool = False
    null_count: Optional[int] = None
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


def _hash_token(digest: "hashlib._Hash", tag: str, payload: bytes) -> None:
    """Write a length-delimited token to avoid encoding ambiguities."""
    tag_bytes = tag.encode("ascii")
    digest.update(len(tag_bytes).to_bytes(2, "big"))
    digest.update(tag_bytes)
    digest.update(len(payload).to_bytes(8, "big"))
    digest.update(payload)


def _canonical_dataframe_cell(digest: "hashlib._Hash", value: Any) -> None:
    if value is None:
        _hash_token(digest, "null", b"")
        return
    try:
        is_missing = bool(pd.isna(value))
    except (TypeError, ValueError):
        is_missing = False
    if is_missing:
        _hash_token(digest, "null", b"")
        return

    if isinstance(value, (bool,)):
        _hash_token(digest, "bool", b"1" if value else b"0")
        return
    if isinstance(value, int):
        _hash_token(digest, "int", str(value).encode("ascii"))
        return
    if isinstance(value, float):
        if math.isinf(value):
            _hash_token(
                digest, "float", b"inf" if value > 0 else b"-inf"
            )
        else:
            _hash_token(digest, "float", format(value, ".17g").encode("ascii"))
        return
    if isinstance(value, str):
        _hash_token(digest, "str", value.encode("utf-8"))
        return
    if isinstance(value, (bytes, bytearray)):
        _hash_token(digest, "bytes", bytes(value))
        return
    if isinstance(value, Decimal):
        _hash_token(digest, "decimal", str(value).encode("utf-8"))
        return
    if isinstance(value, datetime):
        normalized = (
            value.astimezone(timezone.utc)
            if value.tzinfo is not None
            else value
        )
        _hash_token(digest, "datetime", normalized.isoformat().encode("utf-8"))
        return
    if isinstance(value, date):
        _hash_token(digest, "date", value.isoformat().encode("ascii"))
        return
    if isinstance(value, time):
        _hash_token(digest, "time", value.isoformat().encode("ascii"))
        return
    if isinstance(value, timedelta):
        _hash_token(digest, "timedelta", value.isoformat().encode("ascii"))
        return

    if hasattr(value, "item"):
        try:
            _canonical_dataframe_cell(digest, value.item())
            return
        except (TypeError, ValueError, OverflowError):
            pass
    raise TypeError(
        f"Unsupported DataFrame value for stable hash: {type(value).__name__}"
    )


def hash_dataframe(dataframe: Any) -> str:
    """Return a stable SHA-256 for column order, dtypes, row order, and values.

    The hash intentionally includes row order.  Unsupported object values raise
    :class:`TypeError` instead of falling back to an unstable representation.
    """
    digest = hashlib.sha256()
    _hash_token(digest, "schema-version", b"dataframe-v1")
    _hash_token(digest, "row-count", str(int(len(dataframe))).encode("ascii"))
    _hash_token(
        digest, "column-count", str(int(len(dataframe.columns))).encode("ascii")
    )

    for column in dataframe.columns:
        _canonical_dataframe_cell(digest, column)
    for dtype in dataframe.dtypes:
        _hash_token(digest, "dtype", str(dtype).encode("ascii"))
    for row in dataframe.itertuples(index=False, name=None):
        for value in row:
            _canonical_dataframe_cell(digest, value)
    return digest.hexdigest()


class DataAsset(BaseModel):
    """A dataset registered with the analysis runtime."""

    asset_id: str = Field(default_factory=lambda: _new_id("asset"))
    name: str
    source_type: str
    location: Optional[str] = None
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

    @classmethod
    def from_sqlite_query(
        cls,
        *,
        dataframe: Any,
        source_identity: str,
        query_fingerprint: str,
        query_type: str,
        table_name: Optional[str] = None,
        selected_columns: Optional[Sequence[str]] = None,
        truncated: bool = False,
        source_metadata: Optional[dict[str, Any]] = None,
        name: Optional[str] = None,
    ) -> "DataAsset":
        """Build an asset for a database query without inventing a file path."""
        if not isinstance(source_identity, str) or not source_identity.strip():
            raise ValueError("SQLite source identity must be non-empty text")
        if query_type not in {"table", "sql"}:
            raise ValueError("SQLite query type must be 'table' or 'sql'")
        if (
            not isinstance(query_fingerprint, str)
            or len(query_fingerprint) != 64
            or any(char not in "0123456789abcdef" for char in query_fingerprint)
        ):
            raise ValueError("SQLite query fingerprint must be a SHA-256 hex digest")

        provenance: dict[str, Any] = {
            "source_identity": source_identity,
            "query_fingerprint": query_fingerprint,
            "query_type": query_type,
            "table_name": table_name,
            "selected_columns": (
                [str(column) for column in selected_columns]
                if selected_columns is not None
                else [str(column) for column in dataframe.columns]
            ),
            "row_count": int(len(dataframe)),
            "truncated": bool(truncated),
        }
        if source_metadata:
            for key, value in source_metadata.items():
                provenance.setdefault(key, value)
        return cls(
            name=name or table_name or "sqlite_query",
            source_type="sqlite",
            location=None,
            content_hash=hash_dataframe(dataframe),
            schema_snapshot=SchemaSnapshot.from_dataframe(dataframe),
            source_metadata=provenance,
        )

"""Domain-neutral data source adapters for files, memory, and query results."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Iterator, Optional

import pandas as pd
from pydantic import BaseModel, Field

from .assets import SchemaSnapshot, hash_file
from .sampling import SamplingMetadata, SamplingSpec, sample_dataframe


class SourceScan(BaseModel):
    source_type: str
    source_hash: str | None = None
    schema_snapshot: SchemaSnapshot
    estimated_rows: int
    size_bytes: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class DataSource(ABC):
    """Read-only data source contract. SQL support intentionally stays out of P0."""

    source_type: str = "unknown"
    adapter_version: str = "1"

    @abstractmethod
    def scan_schema(self) -> SourceScan: ...

    @abstractmethod
    def preview(self, rows: int = 5) -> pd.DataFrame: ...

    @abstractmethod
    def estimate_rows(self) -> int: ...

    @abstractmethod
    def materialize(self, *, max_rows: int | None = None, columns: list[str] | None = None) -> pd.DataFrame: ...

    def sample(self, spec: SamplingSpec) -> tuple[pd.DataFrame, SamplingMetadata]:
        return sample_dataframe(self.materialize(), spec)


class FileDataSource(DataSource):
    source_type = "file"
    adapter_version = "file-v1"

    def __init__(self, path: Path, *, sheet_name: str | None = None):
        self.path = path.resolve()
        self.sheet_name = sheet_name or None
        self.suffix = self.path.suffix.lower()
        if not self.path.is_file():
            raise FileNotFoundError(self.path)

    def _csv_encoding(self) -> str:
        for encoding in ("utf-8", "utf-8-sig", "gbk", "gb2312", "latin-1"):
            try:
                with self.path.open("r", encoding=encoding) as handle:
                    handle.read(4096)
                return encoding
            except UnicodeDecodeError:
                continue
        return "latin-1"

    def _read(self, *, max_rows: int | None = None, columns: list[str] | None = None) -> pd.DataFrame:
        if self.suffix == ".csv":
            return pd.read_csv(self.path, encoding=self._csv_encoding(), nrows=max_rows, usecols=columns)
        if self.suffix in {".xlsx", ".xls"}:
            sheet = self.sheet_name or 0
            return pd.read_excel(self.path, sheet_name=sheet, nrows=max_rows, usecols=columns)
        if self.suffix == ".parquet":
            try:
                frame = pd.read_parquet(self.path, columns=columns)
            except ImportError as exc:
                raise RuntimeError("Parquet support requires an optional pandas engine such as pyarrow") from exc
            return frame.head(max_rows).copy() if max_rows is not None else frame
        raise ValueError(f"Unsupported file type '{self.suffix}'")

    def scan_schema(self) -> SourceScan:
        sample = self._read(max_rows=1000)
        estimated_rows = self.estimate_rows()
        return SourceScan(
            source_type=self.source_type,
            source_hash=hash_file(self.path),
            schema_snapshot=SchemaSnapshot.from_dataframe(sample),
            estimated_rows=estimated_rows,
            size_bytes=self.path.stat().st_size,
            metadata={"path": str(self.path), "suffix": self.suffix, "sheet_name": self.sheet_name},
        )

    def preview(self, rows: int = 5) -> pd.DataFrame:
        return self._read(max_rows=rows)

    def estimate_rows(self) -> int:
        if self.suffix == ".csv":
            with self.path.open("rb") as handle:
                line_count = sum(
                    chunk.count(b"\n")
                    for chunk in iter(lambda: handle.read(1024 * 1024), b"")
                )
            if self.path.stat().st_size:
                with self.path.open("rb") as handle:
                    handle.seek(-1, 2)
                    if handle.read(1) != b"\n":
                        line_count += 1
            return max(0, line_count - 1)
        return len(self._read())

    def materialize(self, *, max_rows: int | None = None, columns: list[str] | None = None) -> pd.DataFrame:
        return self._read(max_rows=max_rows, columns=columns)

    def sample(self, spec: SamplingSpec) -> tuple[pd.DataFrame, SamplingMetadata]:
        if self.suffix != ".csv" or spec.method == "stratified":
            return super().sample(spec)
        if spec.method == "head":
            sampled = self.materialize(max_rows=spec.target_rows)
            original = self.scan_schema().estimated_rows
            return sampled, SamplingMetadata(
                method="head", target_rows=spec.target_rows,
                original_row_count=original, sampled_row_count=len(sampled),
                coverage={"sample_fraction": len(sampled) / original if original else 1.0},
            )

        candidates: pd.DataFrame | None = None
        original = 0
        import numpy as np
        random = np.random.default_rng(spec.random_seed)
        for chunk in self.iter_chunks():
            original += len(chunk)
            ranked = chunk.copy()
            ranked["__sample_priority__"] = random.random(len(chunk))
            candidates = ranked if candidates is None else pd.concat([candidates, ranked], ignore_index=False)
            if len(candidates) > spec.target_rows:
                candidates = candidates.nsmallest(spec.target_rows, "__sample_priority__")
        if candidates is None:
            sampled = self.preview(0)
        else:
            sampled = candidates.nsmallest(spec.target_rows, "__sample_priority__").drop(columns="__sample_priority__")
        return sampled.copy(), SamplingMetadata(
            method="random", random_seed=spec.random_seed, target_rows=spec.target_rows,
            original_row_count=original, sampled_row_count=len(sampled),
            coverage={"sample_fraction": len(sampled) / original if original else 1.0},
        )

    def iter_chunks(self, *, chunk_rows: int = 100_000, columns: list[str] | None = None) -> Iterator[pd.DataFrame]:
        if self.suffix != ".csv":
            yield self.materialize(columns=columns)
            return
        yield from pd.read_csv(
            self.path, encoding=self._csv_encoding(), usecols=columns, chunksize=chunk_rows,
        )


class MemoryDataSource(DataSource):
    source_type = "memory"
    adapter_version = "memory-v1"

    def __init__(self, dataframe: pd.DataFrame, *, name: str = "memory"):
        self.dataframe = dataframe.copy()
        self.name = name

    def _hash(self) -> str:
        return str(pd.util.hash_pandas_object(self.dataframe, index=True).sum())

    def scan_schema(self) -> SourceScan:
        return SourceScan(
            source_type=self.source_type, source_hash=self._hash(),
            schema_snapshot=SchemaSnapshot.from_dataframe(self.dataframe), estimated_rows=len(self.dataframe),
            metadata={"name": self.name},
        )

    def preview(self, rows: int = 5) -> pd.DataFrame:
        return self.dataframe.head(rows).copy()

    def estimate_rows(self) -> int:
        return len(self.dataframe)

    def materialize(self, *, max_rows: int | None = None, columns: list[str] | None = None) -> pd.DataFrame:
        data = self.dataframe if max_rows is None else self.dataframe.head(max_rows)
        return data.loc[:, columns].copy() if columns else data.copy()


class QueryResultDataSource(MemoryDataSource):
    """A contract for governed query results; it deliberately does not execute SQL."""

    source_type = "query_result"
    adapter_version = "query-result-v1"

    def __init__(self, dataframe: pd.DataFrame, *, query_id: str, source_version: str = ""):
        super().__init__(dataframe, name=query_id)
        self.query_id = query_id
        self.source_version = source_version

    def scan_schema(self) -> SourceScan:
        scan = super().scan_schema()
        scan.metadata.update({"query_id": self.query_id, "source_version": self.source_version})
        return scan

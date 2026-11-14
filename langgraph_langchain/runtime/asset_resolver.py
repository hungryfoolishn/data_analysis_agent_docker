"""Resolve registered SQLite query-result assets into executor inputs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

import pandas as pd
from pydantic import ValidationError

from langgraph_langchain.data.assets import DataAsset, hash_file
from langgraph_langchain.data.snapshots import (
    SnapshotError,
    SnapshotReference,
    SnapshotStore,
)
from langgraph_langchain.data.sources import FileDataSource


class AssetResolutionError(RuntimeError):
    """Base error for a failed Runtime input asset resolution."""


class AssetNotFoundError(AssetResolutionError, KeyError):
    """Raised when a task references an unregistered asset ID."""


class AssetUnsupportedError(AssetResolutionError, ValueError):
    """Raised when an asset cannot be consumed through a snapshot."""


class AssetInputConflictError(AssetResolutionError, ValueError):
    """Raised when resolved inputs would overwrite a reserved namespace value."""


@dataclass(frozen=True)
class ResolvedAsset:
    """A verified dataframe plus the provenance needed by later runtime stages."""

    asset_id: str
    dataframe: pd.DataFrame
    source_type: str
    source_identity: str | None
    query_fingerprint: str | None
    query_type: str | None
    table_name: str | None
    selected_columns: tuple[str, ...]
    row_count: int
    truncated: bool
    snapshot_reference: SnapshotReference | None
    source_location: str | None = None
    sheet_name: str | None = None

    @property
    def provenance(self) -> dict[str, object]:
        return {
            "asset_id": self.asset_id,
            "source_type": self.source_type,
            "source_location": self.source_location,
            "sheet_name": self.sheet_name,
            "source_identity": self.source_identity,
            "query_fingerprint": self.query_fingerprint,
            "query_type": self.query_type,
            "table_name": self.table_name,
            "selected_columns": list(self.selected_columns),
            "row_count": self.row_count,
            "truncated": self.truncated,
            "snapshot_reference": (
                self.snapshot_reference.model_dump(mode="json")
                if self.snapshot_reference is not None
                else None
            ),
        }


_FILE_SOURCE_TYPES = {"csv", "xlsx", "xls", "parquet"}


class AssetResolver:
    """Load registered SQLite query-result snapshots by asset ID.

    The resolver deliberately does not execute SQL.  A dataframe is returned
    only after SnapshotStore has verified both physical and logical hashes.
    """

    def __init__(
        self,
        *,
        assets: Mapping[str, DataAsset],
        snapshot_store: SnapshotStore,
    ) -> None:
        self._assets = assets
        self._snapshot_store = snapshot_store

    def resolve(self, asset_id: str) -> ResolvedAsset:
        if not isinstance(asset_id, str) or not asset_id:
            raise AssetNotFoundError("Input asset ID must be non-empty text")
        asset = self._assets.get(asset_id)
        if asset is None:
            raise AssetNotFoundError(f"Input asset is not registered: {asset_id}")
        if asset.source_type in _FILE_SOURCE_TYPES:
            return self._resolve_file_asset(asset)
        # B8.1: acquisition-registered assets carry a snapshot reference for
        # any source type; resolution goes through the SnapshotStore exactly
        # like sqlite query results (no SQL re-execution, verified load).
        if isinstance(asset.source_metadata.get("snapshot_reference"), dict):
            return self._resolve_snapshot_asset(asset)
        if asset.source_type != "sqlite":
            raise AssetUnsupportedError(
                f"Asset '{asset_id}' is not a supported query-result asset"
            )

        payload = asset.source_metadata.get("snapshot_reference")
        if not isinstance(payload, dict):
            raise AssetUnsupportedError(
                f"Asset '{asset_id}' has no snapshot reference"
            )
        try:
            reference = SnapshotReference.model_validate(payload)
        except ValidationError as exc:
            raise AssetUnsupportedError(
                f"Asset '{asset_id}' has an invalid snapshot reference"
            ) from exc

        try:
            dataframe = self._snapshot_store.load(reference)
        except SnapshotError as exc:
            # Keep the original exception chain for server-side diagnosis, but
            # do not propagate absolute paths or database details to callers.
            raise AssetResolutionError(
                f"Unable to load snapshot for asset '{asset_id}'"
            ) from exc

        metadata = asset.source_metadata
        return ResolvedAsset(
            asset_id=asset_id,
            dataframe=dataframe,
            source_type=asset.source_type,
            source_identity=(
                metadata.get("source_identity")
                if isinstance(metadata.get("source_identity"), str)
                else None
            ),
            query_fingerprint=(
                metadata.get("query_fingerprint")
                if isinstance(metadata.get("query_fingerprint"), str)
                else None
            ),
            query_type=(
                metadata.get("query_type")
                if isinstance(metadata.get("query_type"), str)
                else None
            ),
            table_name=(
                metadata.get("table_name")
                if isinstance(metadata.get("table_name"), str)
                else None
            ),
            selected_columns=tuple(
                column
                for column in metadata.get("selected_columns", [])
                if isinstance(column, str)
            ),
            row_count=int(len(dataframe)),
            truncated=bool(metadata.get("truncated", False)),
            snapshot_reference=reference,
        )

    def _resolve_file_asset(self, asset: DataAsset) -> ResolvedAsset:
        """Load a registered file asset using the existing FileDataSource."""
        asset_id = asset.asset_id
        if not isinstance(asset.location, str) or not asset.location:
            raise AssetUnsupportedError(
                f"File asset '{asset_id}' has no registered location"
            )
        path = Path(asset.location)
        if not path.is_file():
            raise AssetResolutionError(
                f"File asset '{asset_id}' is no longer available"
            )
        if asset.content_hash and hash_file(path) != asset.content_hash:
            raise AssetResolutionError(
                f"File asset '{asset_id}' changed after registration"
            )
        try:
            source = FileDataSource(
                path,
                sheet_name=asset.sheet_name,
            )
            dataframe = source.materialize()
        except Exception as exc:
            raise AssetResolutionError(
                f"Unable to load file asset '{asset_id}'"
            ) from exc
        return ResolvedAsset(
            asset_id=asset_id,
            dataframe=dataframe,
            source_type=asset.source_type,
            source_location=str(path),
            sheet_name=asset.sheet_name,
            source_identity=None,
            query_fingerprint=None,
            query_type=None,
            table_name=None,
            selected_columns=tuple(str(column) for column in dataframe.columns),
            row_count=int(len(dataframe)),
            truncated=False,
            snapshot_reference=None,
        )

    def _resolve_snapshot_asset(self, asset: DataAsset) -> ResolvedAsset:
        """Load a snapshot-backed acquisition asset by verified reference."""
        payload = asset.source_metadata.get("snapshot_reference")
        try:
            reference = SnapshotReference.model_validate(payload)
        except ValidationError as exc:
            raise AssetUnsupportedError(
                f"Asset '{asset.asset_id}' has an invalid snapshot reference"
            ) from exc
        try:
            dataframe = self._snapshot_store.load(reference)
        except SnapshotError as exc:
            raise AssetResolutionError(
                f"Unable to load snapshot for asset '{asset.asset_id}'"
            ) from exc
        metadata = asset.source_metadata or {}
        return ResolvedAsset(
            asset_id=asset.asset_id,
            dataframe=dataframe,
            source_type=asset.source_type,
            source_identity=(
                metadata.get("source_identity")
                if isinstance(metadata.get("source_identity"), str)
                else None
            ),
            query_fingerprint=(
                metadata.get("query_fingerprint")
                if isinstance(metadata.get("query_fingerprint"), str)
                else None
            ),
            query_type=None,
            table_name=None,
            selected_columns=tuple(str(column) for column in dataframe.columns),
            row_count=int(len(dataframe)),
            truncated=bool(metadata.get("truncated", False)),
            snapshot_reference=reference,
        )

    def resolve_many(self, asset_ids: Sequence[str]) -> dict[str, ResolvedAsset]:
        """Resolve inputs in request order, keyed by immutable asset ID."""
        resolved: dict[str, ResolvedAsset] = {}
        for asset_id in asset_ids:
            if asset_id in resolved:
                continue
            resolved[asset_id] = self.resolve(asset_id)
        return resolved

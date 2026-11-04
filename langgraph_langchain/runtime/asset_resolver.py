"""Resolve registered SQLite query-result assets into executor inputs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

import pandas as pd
from pydantic import ValidationError

from langgraph_langchain.data.assets import DataAsset
from langgraph_langchain.data.snapshots import (
    SnapshotError,
    SnapshotReference,
    SnapshotStore,
)


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
    snapshot_reference: SnapshotReference

    @property
    def provenance(self) -> dict[str, object]:
        return {
            "asset_id": self.asset_id,
            "source_type": self.source_type,
            "source_identity": self.source_identity,
            "query_fingerprint": self.query_fingerprint,
            "query_type": self.query_type,
            "table_name": self.table_name,
            "selected_columns": list(self.selected_columns),
            "row_count": self.row_count,
            "truncated": self.truncated,
            "snapshot_reference": self.snapshot_reference.model_dump(mode="json"),
        }


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
        if asset.source_type != "sqlite":
            raise AssetUnsupportedError(
                f"Asset '{asset_id}' is not a SQLite query-result asset"
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

    def resolve_many(self, asset_ids: Sequence[str]) -> dict[str, ResolvedAsset]:
        """Resolve inputs in request order, keyed by immutable asset ID."""
        resolved: dict[str, ResolvedAsset] = {}
        for asset_id in asset_ids:
            if asset_id in resolved:
                continue
            resolved[asset_id] = self.resolve(asset_id)
        return resolved

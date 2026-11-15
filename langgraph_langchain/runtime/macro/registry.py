"""IndicatorAssetKey and Registry (B9.8.1).

Replaces the concept-keyed observations dict with a stable, collision-free
asset registry keyed by IndicatorAssetKey.  All template sufficiency checks,
plan builders and context builders consume this registry instead of raw
dict lookups.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator, Optional, Sequence

from langgraph_langchain.runtime.macro.models import MacroIndicator


@dataclass(frozen=True)
class IndicatorAssetKey:
    """Immutable, hashable unique key for a macro indicator asset.

    Fields mirror MacroIndicator identity dimensions.  concept alone is
    explicitly NOT sufficient as a key (B9.8.1 frozen rule).
    """

    indicator_id: str
    country: str
    concept: str
    frequency: str
    source_id: str
    dataset_id: str

    @classmethod
    def from_indicator(cls, indicator: MacroIndicator) -> "IndicatorAssetKey":
        return cls(
            indicator_id=indicator.indicator_id,
            country=indicator.country,
            concept=indicator.concept,
            frequency=indicator.frequency,
            source_id=indicator.source_id,
            dataset_id=indicator.dataset_id,
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "indicator_id": self.indicator_id,
            "country": self.country,
            "concept": self.concept,
            "frequency": self.frequency,
            "source_id": self.source_id,
            "dataset_id": self.dataset_id,
        }


@dataclass(frozen=True)
class RegisteredIndicatorAsset:
    """A resolved indicator asset entry in the registry."""

    key: IndicatorAssetKey
    asset_id: str
    observation_count: int
    data_version: str
    metadata: dict[str, Any] = field(default_factory=dict)


class IndicatorAssetRegistry:
    """Per-run registry for resolved macro indicator assets.

    Lifecycle: created fresh per MacroRuntimeIntegration.run() call; not
    shared across runs (B9.8.1 rule 9).
    """

    def __init__(self) -> None:
        self._entries: dict[IndicatorAssetKey, RegisteredIndicatorAsset] = {}

    def register(
        self,
        key: IndicatorAssetKey,
        asset_id: str,
        *,
        observation_count: int = 0,
        data_version: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> RegisteredIndicatorAsset:
        """Register an asset; idempotent for identical content, conflicts raise."""
        existing = self._entries.get(key)
        if existing is not None:
            if existing.asset_id == asset_id:
                return existing
            raise ValueError(
                f"Indicator asset conflict for {key.to_dict()}: "
                f"existing={existing.asset_id}, new={asset_id}"
            )
        entry = RegisteredIndicatorAsset(
            key=key,
            asset_id=asset_id,
            observation_count=observation_count,
            data_version=data_version,
            metadata=dict(metadata or {}),
        )
        self._entries[key] = entry
        return entry

    def resolve(self, key: IndicatorAssetKey) -> RegisteredIndicatorAsset:
        entry = self._entries.get(key)
        if entry is None:
            raise KeyError(f"Indicator asset not found for key: {key.to_dict()}")
        return entry

    def resolve_by_indicator_id(self, indicator_id: str) -> RegisteredIndicatorAsset:
        for entry in self._entries.values():
            if entry.key.indicator_id == indicator_id:
                return entry
        raise KeyError(f"No asset for indicator_id: {indicator_id!r}")

    def resolve_by_concept(
        self,
        concept: str,
        *,
        country: Optional[str] = None,
        frequency: Optional[str] = None,
        source_id: Optional[str] = None,
    ) -> list[RegisteredIndicatorAsset]:
        matches = [
            entry
            for entry in self._entries.values()
            if entry.key.concept == concept
            and (country is None or entry.key.country == country)
            and (frequency is None or entry.key.frequency == frequency)
            and (source_id is None or entry.key.source_id == source_id)
        ]
        return sorted(matches, key=lambda e: e.key.indicator_id)

    def resolve_unique_by_concept(
        self,
        concept: str,
        *,
        country: Optional[str] = None,
    ) -> RegisteredIndicatorAsset:
        """Resolve exactly one asset for a concept; ambiguity raises."""
        matches = self.resolve_by_concept(concept, country=country)
        if len(matches) == 0:
            raise KeyError(f"No asset for concept {concept!r}")
        if len(matches) > 1:
            ids = [entry.key.indicator_id for entry in matches]
            raise ValueError(
                f"Ambiguous concept {concept!r}: multiple matches {ids}"
            )
        return matches[0]

    def list_assets(self) -> list[RegisteredIndicatorAsset]:
        return sorted(self._entries.values(), key=lambda e: e.key.indicator_id)

    def __len__(self) -> int:
        return len(self._entries)

    def __iter__(self) -> Iterator[RegisteredIndicatorAsset]:
        return iter(self.list_assets())

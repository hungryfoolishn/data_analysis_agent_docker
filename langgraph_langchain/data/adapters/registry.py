"""Registry and catalog for external data adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from langgraph_langchain.data.adapters.base import (
    AdapterDescriptor,
    AdapterRequestError,
    DataSourceAdapter,
    DatasetSpec,
    FetchRequest,
    FetchResult,
)


@dataclass(frozen=True)
class CatalogEntry:
    dataset_id: str
    adapter_id: str
    name: str
    description: str
    unit: str | None
    frequency: str | None
    source_url: str
    availability: str = "available"


class AdapterRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, DataSourceAdapter] = {}

    def register(self, adapter: DataSourceAdapter) -> None:
        descriptor = adapter.describe()
        if descriptor.adapter_id in self._adapters:
            raise ValueError(f"Adapter already registered: {descriptor.adapter_id}")
        self._adapters[descriptor.adapter_id] = adapter

    def get(self, adapter_id: str) -> DataSourceAdapter:
        adapter = self._adapters.get(adapter_id)
        if adapter is None:
            raise AdapterRequestError(f"Unknown adapter: {adapter_id}")
        return adapter

    def adapters(self) -> Sequence[DataSourceAdapter]:
        return list(self._adapters.values())

    def catalog(self) -> list[CatalogEntry]:
        entries: list[CatalogEntry] = []
        for adapter in self._adapters.values():
            descriptor: AdapterDescriptor = adapter.describe()
            for spec in adapter.list_datasets():
                entries.append(
                    CatalogEntry(
                        dataset_id=spec.dataset_id,
                        adapter_id=descriptor.adapter_id,
                        name=spec.name,
                        description=spec.description,
                        unit=spec.unit,
                        frequency=spec.frequency,
                        source_url=spec.source_url,
                    )
                )
        return entries

    def fetch(self, request: FetchRequest, *, refresh: bool = False) -> FetchResult:
        entries = [entry for entry in self.catalog() if entry.dataset_id == request.dataset_id]
        if not entries:
            raise AdapterRequestError(f"Unknown dataset: {request.dataset_id}")
        adapter = self.get(entries[0].adapter_id)
        return adapter.fetch(request, refresh=refresh)


class DatasetCatalog:
    def __init__(self, entries: Sequence[CatalogEntry] = ()) -> None:
        self._entries: dict[str, CatalogEntry] = {
            entry.dataset_id: entry for entry in entries
        }

    def upsert(self, entry: CatalogEntry) -> None:
        self._entries[entry.dataset_id] = entry

    def get(self, dataset_id: str) -> CatalogEntry:
        entry = self._entries.get(dataset_id)
        if entry is None:
            raise AdapterRequestError(f"Unknown dataset: {dataset_id}")
        return entry

    def list(self) -> Sequence[CatalogEntry]:
        return sorted(self._entries.values(), key=lambda entry: entry.dataset_id)

    @classmethod
    def from_registry(cls, registry: AdapterRegistry) -> "DatasetCatalog":
        return cls(registry.catalog())

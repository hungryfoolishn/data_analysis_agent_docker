# B7 / Phase 2: Public Data Adapter Infrastructure

## Boundary

Adapter layer is independent of Runtime, Planner, Agent, and LLM. It fetches and
normalizes external data; the caller decides whether to register a DataAsset.

## Contracts

New module:

```text
langgraph_langchain/data/adapters/base.py
```

Core types:

- `AdapterDescriptor`
- `DatasetSpec`
- `FetchRequest`
- `FetchResult`
- `QualityReport`
- `AdapterCache`
- `DataSourceAdapter`
- `HttpJsonAdapter`

Adapter protocol:

- `describe()`
- `list_datasets()`
- `fetch(request, refresh=False)`
- `normalize(raw, request, spec)`
- `validate(frame, spec)`

`FetchResult` preserves:

- DataFrame;
- dataset metadata;
- source metadata;
- request parameters;
- fetch timestamp;
- quality report;
- logical content hash;
- warnings/errors.

## Registry and Catalog

New module:

```text
langgraph_langchain/data/adapters/registry.py
```

- `AdapterRegistry` registers adapters and dispatches by `dataset_id`;
- `DatasetCatalog` records dataset identity, source, unit, frequency, URL, and availability;
- `DatasetCatalog.from_registry()` builds the catalog from all registered adapters.

## Quality gates

Quality validation checks:

- required columns;
- empty data;
- all-null required columns;
- time parseability;
- duplicate time values;
- declared unit;
- source and fetch metadata.

Quality failure raises `AdapterQualityError`; no pseudo-success result is returned.

## Cache / retry / refresh

- Stable cache key from adapter ID and normalized request;
- TTL cache and explicit `refresh=True`;
- bounded retry with exponential backoff;
- request validation;
- temporary network failures are distinguished from request/quality errors.

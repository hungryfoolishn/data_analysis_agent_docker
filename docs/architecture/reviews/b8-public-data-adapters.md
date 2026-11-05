# B8 / Phase 2: First Public Data Adapters

## Delivered adapters

| Adapter | Dataset count | Endpoint family |
| --- | ---: | --- |
| `FREDAdapter` | 2 | FRED series observations |
| `BLSAdapter` | 2 | BLS time-series API |
| `BEAAdapter` | 2 | BEA NIPA GetData API |
| `SECAdapter` | 2 | SEC EDGAR XBRL company concepts |

Representative datasets:

- FRED: GDP, CPI;
- BLS: nonfarm employment, CPI;
- BEA: GDP, personal income;
- SEC EDGAR: company revenues, company net income.

## Contract behavior

All adapters:

- declare adapter ID/version and dataset metadata;
- normalize source payloads to tabular form;
- preserve source/request/fetch metadata;
- compute logical content hash;
- run required-column, empty-data, null, time, duplicate, and unit quality checks;
- use cache, bounded retry, backoff, explicit refresh, and request validation;
- raise quality errors instead of returning unusable data.

They do not call Runtime, Planner, Agent, or LLM.

## Test results

```bash
python -m pytest -q tests/test_public_adapters.py tests/test_public_adapter_smoke.py
```

Local result:

```text
8 passed, 4 skipped
```

Mock tests cover:

- all four adapters;
- two datasets per adapter;
- normalization and required columns;
- quality success/failure;
- cache and manual refresh;
- bounded retry;
- invalid request/unknown dataset.

Real smoke tests are intentionally skipped unless credentials or explicit smoke
flags are supplied. They were not executed in this batch because API credentials
were not available.

## Registering a fetched result

Adapter output can be converted to a runtime file asset by writing
`FetchResult.dataframe` to Parquet and registering it with
`AnalysisRuntime.register_dataframe()`. Provenance should include:

- adapter ID/version;
- source URL;
- normalized request;
- fetched_at;
- dataset period;
- quality report;
- content hash.

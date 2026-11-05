# B5 / Phase 1B: SQLite Runtime Integration

## Implemented chain

```text
Registered sqlite_database DataAsset
  -> Schema-aware SQLite planner
  -> AnalysisPlan step: run_sqlite_query
  -> StructuredTaskExecutor
  -> SQLiteQueryTool
  -> bounded read-only query
  -> QueryResultAsset + SnapshotStore
  -> SqliteQueryEvidence artifact
  -> AssetResolver for later analysis
  -> Execution / Evidence / Report path
```

## Changes

- Added `DataAsset.from_sqlite_database()` to register database sources without exposing path in metadata.
- Added `prepare_sqlite_analysis()`:
  - introspects schema;
  - registers a `sqlite_database` asset;
  - builds a deterministic schema-aware SQL plan;
  - initializes Runtime plan.
- Added session-bound `run_sqlite_query` tool in the formal tool registry.
- The tool:
  - accepts a database asset ID, not a connection;
  - reuses M2.5 bounded read-only execution;
  - does not expose credentials or connections to Python;
  - registers the result as a snapshot-backed DataAsset;
  - writes SQL Evidence as an artifact;
  - records the structured execution.
- Session initialization now detects `.sqlite` / `.db` sources and uses the SQLite workflow.

## Acceptance tests

`tests/test_phase1_runtime_integration.py` verifies:

- SQLite asset registration with `sqlite_database` source type;
- schema summary contains queryable tables;
- generated plan uses `run_sqlite_query`;
- registered SQL Tool executes a grouped query;
- result asset is snapshot-backed;
- SQL Evidence is generated and output is resolvable through AssetResolver.

`tests/test_golden_phase1.py` includes SQLite JOIN, CTE, and aggregate cases and freezes their expected values.

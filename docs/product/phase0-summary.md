# Phase 0 Summary

## Result

Phase 0 produced the product and architecture baseline for Phase 1.

## Key Decisions

1. DeepAnalyze is a general tabular data analysis platform first.
2. Macro and finance are Domain Packs, not core Runtime.
3. Phase 1 focuses on files plus one relational database.
4. Runtime V2 should become the controlled default for Phase 1.
5. Legacy ReAct remains an explicit emergency path, not the product architecture.
6. Financial semantic expansion is frozen.
7. P2/P3 financial edge cases move to backlog.

## What Exists Today

- A working legacy ReAct path for CSV/Excel analysis.
- A substantial Runtime V2 with plan, scheduler, runner, graph, verification,
  evidence, findings, report claims, rebuild, and audit package.
- General data source abstractions for CSV/Excel/Parquet, memory, and query
  results.
- Strong evidence and verification tests.
- A separate financial subsystem with extensive tests.

## What Is Missing For Phase 1

Updated by B0 review at branch `codex/phase1-m2.4-sqlite-schema`,
commit `0f4001e`:

- Runtime V2 is not enabled by default.
- The SQLite adapter, query-result DataAssets, and SnapshotStore now exist
  (M2.4-M2.7.2), but the Runtime Executor cannot yet resolve
  `input_asset_ids` back into persisted DataFrames (M2.7.3).
- No read-only SQL tool.
- No data-aware generic planner wired into the default chat path.
- No database golden e2e acceptance.
- Several oversized modules make change risky.

## Next Step

Review the Phase 1 plan. Do not start implementation until the module boundary
and acceptance set are approved.

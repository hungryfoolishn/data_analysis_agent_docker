# DeepAnalyze Roadmap

## Current Phase

**Phase 1 / M2 — SQLite DataSource Adapter (core complete; snapshot
consumption next)**

M1 Architecture Baseline has been completed and reviewed.

The M2 database access layer is implemented as of branch
`codex/phase1-m2.4-sqlite-schema` at commit `0f4001e`:

```text
SQLite connection + read-only enforcement         (data/database.py)
→ schema introspection and bounded execution     (M2.4/M2.5)
→ QueryResultDataSource / DataAsset registration (M2.6)
→ SnapshotStore for query-result DataAssets      (M2.7.2, data/snapshots.py)
→ adapter + snapshot tests
```

Completed sub-milestones: M2.4 (SQLite schema), M2.5 (bounded reads),
M2.6 (query assets), M2.7.1 (consumption inspection), M2.7.2 (snapshot store).

The SnapshotStore persists SQLite query-result DataAssets, but the Runtime
Executor does not yet resolve `input_asset_ids` back into DataFrames
(M2.7.3). The SQL Tool, Planner, Runtime V2 integration, Evidence, and Report
layers still belong to M4/M5a/M6/M7.

Normative docs:

- `docs/product/acceptance/phase1-golden-contract.md`
- `docs/architecture/runtime-v2-default-gate.md`
- `docs/roadmap/phase1-general-analysis-mvp.md`

## Phase Sequence

| Phase | Goal | Primary Outcome |
| --- | --- | --- |
| Phase 0 | Product and architecture convergence | Product vision, scope, module map, issue register, Phase 1 plan |
| Phase 1 | General analysis MVP | CSV/Excel/Parquet + SQLite analysis with planning, execution, evidence, reporting |
| Phase 2 | Data acquisition | Public API/adapters, collection, normalization, quality checks |
| Phase 3 | Macro Domain Pack | GDP, CPI, employment, rates, trade analysis |
| Phase 4 | Financial Domain Pack | Migrate current financial Runtime into a pluggable pack |
| Phase 5 | Research multi-agent | Complex multi-source research with roles and synthesis |

## Phase 1 Focus

- File and database analysis.
- Schema and quality understanding.
- Generic `AnalysisPlan`.
- Python/pandas and read-only SQL execution.
- Verified evidence.
- Traceable report.
- Session continuity.

## Current Freeze

Do not expand:

- Financial semantics.
- Macro data collection.
- Multi-agent research.
- New UI framework.
- Distributed execution.

## Post-phase Rule

After Phase 1, new work must reference a milestone and pass the stop-rule
check. P2/P3 issues go to backlog unless they become P0/P1.

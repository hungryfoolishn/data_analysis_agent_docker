# DeepAnalyze Roadmap

## Current Phase

**Phase 1 / M1 — Architecture Baseline Confirmation**

Phase 0 completed the product/architecture audit. Phase 1 M1 freezes the MVP
scope, SQLite CI baseline, Golden contracts, M2/M6 boundaries, and the Runtime
V2 default-switch gate. No Phase 1 feature code is implemented in M1.

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

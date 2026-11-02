# DeepAnalyze Scope and Non-Goals

## In Scope For Phase 1

### Data Inputs

- CSV files.
- Excel files with one or more sheets.
- Parquet files.
- One relational database source, preferably SQLite for the first integration
  gate, with PostgreSQL/MySQL as the next adapter.
- In-memory DataFrames already registered by the runtime.

### Understanding

- Schema discovery.
- Column types and basic value profiles.
- Missing values, duplicates, outliers, cardinality, and basic quality flags.
- Dataset summary usable by the planner.

### Planning

- Generic `AnalysisPlan` creation.
- Plan validation before execution.
- Plan pause, resume, revise, and confirm controls.
- Plan metadata for traceability.

### Execution

- Existing structured tools:
  - `load_data`
  - `eda_profile`
  - `analysis_methods`
  - `declare_metric`
  - `declare_assumption`
  - `record_finding`
  - `finish_report`
- Existing Python/pandas execution path.
- Read-only SQL execution for supported relational sources.

### Evidence And Reporting

- Verified execution evidence.
- Findings linked to execution/artifact/source lineage.
- Tables and charts.
- Final Markdown report.
- Artifact download and report rebuild.

### Session Management

- Session-scoped workspace.
- Upload history.
- Run history.
- Cancel, pause, resume, retry.
- Artifact retrieval.

## Out Of Scope For Phase 1

### Explicit Non-goals

- Stock trading, investment decisions, portfolio management, or order execution.
- Automated crawling of financial or macro data.
- Full financial statement parsing or accounting-standard interpretation.
- Macroeconomic data ingestion or normalization.
- Multi-agent debate or multi-agent financial research.
- Production multi-tenant RBAC.
- Distributed workflow orchestration.
- Model training or AutoML.
- Real-time streaming analytics.
- A new frontend framework.
- Replacing LangGraph or Runtime V2.

## Later, But Not Phase 1

| Capability | Later Phase |
| --- | --- |
| Public macro data adapters | Phase 2 or later |
| Macro Domain Pack | Phase 3 |
| Financial Domain Pack consolidation | Phase 4 |
| Multi-agent research | Phase 5 |
| Advanced permissions and multi-tenancy | Platform hardening |
| Distributed execution | Platform hardening |

## M1 Contract Freeze

For Phase 1 M1, the general analysis MVP is frozen to these input families:

- CSV files.
- Excel files with one or more sheets.
- Parquet files.
- SQLite relational databases.
- In-memory DataFrames registered by the runtime.

The first database target is SQLite because it is deterministic, local, and CI-friendly. PostgreSQL/MySQL adapters are deferred until after the SQLite contract passes.

Phase 1 remains a general tabular-analysis milestone. It does not add financial
semantics, macro-data acquisition, trading logic, or multi-agent research.
Domain-specific rules belong in later Domain Packs.

## Boundary Rule

A capability belongs in the core platform only if it would be useful for CSV,
Excel, Parquet, and SQL tabular analysis in any domain. It belongs in a Domain
Pack only when it requires domain-specific semantics, formulas, sources, or
regulatory interpretation.

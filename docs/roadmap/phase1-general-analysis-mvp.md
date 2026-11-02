# Phase 1 — General Analysis MVP Roadmap

## Goal

Deliver a reliable CSV/Excel/Parquet plus one-relational-database analysis MVP
with schema understanding, planning, execution, evidence, and reporting.

## Success Statement

A user uploads one of the Phase 1 golden files or connects the golden SQLite
database, asks one of the golden questions, and receives a correct report with
evidence and artifacts.

## Milestones

### M1 — Architecture Baseline

- Phase 0 documents merged.
- Team agrees module classification.
- Phase 1 target architecture approved.
- Stop rules approved.

### M2 — Database Source Adapter

- Add `DatabaseDataSource`.
- Support SQLite first.
- Implement schema introspection.
- Implement read-only SQL execution.
- Enforce query timeout and row limit.
- Add unit and integration tests.

Acceptance:

- SQLite fixture is scanned.
- SQL result becomes a runtime data asset.
- Query evidence links query text and artifact.
- Unsafe SQL is rejected.

### M3 — Data Understanding Service

- Build a reusable schema/profile service.
- Return row count, columns, types, missing values, duplicates, cardinality,
  numeric summaries, datetime range, and quality warnings.
- Expose result to planner and report.

Acceptance:

- Golden datasets produce deterministic profiles.
- Missing/duplicate/outlier scenarios produce documented quality flags.

### M4 — Generic Planner

- Generate `AnalysisPlan` from user question plus understanding summary.
- Use deterministic templates for simple analytical intents.
- Use LLM only for intent/parameter selection.
- Validate methods, assets, columns, and dependencies.
- Persist plan metadata.

Acceptance:

- Every golden question produces an inspectable plan.
- No plan references unknown columns or unavailable tools.

### M5 — Runtime Default Path

- Add a tested gate for Runtime V2 default execution.
- File and database golden scenarios run through Runtime V2.
- Legacy ReAct remains explicit emergency fallback only.

Acceptance:

- Runtime V2 executes all golden questions.
- No silent fallback occurs on ordinary task failure.

### M6 — SQL Tool And Evidence

- Add read-only SQL tool.
- Return result artifacts.
- Verify row counts and artifact existence.
- Link SQL text, result, execution, and evidence.

Acceptance:

- Evidence for database questions is complete and traceable.

### M7 — Report And Session Integration

- Update report to include data quality, evidence references, SQL/pandas
  reproducibility, and limitations.
- Integrate database analysis into session/run/artifact history.

Acceptance:

- User can download report and artifacts.
- Report claims trace to evidence.

### M8 — Golden Acceptance

- Execute all golden datasets/questions.
- Record pass/fail and known limitations.
- Freeze Phase 1.

## Task Dependency Order

```text
M1 Baseline
  -> M2 Database Source
       -> M3 Understanding
            -> M4 Planner
                 -> M5 Runtime Default
                      -> M6 SQL Tool / Evidence
                           -> M7 Report / Session
                                -> M8 Golden Acceptance
```

## Explicitly Deferred

- Macro data collection.
- Financial Domain Pack migration.
- Multi-agent research.
- New frontend.
- Distributed execution.
- Multi-tenant RBAC.

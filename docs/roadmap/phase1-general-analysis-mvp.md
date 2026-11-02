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

Status: ready for review; no Phase 1 feature code is implemented.

Objectives:

1. Freeze Phase 1 product scope and non-goals.
2. Freeze SQLite as the first CI database.
3. Freeze Golden Dataset and Golden Question contracts.
4. Separate M2 database adapter responsibilities from M6 SQL tool integration.
5. Define the Runtime V2 default-switch gate and prohibit silent fallback.

Inputs:

- Phase 0 scope freeze.
- Phase 0 architecture audit.
- Phase 0 issue register.

Documents:

- `docs/product/scope-and-non-goals.md`
- `docs/architecture/target-architecture-phase1.md`
- `docs/product/acceptance/phase1-golden-contract.md`
- `docs/architecture/runtime-v2-default-gate.md`
- `docs/development/definition-of-done.md`

Acceptance checklist:

- [ ] Phase 1 inputs are limited to CSV, Excel, Parquet, SQLite, and in-memory
  DataFrames.
- [ ] Financial, macro, crawler, and multi-agent work is explicitly out of scope.
- [ ] SQLite is selected as the first CI database.
- [ ] SQLite fixtures are local, deterministic, isolated, and CI-friendly.
- [ ] Dataset manifests define schema, row count, nullability, semantics,
  profile expectations, and expected artifacts.
- [ ] Question manifests define expected results, tolerances, required methods,
  evidence, forbidden behavior, and CI tier.
- [ ] Numeric tolerance rules are explicit.
- [ ] Evidence, artifact, plan, and report assertions are explicit.
- [ ] M2 owns connection, introspection, and bounded execution.
- [ ] M6 owns tool integration and evidence wiring, not connection management.
- [ ] Runtime V2 default-switch gate has measurable conditions.
- [ ] Silent legacy fallback is prohibited.
- [ ] M1-M8 dependencies, deliverables, and non-goals are documented.

Definition of done:

- Documentation-only change.
- Markdown and internal links reviewed.
- `git diff --check` passes.
- No runtime code or dependencies changed.
- Reviewer can move directly to M2 without reopening scope questions.

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

## Milestone Details

| Milestone | Depends On | Primary Modules | Tests | Non-goals |
| --- | --- | --- | --- | --- |
| M1 Architecture Baseline | Phase 0 | docs only | docs review, link check | No adapter/tool/runtime implementation |
| M2 SQLite DataSource Adapter | M1 | `langgraph_langchain/data/database.py` | adapter unit/integration tests | No Runtime tool, no planner |
| M3 Generic Data Understanding | M2 for SQLite, existing file support | data understanding service | profile/quality tests | No financial or macro semantics |
| M4 Data-aware Generic Planner | M3 output contract | `langgraph_langchain/runtime/generic_planner.py` | plan validation tests | No execution changes |
| M5 Runtime V2 File Default | M2-M4 and golden file tests | Runtime V2 bridge/config | runtime selection tests | No silent legacy fallback |
| M6 SQL Tool Integration | M2 adapter + Runtime V2 | SQL tool and evidence wiring | tool/evidence tests | No direct DB connection in tool |
| M7 Report / Session / Artifacts | M6 | report/session integration | artifact/e2e tests | No new UI framework |
| M8 Golden Acceptance | M1-M7 | fixtures and e2e tests | full Golden matrix | No new scope |

M2 and M3 may proceed partially in parallel only if M3 uses file fixtures and
does not depend on SQLite adapter details. M4 cannot start until M3 freezes its
understanding output contract.

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

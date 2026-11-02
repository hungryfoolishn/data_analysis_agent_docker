# Phase 1 — General Analysis MVP Roadmap

## Goal

Deliver a reliable CSV/Excel/Parquet plus one-relational-database analysis MVP
with schema understanding, planning, execution, evidence, and reporting.

## Success Statement

A user uploads one of the Phase 1 golden files or connects a temporary SQLite
fixture built by the Golden fixture helper, asks one of the golden questions,
and receives a correct report with evidence and artifacts.

## Milestones

### M1 — Architecture Baseline

Status: completed as a documentation-only architecture milestone; no Phase 1
feature code is implemented and no actual manifests/fixtures are frozen yet.

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

Acceptance checklist (contract review only):

- [x] Phase 1 inputs are limited to CSV, Excel, Parquet, SQLite, and in-memory
  DataFrames as runtime inputs.
- [x] Financial, macro, crawler, and multi-agent work is explicitly out of scope.
- [x] SQLite is selected as the first CI database.
- [x] The SQLite fixture-building contract is local, deterministic, isolated,
  and CI-friendly.
- [x] The dataset manifest schema defines schema, row count, nullability,
  semantics, profile expectations, and expected artifacts.
- [x] The question manifest schema defines expected results, tolerances,
  required methods, evidence, forbidden behavior, and CI tier.
- [x] Numeric tolerance rules are explicit.
- [x] Evidence, artifact, plan, and report assertions are explicit.
- [x] M2 owns connection, introspection, and bounded execution.
- [x] M6 owns tool integration and evidence wiring, not connection management.
- [x] Runtime V2 file-path and overall default-switch gates have measurable
  conditions.
- [x] Silent legacy fallback is prohibited.
- [x] M1-M8 dependencies, deliverables, and non-goals are documented.
- [ ] Actual dataset/question manifests and all expected values are delivered in
  M2/M3/M8, not in M1.

These items validate the frozen contract, not executable Golden tests. Actual
manifests, fixtures, and Golden test runs are M2/M3/M8 deliverables.

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

- The SQLite fixture can be connected and scanned.
- Schema introspection returns correct table and column information.
- Read-only SQL execution returns a structured query result.
- A query result can be converted into a runtime data asset.
- SQL timeout, row limit, and result-size limits are enforced.
- `INSERT`, `UPDATE`, `DELETE`, DDL, and other unsafe operations are rejected.
- Connection close, exception cleanup, and resource release work correctly.

Evidence wiring is not part of M2; it belongs to M6.

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

### M5a — Runtime V2 File Path Enablement

- Add a tested gate for Runtime V2 on file-analysis scenarios only.
- Enable Runtime V2 for file golden questions after the generic planner exists.
- Legacy ReAct remains an explicit emergency fallback only.

Acceptance:

- File golden questions run through Runtime V2.
- Planner and file execution paths are selected explicitly.
- No silent fallback occurs on ordinary task failure.
- SQLite Golden tests are not required for M5a.

### M6 — SQL Tool And Evidence

- Add read-only SQL tool.
- Return result artifacts.
- Verify row counts and artifact existence.
- Link SQL text, result, execution, and evidence.

Acceptance:

- The SQL tool executes queries through the M2 adapter.
- SQL query text, execution records, and result artifacts are linked.
- Evidence can reference the SQL execution result and its artifact.
- Evidence can be consumed by findings and report claims.
- Database questions produce complete, traceable evidence.

### M7 — Report And Session Integration

- Update report to include data quality, evidence references, SQL/pandas
  reproducibility, and limitations.
- Integrate database analysis into session/run/artifact history.

Acceptance:

- User can download report and artifacts.
- Report claims trace to evidence.

### M8 / M5b — Overall Default And Golden Acceptance

- This milestone applies the Runtime V2 overall default gate after file and
  SQLite paths pass.
- Execute all file and SQLite golden datasets/questions.
- Verify Session, SSE, artifacts, report, and evidence integration.
- Record pass/fail and known limitations.
- Switch Runtime V2 to the overall default only if the gate passes.
- Freeze Phase 1.

## Milestone Details

| Milestone | Depends On | Primary Modules | Tests | Non-goals |
| --- | --- | --- | --- | --- |
| M1 Architecture Baseline | Phase 0 | docs only | docs review, link check | No adapter/tool/runtime implementation |
| M2 SQLite DataSource Adapter | M1 | `langgraph_langchain/data/database.py` | adapter unit/integration tests | No Runtime tool, planner, or Evidence wiring |
| M3 Generic Data Understanding | M2 for SQLite, existing file support | data understanding service | profile/quality tests | No financial or macro semantics |
| M4 Data-aware Generic Planner | M3 output contract | `langgraph_langchain/runtime/generic_planner.py` | plan validation tests | No execution changes |
| M5a Runtime V2 File Path | M2-M4 and golden file tests | Runtime V2 bridge/config | runtime selection/file tests | No SQL requirement; no silent fallback |
| M6 SQL Tool Integration | M2 adapter | SQL tool and evidence wiring | tool/evidence tests | No direct DB connection in tool |
| M7 Report / Session / Artifacts | M5a + M6 | report/session integration | artifact/e2e tests | No new UI framework |
| M5b/M8 Overall Default + Golden Acceptance | M5a + M6 + M7 | Runtime gate, fixtures, e2e tests | file and SQLite Golden matrix | No scope growth |

M2 and M3 may proceed partially in parallel only if M3 uses file fixtures and
does not depend on SQLite adapter details. M4 cannot start until M3 freezes its
understanding output contract.

## Task Dependency Order

```text
M1 Baseline
  -> M2 Database Source
       -> M3 Understanding
            -> M4 Planner
                 -> M5a Runtime V2 File Path
                      -> M6 SQL Tool / Evidence
                           -> M7 Report / Session
                                -> M5b/M8 Overall Default + Golden Acceptance
```

## M1 Follow-up Notes

M1 freezes the contract schema, terminology, fixture-building rules, and
coverage matrix. It does not freeze actual manifests or fixtures. Those are
delivered later:

- SQLite fixture builder: M2.
- Deterministic profiles for file fixtures: M3.
- Final question expected values and CI matrix: M8.

A change to fixture data or a question's expected value after M8 requires a
milestone decision and a documented reason.

## Explicitly Deferred

- Macro data collection.
- Financial Domain Pack migration.
- Multi-agent research.
- New frontend.
- Distributed execution.
- Multi-tenant RBAC.

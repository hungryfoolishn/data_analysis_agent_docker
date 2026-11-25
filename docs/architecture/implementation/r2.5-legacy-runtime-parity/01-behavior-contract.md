# R2.5 Behavior Contract — Legacy Runtime vs Unified Runtime V2

Authority: Architecture Convergence V1.1, Stage E.
Baseline: `codex/architecture-convergence-v1.1-e2e` @ `36a3900`.
Migration instrument: `langgraph_langchain/runtime/capability_adapter.py` (`ToolCallBudget`),
wired into `StructuredTaskExecutor` and `_run_runtime_v2_graph_stream`.

## Governing principle

Runtime V2 already invokes the **real Legacy tool objects** through `StructuredTaskExecutor`;
it does not reimplement tools. R2.5 therefore migrates Legacy behavior by adding the missing
execution governance around that bridge instead of duplicating tool code:

- every structured tool call enters the unified execution result/evidence pipeline;
- multi-round tool usage is bounded by an explicit fail-closed budget;
- python execution remains governed by the R0a timeout + artifact-recorder boundary;
- react steps remain governed by the V2 task interface (no direct ReAct bypass of the plan).

## Capability behavior contract

| # | Capability | Legacy behavior | Runtime V2 current behavior | Gap | Expected behavior | Migration design | Security constraint | Evidence requirement | Test scenarios | Acceptance |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | Dynamic data exploration | ReAct loop calls `load_data`/`python_repl`/SQL tools freely, only bounded by `recursion_limit: 60` (`langgraph_agent.py:2171`) | Plan steps + `python_repl` via `PythonTaskExecutor` (timeout, artifact recorder); structured tool calls now consumed against `ToolCallBudget` | Legacy had no explicit per-run tool budget in V2 bridge | Bounded, auditable multi-tool runs that cannot loop forever | `ToolCallBudget(max_calls=max(24, len(plan.steps)*3))` created per V2 stream, fail-closed via `CapabilityBudgetExceededError` | Budget is runtime-owned; LLM output can never raise it | Budget snapshot exposed on `session.runtime_tool_budget` | `tests/test_r25_legacy_parity.py::test_tool_budget_tracks_history_and_fails_closed`, `::test_structured_executor_rejects_budget_exhaustion_before_tool_side_effect` | Budget exhaustion raises before tool side effect; history recorded |
| 2 | Dynamic schema exploration | `load_data` + `eda_profile` + ad-hoc python inspection | Both tools supported through the structured executor (`load_data`, `eda_profile`; V2 stage hooks for `schema_understanding` / `data_quality_check`) | none material | Same tool surface under V2 governance | Real tool objects reused; no re-implementation | Data boundary unchanged (workspace-scoped source path, R0b identity) | DataAsset content hash via existing `load_data` path | parity suite (fake chat model drives `load_data` + analysis tools) | Identical schema-visible outputs through V2 |
| 3 | Multi-round tool calls | Native ReAct multi-round | Sequential plan execution (`max_running_tasks=1`); every structured call now counted in one budget | Before R2.5 the count of structured calls was implicit | Explicit per-run call accounting and hard stop | Budget `consume()` before tool resolution; exhaustion fails closed | Reject-on-exceed, never silent truncate | `budget.snapshot()` records per-tool counts and ordered history | `test_r25_legacy_parity.py::test_tool_budget_tracks_history_and_fails_closed` | Call counts visible, stop enforced |
| 4 | Intermediate result inspection | Persistent python namespace (`session.ns`) survives across calls | `runtime.execution_namespace = session.ns` shared into V2; artifacts registered via `register_session_artifact` | none material | Same persistence under V2 artifact governance | Shared namespace + artifact recorder unchanged | Artifact registration mandatory for saved files | Artifact metadata with `created_by_tool=python_repl`, step id | parity + python executor suites | Intermediate state reachable across steps |
| 5 | Context-aware tool selection | ReAct LLM picks tools per turn | Planner emits `AnalysisPlan`; `SkillRetriever` attached to execution controller; `react_*` steps can invoke the ReAct agent | Planner-driven selection is prompt-quality dependent (R3 scope) | Tool selection traceable to plan step | Plan step method → tool resolution via `tool_map` | Unknown/non-declared tools rejected before execution | Execution results recorded per task | `tests/runtime_v2/test_runtime_v1_v2_parity.py` | Unsupported methods hard-stop, no fallback |
| 6 | Tool result feedback | ReAct sees each tool observation | `runtime.record_execution_result(result)` on finish; outputs/asset ids extracted | Plan-level revision from results not yet automated (R3) | Results observable by downstream steps | Output binding + `expected_outputs` injection in `StructuredTaskExecutor._prepare_arguments` | No execution result bypasses the recorded result object | Result record with status/artifacts/duration | `test_r25_legacy_parity.py::test_structured_executor_records_each_legacy_tool_call` | Each call yields one recorded result |
| 7 | Iterative analysis | ReAct iterates until report or recursion limit | Multi-step plan executed sequentially under one budget | Same as #1/#3 | Iteration bounded and auditable | Single budget per V2 stream | Budget cannot be reset by agent | Budget snapshot | budget exhaustion test | Iteration terminates deterministically |
| 8 | Dynamic query | `run_sqlite_query` ad hoc | Structured executor supports `run_sqlite_query` (read-only, snapshot-backed per R2 inventory) | none | Same behavior under V2 | Real tool object reused | Read-only SQL boundary unchanged | Snapshot-backed DataAsset | SQLite query suites + parity | Read-only enforced |
| 9 | Plan adaptation | ReAct may deviate from the confirmed plan with disclosure | V2 plan fixed at stream start; deviations only via `react_*` steps | Automated plan revision deferred to R3 adaptive planning | No uncontrolled plan mutation in V2 | Fail-closed: plan is validated once, then executed | Plan mutation cannot bypass execution policy | Run/step state transitions recorded | parity failure/cancel tests | Status changes explicit; no silent re-plan |
| 10 | Multi-step reasoning | Native ReAct | Plan steps with dependency graph from planner | planner quality (R3) | Deterministic step ordering preserved | Existing scheduler (sequential) | Steps cannot bypass ownership/identity checks | Step records | parity normal analysis | Same conclusions reachable via V2 |
| 11 | Failure recovery | Legacy: consecutive-error accounting, resume, stage retry | V2: per-task `failed`/`needs_revision` recorded; strict mode hard-stops session; fallback only for infrastructure failures when explicitly enabled | Recovery must not bypass evidence/report gates | Failures visible, never converted to false success | Existing V2 outcome machinery unchanged in R2.5 | No fallback in strict mode | `runtime_outcome` with error code | `test_parity_failure_never_produces_false_success` | Failure yields failure, not report |
| 12 | Reporting | `finish_report` + legacy synthesis | `build_analysis_report` + validator + report gate before stream completion | none material | Same reporting gate for migrated capabilities | Unchanged in R2.5 | Report without verified evidence rejected in strict mode | Findings/evidence linkage | parity normal analysis closure | Report gate still enforced |

## Residual limitation (documented, accepted for R2.5)

The R2.5 budget governs **structured tool calls**. `python_repl` calls are governed by the
R0a execution boundary (timeout, workspace confinement, artifact recorder) rather than the
tool-call counter, and `react_*` steps delegate to the prebuilt agent with its own
`recursion_limit`. This is a deliberate partial scope: it migrates Legacy multi-tool behavior
under V2 governance without introducing a second execution policy. Removing this residual gap
(unified counting across all three executors) is assigned to R3 orchestration work, not to R2.5.

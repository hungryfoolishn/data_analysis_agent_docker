# R2.5 Behavior Parity Golden Matrix

Behavior-level validation, not byte-identical output comparison. Legacy and V2 may differ in
token stream wording; the matrix locks capability availability, call accounting, data
correctness, evidence, final-result semantics, error handling and security boundary.

| # | Scenario | V1.1 requirement | Locked by | What is asserted |
| --- | --- | --- | --- | --- |
| 1 | Simple data question | parity on numbers + closure | `tests/runtime_v2/test_runtime_v1_v2_parity.py::test_parity_normal_analysis_numbers_and_closure` | Same numbers via V1 and V2; V2 stream closes cleanly |
| 2 | Schema discovery | load_data behavior identical | same parity test (fake chat model drives load_data) | Schema-visible outputs identical through the V2 bridge |
| 3 | Dynamic exploration | bounded, auditable | `test_parity_tool_call_counts_have_no_duplicate_execution` + new budget tests | Tool call counts recorded; no duplicate execution ids; budget fail-closed |
| 4 | Multi-step analysis | ordered multi-step plan | `test_parity_normal_analysis_numbers_and_closure` | Sequential plan executes and completes |
| 5 | Multiple tool calls | per-call accounting | `tests/test_r25_legacy_parity.py::test_tool_budget_tracks_history_and_fails_closed` | Per-tool counts + ordered history; exhaustion raises before side effect |
| 6 | Intermediate result inspection | persisted namespace | `test_structured_executor_records_each_legacy_tool_call` + python executor artifact suites | Each call recorded; artifacts registered with step id |
| 7 | Result-driven next tool selection | downstream steps see results | parity suite (scripted multi-tool conversation) + `react_executor` suites | Output binding feeds later steps; unsupported methods hard-stop |
| 8 | Iterative analysis | stop condition exists | `tests/test_r25_legacy_parity.py::test_structured_executor_rejects_budget_exhaustion_before_tool_side_effect` | Iteration ends deterministically at budget exhaustion |
| 9 | Plan revision | explicit state change only | parity failure/cancel tests + `_run_runtime_v2_graph_stream` plan validation | No silent re-plan; deviations only via react steps; R3 owns automation |
| 10 | Failure recovery | failure never becomes success | `test_parity_failure_never_produces_false_success` | Failed V2 run yields failure outcome, not a report |
| 11 | Evidence generation | unified evidence pipeline | parity suite + runtime evidence/report suites (`EvidenceCollector`, `VerificationPolicy` wiring unchanged) | Evidence collected through the same factory as before R2.5 |
| 12 | Report generation | report gate enforced | `test_parity_normal_analysis_numbers_and_closure` (closure) + report validator suites | Reports still gated on verified evidence |

## Surface restriction coverage

`tests/test_r25_legacy_parity.py::test_tool_budget_can_restrict_capability_surface` locks the
optional `allowed_tools` surface: a tool outside the allowed set is rejected with
`CapabilityBudgetExceededError` even when budget calls remain. This is the contract R3 will
use for capability allow-listing; V2 currently constructs the budget without restricting the
surface, matching the Legacy full-tool inventory in R2.

## Known matrix limitation

Scenarios 3/5/6/7 are validated at the executor/bridge level with fake chat models plus the
API-level parity suite. A dedicated multi-scenario planner-quality benchmark is an R3
deliverable (Golden Task Set) and is intentionally not claimed as an R2.5 result.

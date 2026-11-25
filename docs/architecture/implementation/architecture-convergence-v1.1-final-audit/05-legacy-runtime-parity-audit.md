# Legacy / Unified Runtime Parity Audit — R2.5

## Method

Each of the 12 V1.1 capability contracts was re-checked against code and the executed test
suite (`tests/test_r25_legacy_parity.py` + `tests/runtime_v2/test_runtime_v1_v2_parity.py`,
11 passed). The parity suite drives the real `run_analysis_stream` API for both runtimes
with a scripted fake chat model — real production paths, not interface mocks.

## Scenario classification (V1.1 8.4 matrix)

| Scenario | Classification | Basis |
| --- | --- | --- |
| 1 Simple data question | TRUE_PARITY | same numbers asserted for V1 and V2 (`test_parity_normal_analysis_numbers_and_closure`) |
| 2 Schema discovery | TRUE_PARITY | load_data behavior identical through V2 bridge (same test) |
| 3 Dynamic exploration | PARTIAL_PARITY | bounded via budget; exploration-within-a-step is react-driven; unbounded-loop parity is not a V1.1 goal |
| 4 Multi-step analysis | TRUE_PARITY | sequential plan executes and closes |
| 5 Multiple tool calls | TRUE_PARITY | per-call accounting asserted (`test_tool_budget_tracks_history_and_fails_closed`) |
| 6 Intermediate result inspection | TRUE_PARITY | shared namespace + artifact registration; per-call recording test |
| 7 Result-driven selection | PARTIAL_PARITY | downstream binding feeds steps; react-step selection is LLM-driven in both runtimes |
| 8 Iterative analysis | TRUE_PARITY | iteration ends deterministically at budget exhaustion (before side effects) |
| 9 Plan revision | INTERFACE_ONLY | no automated re-plan in V2; deviations only via governed react steps (deferred, safe default) |
| 10 Failure recovery | TRUE_PARITY | failure never produces false success (`test_parity_failure_never_produces_false_success`) |
| 11 Evidence generation | TRUE_PARITY | same EvidenceCollector/VerificationPolicy factory both runtimes |
| 12 Report generation | TRUE_PARITY | report gate + closure asserted |

Tally: 8 TRUE_PARITY, 2 PARTIAL_PARITY, 1 INTERFACE_ONLY, 0 LEGACY_ONLY, 0 REGRESSION.

## Honest residual

- `python_repl` calls were governed by timeout/artifact boundary rather than the call
  counter at R2.5; R3 closed this residual at the dispatcher (see 06).
- Dynamic exploration parity is bounded-by-design: V1.1 requires bounded exploration, so
  unbounded-like-Legacy is explicitly not a parity goal.

**R2.5 verdict: BEHAVIOR PARITY ESTABLISHED for declared core behaviors; plan-revision
remains interface-only with a safe default.**

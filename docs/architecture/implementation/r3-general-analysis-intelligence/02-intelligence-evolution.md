# R3 Intelligence Evolution — Implemented This Stage

## Implemented: unified orchestration budget

R2.5 introduced the Legacy→V2 capability budget but counted only structured tool calls
(see `../r2.5-legacy-runtime-parity/01-behavior-contract.md`, residual limitation).
R3 closes that gap at the single dispatch point so **every** governed execution —
structured tool call, react step, and python_repl run — is counted against one shared,
fail-closed, auditable per-run budget.

### Design

| Decision | Rationale |
| --- | --- |
| Accounting moved from `StructuredTaskExecutor` into `runtime/executor.TaskExecutor` | TaskExecutor is the only dispatch point for all three executor types; one choke point = one accounting semantic |
| Budget consumed inside the dispatch try-block, before executor resolution | Exhaustion converts to a recorded `failed` ExecutionResult (`CapabilityBudgetExceededError`) instead of crashing the stream; no executor side effect occurs |
| Blocked calls do not consume budget | A rejected call is not an executed call; snapshot stays truthful |
| `ToolCallBudget` unchanged | Adapter API stays stable; `allowed_tools` surface restriction now also governs react/python calls |
| `build_execution_controller(call_budget=...)` | Runtime-level wiring keeps executor construction symmetric with verifier/evidence wiring |

### Files changed

- `langgraph_langchain/runtime/executor.py` — `call_budget` param + consume-before-dispatch
- `langgraph_langchain/runtime/context.py` — `build_execution_controller(call_budget=...)`
- `langgraph_langchain/execution/structured_executor.py` — executor-level budget removed (moved to dispatcher)
- `langgraph_langchain/langgraph_agent.py` — budget passed at controller level
- `tests/test_r25_legacy_parity.py` — R2.5 tests re-anchored to the dispatcher contract
- `tests/test_r3_unified_orchestration_budget.py` — new unified budget contract tests (4)

### Test evidence (executed this stage)

| Suite | Result |
| --- | --- |
| `tests/test_r25_legacy_parity.py` + `tests/test_r3_unified_orchestration_budget.py` | 8 passed |
| Dispatcher-affected regression (planner v2 contract, executors, output bindings, runner hooks, graph, B8 integration, V1/V2 parity) | 74 passed |
| Intelligence/evaluation/planner suites (evaluation runner + deterministic, runtime golden E2E, B6 ground truth, general planner, plan control, structured plan) | 37 passed |

## Consciously deferred (with safe defaults retained)

| Deferred item | Why deferred | Safe default in place |
| --- | --- | --- |
| Automated plan revision (adaptive re-planning) | Requires a new outcome state machine; prompt-driven re-planning would be untestable now | Fixed validated plan; deviations only via governed react steps |
| Task-level retry budget | No retry loop exists today; adding one needs scheduler semantics + tests | Failures recorded; user retries explicitly |
| Context size limits on shared namespace | Legacy-compatible persistence is load-bearing for parity | Timeout + workspace confinement already bound python execution |
| Planner-quality benchmark (LLM planning accuracy) | Needs a real-model task set; deterministic planner already golden-tested | 50 golden cases cover execution correctness independently of planner |

No R3 constraint was bypassed to implement the increment: no default-route change, no
B11 contract change, no evidence trust change, no execution boundary weakening.

# R3 Intelligence & Budget Audit — Unified ToolCallBudget

## Budget semantics (code-verified)

`runtime/capability_adapter.py` (read in full): finite `max_calls >= 1`, optional
`allowed_tools` surface, `consume()` raises `CapabilityBudgetExceededError` on exceed or
non-allowlisted tool **before** any state mutation... actually: rejection paths raise
before mutation; accepted calls increment `calls` and append history atomically in
sequence. `remaining` clamps at 0; `snapshot()` exposes max/used/remaining/per-tool
counts/ordered history.

Measured quantity: **scheduled invocations dispatched through `TaskExecutor`** — one
consume per dispatch (structured tool call, react step, or python_repl run). Delegated
sub-analyses run in separate sessions with their own lifecycle (worker jobs), not nested
in this budget; retry counting is moot because `TaskRunner` has no retry loop
(grep-verified: no retry/requeue in `runner.py`/`scheduler.py`).

## Single authority

- Exactly one budget object per V2 stream (`_run_runtime_v2_graph_stream`), attached to
  `session.runtime_tool_budget` and wired once into `build_execution_controller`.
- Dispatch-point accounting replaced the R2.5 executor-level accounting; no double
  counting (structured executor consume removed in `cfa6c79`, diff-verified).
- Alternate construction paths exist: `graph.build_runtime_v2_controller` and
  `financial/runtime_integration.py` build executors without a budget. Finding F-2.
  Bound there: deterministic template plans validated by `AnalysisPlan.validate_steps`
  (`max_steps` default 20, hard cap 200) — bounded, deterministic, not user-loopable.

## Concurrency (Finding F-1 — remediated)

`ToolCallBudget` state is a plain `Counter` + list without synchronization. Production
reachability is safe only because the scheduler enforces `max_running_tasks=1` (sequential
dispatch; verified `runner.py` loop + `initialize_plan(max_running_tasks=1)`). Since the
budget is the declared single enforcement point, it was hardened with a `threading.Lock`
and a concurrent stress test proving no over-admission and exact accounting under 8
parallel consumers (remediation commit, see 10).

## Failure & rejection matrix (executed tests)

| Case | Behavior | Test |
| --- | --- | --- |
| Successful call | consume + recorded result | dispatcher recording tests |
| Budget exhausted | FAILED result, `CapabilityBudgetExceededError`, no executor side effect | `test_task_dispatcher_budget_exhaustion_blocks_tool_side_effect` (python + structured) |
| Non-allowlisted tool | FAILED result, no side effect, budget unchanged | `test_surface_restriction_blocks_non_allowlisted_executor_call`, `test_unified_budget_failure_does_not_count_against_budget` |
| All three executor types | single shared accounting, exact history | `test_unified_budget_counts_all_three_executor_types` |
| Tool failure mid-run | consumes budget (it was executed), recorded FAILED | runner semantics (`_finish_step`) |

Policy is explicit: rejected-before-dispatch calls do not consume; dispatched calls do.

## State & recovery

- Budget lifecycle = one V2 stream; created per stream, never reset mid-run
  (`consume` has no reset path; snapshot read-only).
- Persistence/restore: the budget is not persisted. A restored V2 session replays into a
  NEW stream with a NEW budget. Scope check: restore does not resume the same dispatch
  loop (V1 does not replay V2 progress — enforced by the documented drain-before-rollback
  rule), so restore cannot be used to evade a budget. Accepted limitation: historical
  budget totals are auditable only within a live stream via `session.runtime_tool_budget`.

## Bypass attempts (all failed)

- Direct `TaskExecutor` without budget: possible only for callers that construct their own
  executor (tests / financial path) — production V2 stream always wires the budget
  (F-2 covers the remaining bounded path).
- Legacy adapters: budget sits at the shared dispatcher; legacy ReAct loop is outside V2
  governance by declared scope and bounded by `recursion_limit: 60` + R0a policy.
- Graph resume/cancel: cancel stops pulling new tasks; no new dispatch after cancel
  (verified `_produce_graph_events` + cancel handling in `langgraph_agent.py`).
- Exceptions during accounting: `consume` failures inside the dispatch try-block become
  recorded failed results (no stream crash).

## Intelligence capability verdict (17 items re-checked)

14 VERIFIED, 2 PARTIAL (metric/dimension selection are heuristic-deterministic),
1 GAP deferred by design (automated plan revision). Deferred-with-safe-default items:
retry budget, context size limits, LLM planner benchmark. Intelligence is at
**foundation + orchestration-safety level**, as V1.1 scoped it; not claimed as general
intelligence maturity.

**R3 verdict: ORCHESTRATION SAFETY ESTABLISHED; intelligence foundation verified within
declared scope.**

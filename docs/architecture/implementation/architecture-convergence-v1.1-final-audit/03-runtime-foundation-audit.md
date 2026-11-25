# Runtime Foundation Audit — R1 Convergence, Data, Evidence, State

## Runtime map (code-derived)

- Legacy ReAct runtime: `langgraph_agent.py` ReAct loop (`recursion_limit: 60`), still the
  default route (`RUNTIME_V2_ENABLED` default off) — deliberate V1.1 posture: convergence
  without flipping the production default.
- Runtime V2 bridge: `_run_runtime_v2_graph_stream` → `AnalysisRuntime.initialize_plan` →
  `RuntimeV2Controller` (`scheduler` + `TaskRunner` + `TaskExecutor`) → three governed
  executors (structured / react / python).
- Routing authority: `runtime/runtime_route.py` (RuntimeSelectionPolicy). One source of truth.
- Execution policy authority: `execution_policy.resolve_execution_backend` (single function,
  three callers). One source of truth.
- Budget authority: per-run `ToolCallBudget` at `TaskExecutor` (see 06; alternate bounded
  path finding F-2 recorded).

## Convergence verdict: CONVERGED WITH ADAPTERS

V2 reuses real Legacy tool objects via `StructuredTaskExecutor`; the react executor wraps
the prebuilt agent behind the V2 task interface. These are intentional adapters, not
duplicate policies. No duplicated execution-policy or verification layer found: the same
`VerificationPolicy` and `EvidenceCollector` serve both runtimes through the controller.

## Data governance: VERIFIED

`runtime/asset_resolver.py` (read): every V2 input resolves through one resolver —
file assets (workspace-scoped), acquisition-registered snapshot assets, and sqlite
results all load through `SnapshotStore.load` with no SQL re-execution; failures are
sanitized (`AssetResolutionError` without absolute paths/db details).

## Evidence governance: VERIFIED

- Single collector per V2 stream (`EvidenceCollector().collect(...)` at
  `langgraph_agent.py:1742`); evidence attempted only when verification passed and
  artifacts exist (`runner.py:_review_result`).
- `runtime/verification_policy.py` (read): deterministic, contract-driven checks
  (numeric/group/aggregation/time/schema/artifact); never infers numbers from free text.
- Verification failure converts the result to FAILED and marks the task failed
  (`runner.py:_finish_step`) — unverified results cannot become evidence.

## State & persistence: VERIFIED with accepted limitation

- Scheduler states converge: failures cascade to `skip_dependents` so no task is left
  blocked; cancellation marks cancelled with a recorded outcome.
- Runtime identity is persisted with task snapshots and re-verified on restore
  (`runtime/identity.py`, restore path in Stage C tests).
- Limitation (pre-existing, documented): V2 plan progress is not replayable by the V1
  runtime (documented in `_run_runtime_v2_graph_stream` docstring since R0a Gate) —
  drain-before-rollback rule remains the mitigation. Not a V1.1 regression.

**R1 verdict: CONVERGED WITH ADAPTERS — no duplicate governance authority found.**

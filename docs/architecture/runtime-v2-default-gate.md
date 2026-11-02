# Runtime V2 Default Gate

## Purpose

Runtime V2 may become the default path only after Phase 1 proves the full
file-and-SQL analysis loop. Until then, it remains an opt-in path.

## Current State

- `RUNTIME_V2_ENABLED` defaults to `false`.
- Legacy ReAct is still the default request path.
- Runtime V2 has strong structured tests, but the Phase 1 database and planner
  loop is not implemented yet.

## Gate Types

There are two gates:

1. **File Path Gate (M5a)** — Runtime V2 becomes the default for file-analysis
   scenarios after planner, file tests, and runtime selection tests pass.
2. **Overall Default Gate (M5b/M8)** — Runtime V2 becomes the global default
   only after file and SQLite Golden E2E, evidence, report, session, and
   artifact integration tests pass.

## Overall Default Switch Gate

Runtime V2 may become the global default only when all items below pass:

1. File-analysis golden tests pass through Runtime V2.
2. SQLite `DatabaseDataSource` tests pass.
3. Read-only SQL tool integration tests pass.
4. Golden SQLite E2E tests pass.
5. Evidence links executions, artifacts, and source data.
6. Report claims trace to Evidence and execution.
7. Session, SSE, artifacts, and report integration tests pass.
8. Runtime V2 error paths and recovery paths pass.
9. Plan pause, resume, revise, confirm, and retry tests pass.
10. No unapproved P0/P1 blocker remains.
11. Runtime path, execution records, and fallback reason are observable in logs.
12. Legacy ReAct fallback is disabled or explicitly configured.

## Fallback Policy

Silent fallback is prohibited.

### Required Behavior

- If Runtime V2 fails, return a structured failure with run ID, failing task,
  diagnostic reason, and recovery hint.
- Do not silently call Legacy ReAct and return a successful-looking report.
- Legacy ReAct may run only when `RUNTIME_V2_FALLBACK_ENABLED=true` and the
  failure is classified as infrastructure-safe for fallback.
- Every fallback must record:
  - selected runtime path,
  - original Runtime V2 failure,
  - fallback reason,
  - execution trace,
  - whether the result should be trusted as a Runtime V2 result.

### Test Requirements

Add tests for:

1. Runtime V2 success keeps `runtime_path=runtime_v2`.
2. Runtime V2 ordinary task failure does not call legacy ReAct.
3. Infrastructure fallback occurs only with explicit configuration.
4. Fallback logs and metadata expose the fallback reason.
5. SSE clients receive an explicit failure or fallback event.
6. Session and run history preserve the selected path.

## Rollout Plan

1. Keep Runtime V2 opt-in while M2-M6 are implemented.
2. At M5a, enable it only for file-analysis paths behind a tested gate.
3. At M5b/M8, run the overall gate after SQLite and integration tests pass.
4. Enable Runtime V2 globally only after file and SQLite golden E2E tests pass.
5. Keep `RUNTIME_V2_ENABLED` as an override during stabilization.
6. Remove silent fallback after confidence is established.

# R1 Runtime Foundation Architecture Audit

## Scope

R1 does not introduce another runtime. It establishes the stable foundation required by R2/R2.5
from the existing Runtime V2 implementation.

## Runtime inventory

| Runtime | Entry / implementation | Current role | R1 decision |
| --- | --- | --- | --- |
| Runtime V1 / Legacy ReAct | `langgraph_agent._Session`, API chat/task paths | Product default while `RUNTIME_V2_ENABLED=False` | Preserve behavior; propagate owner identity |
| Runtime V2 | `runtime/context.py::AnalysisRuntime`, plan/task/evidence model | Structural Runtime base | Use as the only future unified Runtime base |
| Scheduler / TaskExecutor | `runtime/scheduler.py`, `runtime/executor.py`, Runtime V2 paths | Governed plan execution | Preserve contracts and existing tests |
| Legacy capability surfaces | Dynamic tools, delegate, repl workflows | Migrate in R2.5 | Do not duplicate into a new module |
| Docker Worker | `execution/worker_backend.py`, `container_manager.py` | Production untrusted execution | Preserve R0a boundary |
| Process Worker | `execution/worker_dispatcher.py` | Explicit dev/test only | Preserve fail-closed policy |
| Data Platform | `data/assets.py`, `data/sources.py`, `data/database.py` | Existing canonical Data contracts | Reuse; add identity/persistence alignment |
| Evidence | `runtime/evidence`, `evidence/registry.py` | R0a-frozen trust lifecycle | Reuse; no new trust authority |
| Persistence | `runtime/context.py`, `runtime/history.py`, SQLite store | Run snapshots/history | Add ownership validation |

## Key baseline facts

- `RUNTIME_V2_ENABLED` remains default off; R1 must not change user-visible routing.
- Runtime V2 already has Plan, Task, ExecutionResult, Artifact, Evidence and Report contracts.
- R0a guarantees all untrusted product execution goes through policy-selected Docker or explicitly
  qualified Process backend, with no silent fallback.
- R0b guarantees API session ownership and canonical workspace paths.
- Data and Evidence governance already use content-addressed assets and fail-closed verification.

## R1 additions

1. `RuntimeIdentityContext` in `langgraph_langchain/runtime/identity.py`.
2. `AnalysisRuntime(owner_id=...)` propagates trusted owner identity into task snapshots.
3. Restore compares persisted owner identity and fails closed on mismatch; ownerless legacy
   snapshots are migrated only when the caller supplies the deployment owner.
4. `WorkerJob.owner_id` is additive audit metadata. It does not grant worker authorization and is
   never consumed as a trust decision.
5. Product job/process job builders propagate owner identity from Runtime/session.
6. API passes the session owner from R0b into `run_analysis_stream`.

## Explicit non-goals

- No change to Runtime default routing.
- No B11 frozen contract change.
- No new DataSource or Evidence contract.
- No removal of Runtime V1 before capability parity work.
- No per-human identity provider; R0b single-owner model remains in force.
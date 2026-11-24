# R0a-1B Executive Summary

## Scope

R0a-1B integrates the Docker worker (built in A1.4.2.2.x and hardened in R0a-1A) into the real
product execution path, replacing stubbed/blocked behavior on the docker backend with full
production execution: job construction, state round-trip, artifact acceptance, and runtime
integration across all three product entry points.

## Git Baseline

- Branch: `codex/r0a-1b-docker-worker-integration` (from `83c8bfa`, the R0a-1A freeze)
- Server worktree: `docker-host:/python/pragrams/data_analysis_agent_r0a1a`
- No B11 frozen contract, no `RUNTIME_V2_ENABLED` default, and no A1.3.2/A1.3.3 freeze was modified.

## What Was Implemented

1. **Product job builder** (`langgraph_langchain/execution/product_job.py`):
   portable `WorkerJob` construction from live session state. DataFrames are staged as parquet,
   ndarrays as `.npy` (`allow_pickle=False`), JSON-safe scalars travel in `job.namespace`.
   **No pickling of container state in either direction** (host RCE prevention).
2. **Worker entrypoint v1.1.0** (`docker/worker/worker_entrypoint.py`): namespace bootstrap,
   data-only state restore/emit (success path only), input mirroring into the writable cwd to
   preserve legacy relative-path semantics, protocol v1 unchanged.
3. **Docker backend artifact/state acceptance** (`worker_backend.py`): user artifacts copied into
   the session workspace; `state__*` files travel through a dedicated state channel and are never
   exposed to the user or written to the workspace.
4. **Three product entries wired**: `langgraph_agent._Session.run_code`, `tools/tool_delegate.py`
   (fresh-copy semantics: no state restore), `execution/python_task_executor.py` (state restored).
5. **Default image** switched to `deepanalyze-worker:1.1.0`; `/health` reports worker backend and
   readiness; compose/env defaults set to docker backend.
6. **E2E test suite** `tests/test_docker_product_e2e.py` (11 real-container tests, no docker mock).

## Verification Summary

- Docker gate suite (backend + container manager + product job): **36 passed**
- Real-container product E2E: **11 passed** (execution, state round-trip, artifacts, timeout,
  non-root isolation, readonly rootfs, no-network, two-job isolation, fail-closed missing image,
  delegate, runtime task)
- Full regression: see `07-test-and-e2e-results.md`.

## Key Decisions

| Decision | Rationale |
| --- | --- |
| Data-only state channel (parquet/npy/json), never pickle | Untrusted container must not be able to inject executable state into the host |
| `state__*` never enters user artifact list | Prevents host-side overwrite of session files via forged state names |
| Input mirroring via symlinks in cwd | Preserves V1 `pd.read_csv("in.csv")` semantics; symlinks are rejected by artifact collection so they cannot be exported |
| Fail closed, no fallback | Backend resolution errors return `[SECURITY_DENIED]`; missing image fails the job |
| process+opt-in remains for conda dev only | Documented known limitation, not a production path |

## Status

PASS_WITH_KNOWN_LIMITATIONS — see `09-risk-register-and-open-issues.md`.

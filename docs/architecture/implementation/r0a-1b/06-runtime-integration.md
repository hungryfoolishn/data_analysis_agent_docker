# R0a-1B Runtime Integration

## Runtime V2 (`python_task_executor.py`)

- Docker branch resolves the backend once per executor instance, builds the product job from the
  `TaskExecutionRequest` (code, namespace, workspace, source, timeout, output-char limit), and
  restores state into the runtime namespace on success.
- `max_artifact_bytes` intentionally uses the product-job default (100 MiB): the V2 request model
  does not carry an artifact-size field; per-job override can be added to the request contract in
  a later hardening commit if needed.
- Cancellation is threaded through `WorkerDispatcher.execute(job, cancellation)`; the docker wait
  loop honors the cancellation event and terminates the container.

## Runtime V1 (`langgraph_agent._Session.run_code`)

- Docker branch is fully integrated including state round-trip; the process branch keeps the
  historical `namespace_updates` merge (process results are trusted host processes by definition
  of the opt-in dev backend).
- `RUNTIME_V2_ENABLED` default remains **off**; nothing in this work package changes runtime
  routing defaults.

## Health

- `/health` exposes `worker_backend` (resolved backend name) and `worker_readiness`
  (backend health check result) for deployment verification before enabling sessions.

## Rollback

- Revert the single implementation commit; the previous behavior (R0a-1A fail-closed stubs) is
  restored because all changes are additive on the docker branch and the process backend is
  untouched.
- Deployment rollback without code revert: set `WORKER_BACKEND=process` (opt-in dev) or keep the
  API at the previous image; the docker path is selected by environment, not by data.

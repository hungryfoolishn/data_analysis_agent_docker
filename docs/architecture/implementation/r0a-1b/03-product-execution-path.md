# R0a-1B Product Execution Path Integration

## Entry 1 — `_Session.run_code` (langgraph_agent.py)

- Docker branch builds a product job from `self.ns` (state staging) and dispatches; on success the
  data-only state is restored into the session namespace before returning stdout.
- Static safety validation (`_validate_code_safety`) runs before dispatch exactly as in V1.
- State staging errors return `[ERROR] Execution state preparation failed: ...` (fail closed).
- Staged state files are removed in a `finally` block after dispatch (`cleanup_staging`).

## Entry 2 — `tools/tool_delegate.py`

- Docker branch dispatches through the same worker path but **does not restore state**:
  delegate analysis keeps fresh-copy semantics (isolated analysis, results returned as artifacts
  and stdout), matching the documented R0a-1A behavior.

## Entry 3 — `execution/python_task_executor.py`

- Docker branch builds the product job from the task request (namespace, workspace, source),
  dispatches, and restores state into the runtime namespace on success.
- The legacy `resolved` variable is None-guarded; legacy path behavior unchanged for the process
  backend.

## API Surface

- `GET /health` now reports `worker_backend` and `worker_readiness` so operations can verify the
  docker backend is live before accepting sessions.
- `docker-compose.yml` default `WORKER_BACKEND=docker`; the API container receives the docker
  socket and the jobs root identity bind; **the worker container never receives either**.
- `.env.example` documents both backend options; `process` remains an explicit opt-in for
  development hosts (see known limitations).

## Protocol Compatibility

- Wire protocol remains v1 (`DockerJobEnvelope` / `DockerResultEnvelope`); the product layer is
  additive: state files are ordinary protocol artifacts with the reserved `state__` name prefix.
- A worker image without the state feature degrades visibly: state restore fails closed with
  `STATE_RESTORE_FAILED` rather than silently dropping variables.

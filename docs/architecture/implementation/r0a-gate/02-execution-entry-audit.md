# R0a Gate Execution Entry Coverage

## Entry Inventory (static scan + call graph)

| Entry | Code path | Handles user/LLM input | Policy gate | Backend | Failure behavior |
| --- | --- | --- | --- | --- | --- |
| `run_code` | `langgraph_agent._Session.run_code` | LLM-generated code | `resolve_execution_backend(UNTRUSTED)` | docker / process(opt-in) | `[SECURITY_DENIED]` on policy error; job failure on backend error; no fallback |
| `tool_delegate` | `tools/tool_delegate.py` | agent-delegated analysis | same | docker / process(opt-in) | tool_error fail closed; delegation depth guard consistent |
| `python_task_executor` | `execution/python_task_executor.py` | runtime task code | same (resolved once, policy-controlled) | docker / process(opt-in) | failed result with `ExecutionPolicyDenied`; no fallback |
| Worker entrypoint | `docker/worker/worker_entrypoint.py` | container-internal | protocol v1 + isolation | docker | `ProtocolReject` fail closed |
| Replay package | `runtime/package.py` (generated script) | persisted analysis steps | **outside host process** (offline artifact) | n/a | consumer-side responsibility; documented |
| IsolatedPythonExecutor | `execution/python_executor.py` | process backend only | reached only via dispatcher process backend | process | dev-only; non-sandbox by explicit opt-in |

## Dynamic Execution Scan Results

- `exec(`: `python_executor.py:189` (process backend), `worker_entrypoint.py` (container),
  `runtime/package.py:117` (generated replay script text — not executed by host).
- `eval(`: none in host paths.
- `compile(`: `langgraph_agent.py:1108` — syntax pre-validation only (result discarded).
- `subprocess`/`Popen`/`os.system`: only the process backend's multiprocessing executor;
  no shell invocation elsewhere.
- LLM code always flows through `run_code`/`delegate`/`python_task_executor` — all gated.

## Scenario Verification (Phase 5.2 matrix)

| Scenario | Expected | Verified by |
| --- | --- | --- |
| Normal trusted task | policy-selected backend | product E2E suites (green) |
| User/LLM code | unified policy | `test_dispatcher_no_silent_fallback` + E2E |
| Unknown origin (no config) | fail closed | `ExecutionBackendNotConfiguredError` path |
| Unknown backend name | fail closed | policy raises; dispatcher wraps as failure |
| Docker failure | no process fallback | `test_docker_unavailable_no_fallback`, `test_missing_image_fails_closed_without_process_fallback` |
| Host inline execution | none exists | static scan + call graph |
| Process backend | explicit dev opt-in only | `UnsafeExecutionBackendDeniedError` without flag |

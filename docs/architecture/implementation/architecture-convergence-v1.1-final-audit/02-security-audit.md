# Security Audit — R0a Execution Trust, R0b Identity/Ownership, Path Boundary

## R0a — Execution trust boundary: VERIFIED

Entry-point closure (grep-verified, no other dynamic-code dispatch sites):

| Entry point | Policy resolution | Dispatcher |
| --- | --- | --- |
| `python_repl` (agent) | `resolve_execution_backend(UNTRUSTED)` (`langgraph_agent.py:1167`) | `WorkerDispatcher` (`:1193`) |
| `delegate_analysis` | `resolve_execution_backend(UNTRUSTED)` (`tools/tool_delegate.py:182`) | `WorkerDispatcher` (`:220`) |
| Runtime V2 python tasks | `resolve_execution_backend(UNTRUSTED)` (`python_task_executor.py:135`) | `WorkerDispatcher` (`:171`) |

Policy semantics (`execution/execution_policy.py`, read in full):

- `WORKER_BACKEND` unset + UNTRUSTED → `ExecutionBackendNotConfiguredError` (fail closed).
- `process` + UNTRUSTED without `ALLOW_UNTRUSTED_PROCESS_EXECUTION=1` → refused.
- Unknown backend value → `UnknownExecutionBackendError`.
- No fallback chain exists anywhere in the resolution code; backend failures surface as
  failed jobs (`worker_dispatcher.py` maps every backend exception to FAILED, never retries
  on another backend).

Container hardening (`execution/container_manager.py:601-611`, read directly):
`NetworkMode=none`, `CapDrop=[ALL]`, `SecurityOpt=[no-new-privileges]`,
`read_only_rootfs=True`, CPU/memory/pids limits, tmpfs-scoped `/tmp`.
Additional verified properties: engine-verified image identity (content-addressed image ID,
`ImageIdentityMismatchError` on mismatch), input staging with per-file SHA-256 + manifest
verification, atomic artifact publish that refuses unsafe filenames, duplicates, and
size mismatches.

## R0b — Identity and ownership: VERIFIED

- Session IDs whitelisted `^[A-Za-z0-9_-]{8,64}$`; owner IDs validated from server-side
  config only (`security.py`, `default_owner_id` never reads request data).
- `_ensure_session_consistency` compares stored `owner_id` before any workspace access;
  `_SessionOwnershipDenied` prevents cleanup on ownership mismatch.
- Restore path (`_load_sessions`) validates owner and rejects unsafe identities without
  deletion; cross-session workspace access resolves through `_resolve_session_workspace`
  with session-id + owner checks (lines 489-560 verified).
- `RuntimeIdentityContext` rejects path-component session ids and is propagated as worker
  envelope metadata, never executed code.

## Path boundary: VERIFIED

`execution/path_safety.py` (read in full): rejects absolute paths, traversal segments,
null/control bytes, non-NFC unicode, mixed separators; `safe_workspace_join` resolves the
real root (refuses symlinked root), walks component-by-component refusing symlink
components, and verifies containment by resolved `relative_to` — not string prefixes.
`is_safe_published_filename` additionally forbids separators in artifact names.

## Adversarial checks (attempted counterexamples)

- Forged ownership: owner comes from env/config; request-supplied owner only accepted
  through `validate_owner_id` then compared against stored value — no forging path found.
- Workspace escape via symlink: refused per-component (code, above).
- Execution-policy bypass: all three dynamic-code sites resolve through the single policy
  function; grep found no other construction of `ProcessWorkerBackend`/`DockerWorkerBackend`
  outside the policy/dispatcher factory (`worker_backend_from_env` exists for operator-level
  backend construction only and is not used by untrusted entry points).
- Budget bypass: see 06 (F-2).

**Security verdict: VERIFIED (R0a), VERIFIED (R0b), VERIFIED (paths).**

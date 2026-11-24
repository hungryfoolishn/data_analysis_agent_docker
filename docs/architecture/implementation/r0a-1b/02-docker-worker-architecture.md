# R0a-1B Docker Worker Architecture (After)

## Call Chain (real product path)

```
Agent tool call
  -> langgraph_agent._Session.run_code / tools/tool_delegate / PythonTaskExecutor.execute
       -> resolve_execution_backend(UNTRUSTED)          # policy gate, fail closed
       -> build_product_worker_job(...)                 # state staging (docker backend only)
       -> WorkerDispatcher.execute(job, cancel_event)
            -> DockerWorkerBackend._execute
                 -> ContainerManager.create_workspace(job_id, envelope, original_files)
                 -> ContainerManager.run_container(spec)   # hardened container
                 -> container: worker_entrypoint.py /workspace/input/job.json
                      -> _build_namespace (identity, helpers, state restore)
                      -> _mirror_inputs_into_cwd (relative-path compatibility)
                      -> exec(code) under redirected stdout/stderr
                      -> _emit_state (success path only)
                      -> _collect_artifacts -> protocol v1 result envelope (stdout)
                 -> collect_exit_code / collect_stdout
                 -> _collect_validate_publish (input integrity first, then output validation)
                 -> _accept_artifacts (user artifacts -> session workspace; state__ -> state channel)
       -> restore_product_namespace_state(ns, result.state_files)   # run_code / python task only
```

## State Model (data-only)

| Type | Host staging | Container input | Container output | Host restore |
| --- | --- | --- | --- | --- |
| DataFrame | `<ws>/.analysis_state/state__df__<var>.parquet` | `/workspace/input/state__df__<var>.parquet` | `state__df__<var>.parquet` | `pd.read_parquet(bytes)` |
| ndarray | `state__np__<var>.npy` | same flat name | same | `np.load(..., allow_pickle=False)` |
| JSON scalar | `job.namespace` | `job.namespace` | `state__json.json` | merged into ns |
| callables/modules/classes | never serialized | — | — | — |

- Restore/emit decode with data-only decoders; a malformed or unexpected state artifact raises
  `ProductStateError` and the result is **rejected, not partially applied**.
- The entrypoint restores state variables by exact prefix parsing
  (`state__df__` / `state__np__`, 4-char type marker); mismatched names cannot shadow helpers.
- Output state is emitted only on success; failed jobs do not mutate host state.

## Isolation Layers

1. Per-job workspace under `/tmp/deepanalyze-worker-jobs/<job_id>`; input copied by content
   (hashes recorded in the manager manifest and re-verified before accepting outputs).
2. Container hardening (see `04-docker-security-validation.md`).
3. Artifact validation before publish: extension allowlist, size/count limits, hash verification,
   symlink rejection, atomic all-or-nothing publish into `collected/`.
4. Host-side `safe_workspace_join` bounds every workspace write.

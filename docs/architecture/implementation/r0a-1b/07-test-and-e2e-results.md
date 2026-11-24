# R0a-1B Test and E2E Results

All results below are from real executions on `docker-host` in conda env `analysis`
(worktree `/python/pragrams/data_analysis_agent_r0a1a`, branch `codex/r0a-1b-docker-worker-integration`).

## 1. Product Job Unit Tests

```
python -m pytest tests/test_product_job.py -q
```

16 passed — covers DataFrame/ndarray/JSON staging, reserved-name filtering, source-file hashing,
staging cleanup, fail-closed staging errors, data-only restore (incl. rejection of unexpected
state artifacts), and state/user artifact separation.

## 2. Docker Gate Suite

```
python -m pytest tests/test_docker_worker_backend.py tests/test_container_manager.py tests/test_product_job.py -q
```

**36 passed** (2 warnings: pre-existing unknown `pytest.mark.docker` marker).

## 3. Real-Container Product E2E (new)

```
python -m pytest tests/test_docker_product_e2e.py -q
```

**11 passed** (~74 s wall, real daemon, no docker mock):

| # | Test | Verifies |
| --- | --- | --- |
| 1 | basic_execution_runs_in_container_not_host | run_code docker path, container PID namespace, session df round-trip |
| 2 | namespace_state_roundtrip_across_calls | df mutation restored to host; df2 carried to next call |
| 3 | artifact_generation_is_accepted_into_workspace | generated file lands in session workspace |
| 4 | timeout_is_enforced_by_the_container | container killed at timeout; timed_out status |
| 5 | source_file_is_staged_and_readable | source staged into isolated input |
| 6 | container_runs_non_root_with_isolated_host_view | UID 10001, host /tmp sentinel invisible, readonly rootfs, no egress |
| 7 | container_runtime_config_is_enforced | live inspect: user/network/caps/pids/memory/readonly |
| 8 | two_jobs_cannot_access_each_others_workspace | cross-job isolation |
| 9 | missing_image_fails_closed_without_process_fallback | fail closed, no fallback |
| 10 | delegate_analysis_runs_in_container | delegate entry, fresh-copy semantics |
| 11 | runtime_python_task_runs_in_container | Runtime V2 executor entry + relative source read |

## 4. Full Regression

```
python -m pytest tests/ -q
```

Result: see final run summary in `R0A_1B_COMPLETION_REPORT.md` (recorded after completion).
Baseline expectation (R0a-1A freeze): 1899 passed / 2 failed (dashboard, pre-existing frozen
baseline failures reproduced at clean `4a9b254`) / 13 skipped.

## 5. Defects Found and Fixed During E2E Bring-Up

| Defect | Root cause | Fix |
| --- | --- | --- |
| State input staging failed | backend resolved `relative_path` (flat container name) against workspace root; state files live in `.analysis_state/` | backend resolves `artifact_type == "state"` sources from `.analysis_state/` |
| State variable restored as `_df` | entrypoint var-name slice skipped 3 chars instead of 4 (`state__df__` = 11 chars) | slice offset corrected for df/np |
| Isolation probe wrote `/etc/...` | test hit the host-side static safety validator before dispatch | probe path changed to `/usr/...` (tests the rootfs layer, not the validator) |
| Rootfs probe catch type | read-only rootfs raises `OSError` errno 30, not `PermissionError` | test catches `OSError` |
| V2 docker job crashed | executor referenced nonexistent `request.max_artifact_bytes` | use product-job default |
| df state never emitted | input mirroring symlinked `state__*` into cwd, shadowing the emit target (write to read-only input failed silently) | mirroring skips `state__*` |

### Final Full Regression (recorded after completion)

```
python -m pytest tests/ -q
```

**1916 passed / 2 failed / 13 skipped** (14 min 52 s, real docker suites included).

- The only 2 failures are the frozen-baseline Dashboard failures
  (`test_dashboard.py::test_individual_metrics_endpoints`,
  `test_dashboard.py::test_dashboard_display_format`), previously reproduced unchanged at the
  clean R0a-1A baseline; they are unrelated to this work package.
- The first full-suite run (before the final entrypoint fix) additionally failed 4
  `tests/test_docker_worker_entrypoint.py` subprocess tests because the rewritten entrypoint
  referenced the absolute `/workspace/input` path on the host. Fix: the entrypoint now derives
  its input directory from the job envelope location (production containers still resolve
  `/workspace/input`); the final full-suite run is green except the 2 baseline failures.

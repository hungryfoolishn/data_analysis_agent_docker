# R0a Gate Docker Worker Real Execution Validation

## Preconditions (verified live)

- Daemon 27.1.1 reachable; `deepanalyze-worker:1.1.0` present (image ID `1ce20d25b8d8`);
  protocol v1; entrypoint `/app/worker_entrypoint.py`; UID/GID 10001.
- Runtime config inspect-tested: NetworkMode none, ReadonlyRootfs, CapDrop ALL,
  no-new-privileges, PidsLimit 128, Memory 512MiB, NanoCpus 1e9, tmpfs /tmp 256m.

## Product Entry E2E (real daemon, no mocks)

| Path | Suite | Result |
| --- | --- | --- |
| A — run_code | `tests/test_docker_product_e2e.py::TestRunCodeDockerE2E` | green |
| B — tool_delegate | `::TestOtherProductEntriesDockerE2E::test_delegate_analysis_runs_in_container` | green |
| C — python_task_executor | `::TestOtherProductEntriesDockerE2E::test_runtime_python_task_runs_in_container` | green |
| Gate E2E matrix | `tests/test_r0a_gate_e2e.py` | 9 green |

Covered on the real chain: code execution, DataFrame/ndarray/JSON state round-trip, artifact
generation + acceptance, state restore/writeback, timeout, failure paths, missing image fail
closed, two-job isolation.

## Security Negative Matrix (Phase 6.4)

| # | Negative probe | Observed | Evidence |
| --- | --- | --- | --- |
| 1 | Host /tmp sentinel inside container | not visible | product E2E `HOST_SENTINEL= False` |
| 2 | RootFS write | denied (OSError 30, readonly) | product E2E |
| 3 | Network outbound (8.8.8.8:53) | blocked (NetworkMode none) | product E2E |
| 4 | Container UID | 10001 non-root | product E2E + inspect |
| 5 | Capabilities | ALL dropped | inspect test |
| 6 | PID/CPU/Memory limits | enforced | inspect test |
| 7–8 | Two jobs / workspaces isolated | yes | product E2E + Gate E2E-09 |
| 9 | Artifact cross-job pollution | no | Gate E2E-N18 (`EXISTS= False`) |
| 10 | State cross-job pollution | no | per-job workspace + state channel |
| 11 | Docker daemon missing | fail closed | `test_docker_unavailable_no_fallback` |
| 12 | Image missing | fail closed | `test_image_missing_fails_closed` |
| 13 | Engine failure (create refused) | fail closed | `test_create_failure_propagates_as_failure` |
| 14 | Worker crash | no pseudo success | EXECUTION_ERROR propagation |
| 15 | Timeout | no pseudo success | product E2E timeout test |
| 16 | Protocol garbage stdout | contract invalid, fail closed | `test_e_invalid_protocol_rejected` |
| 17 | Artifact over-limit / bad type | rejected before publish | backend validation chain |
| 18 | Invalid artifact into trusted consumption | blocked | evidence requires verified + registry chain |
| 19 (new) | Container image identity mismatch | fail closed (`image_identity_mismatch`) | Gate `test_e2e_n20_*` |
| 20 (new) | Pinned digest mismatch | fail closed before creation | Gate `test_digest_pinning_wrong_digest_fail_closed` |

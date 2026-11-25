# R0a Gate Golden Matrix Results

## Positive matrix (Phase 14.1)

| ID | Scenario | Result | Evidence |
| --- | --- | --- | --- |
| E2E-01 | Basic user code execution | PASS | `test_docker_product_e2e.py::test_basic_execution_runs_in_container_not_host` |
| E2E-02 | DataFrame analysis → parquet artifact | PASS | `test_artifact_generation_is_accepted_into_workspace` + Gate E2E-03 step 1 |
| E2E-03 | Trusted evidence lifecycle (exec→artifact→evidence→verify→persist→restore→consume) | PASS | `test_r0a_gate_e2e.py::test_e2e03_trusted_evidence_lifecycle` |
| E2E-04 | Session restore trust downgrade | PASS | `test_evidence_trust_boundary.py::TestRestoreTrustBoundary` + runtime revalidation suites |
| E2E-05 | Runtime V2 real execution | PASS | `test_runtime_python_task_runs_in_container` |
| E2E-06 | Agent delegate | PASS | `test_delegate_analysis_runs_in_container` |
| E2E-07 | Python task executor state roundtrip | PASS | product E2E + Gate E2E-03 |
| E2E-08 | Multiple jobs isolation | PASS | `test_two_jobs_cannot_access_each_others_workspace` |
| E2E-09 | Multiple sessions isolation | PASS | Gate `test_e2e09_two_sessions_isolation` |
| E2E-10 | Verified report consumption (+ unverified denial) | PASS | Gate E2E-03 consumption assertions |

## Negative matrix (Phase 14.2)

| ID | Scenario | Result | Evidence |
| --- | --- | --- | --- |
| N01 | Missing docker → fail closed | PASS | `test_docker_unavailable_no_fallback` |
| N02 | Missing image → fail closed | PASS | `test_image_missing_fails_closed` |
| N03 | Worker failure → no success/evidence | PASS | EXECUTION_ERROR propagation tests |
| N04 | Invalid artifact → no consumption | PASS | backend validation chain tests |
| N05 | Evidence creation without hash → rejected | PASS | `test_operation_evidence_requires_content_hash`, sqlite variant |
| N06 | Injected verified status → rejected/downgraded | PASS | `test_register_never_preserves_caller_verified` |
| N07 | Session restore verified injection → downgraded | PASS | `test_tampered_state_file_cannot_inject_verified` |
| N08 | Tampered content → integrity failure | PASS | asset restore STALE tests (A1.4.1) |
| N09 | Forged verification ID → rejected | PASS | finding lineage gate tests |
| N10 | Cross-session replay → rejected/downgraded | PASS | per-workspace persistence + restore downgrade |
| N11 | Validator missing → fail closed | PASS | `test_verify_without_validator_fails_closed` |
| N12 | Validator exception → fail closed | PASS | `test_validator_exception_never_promotes` |
| N13 | Validator UNKNOWN → fail closed | PASS | `test_validator_unknown_results_reject` |
| N14 | State artifact → never evidence | PASS | Gate `test_e2e_n14_state_never_evidence` |
| N15 | Docker failure → no process fallback | PASS | `test_dispatcher_no_silent_fallback` |
| N16 | Metadata-only finding → no trusted claim | PASS (FIXED) | Gate `test_e2e_n16_metadata_only_finding_not_verified` |
| N17 | Artifact hash mismatch → rejected | PASS | backend hash-verification tests |
| N18 | Cross-job artifact replay → rejected | PASS | Gate `test_e2e_n18_cross_job_artifact_replay_rejected` |
| N19 | Execution provenance tampering | PASS | manager manifest identity/integrity checks |
| N20 | Worker image identity mismatch → fail closed | PASS (FIXED) | Gate `test_e2e_n20_image_identity_mismatch_fail_closed` + digest pinning tests |

All 30 matrix items executed against real code; docker rows against the real daemon. No mock
substituted for any core chain. Nothing blocked.

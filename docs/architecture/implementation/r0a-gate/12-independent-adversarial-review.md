# R0a Gate Independent Adversarial Review

Reviewed as a fresh adversarial pass over `a802571` plus the Gate fixes (Fix A: metadata-only
finding trust promotion; Fix B: image identity anchoring). Evidence is either a code path, a real
Docker E2E test, a targeted unit test, or an explicit limitation. No conclusion is based only on
the implementation report.

| # | Challenge | Result | Evidence |
| ---: | --- | --- | --- |
| 1 | Is any dynamic execution entry missing from the inventory? | SAFE | Implementation inventory scanned host-side `exec`/`eval`; all three product entries are documented in `01-execution-entry-coverage.md`. The only uncovered `exec` is in a generated standalone replay package, explicitly outside the host process. |
| 2 | Can ExecutionPolicy be bypassed? | SAFE | All product entries call `resolve_execution_backend(UNTRUSTED)`; unknown/empty/unqualified backends raise instead of selecting a backend. |
| 3 | Can WorkerDispatcher be bypassed? | SAFE | 1B product paths construct `ProductWorkerJob` and dispatch through `WorkerDispatcher`; real-container product E2Es exercise the same path. |
| 4 | Is there host inline execution? | SAFE | The only host-side dynamic executor is `execution/python_executor.py::IsolatedPythonExecutor`, reachable through the explicitly opt-in process backend, not a session inline path. |
| 5 | Can Docker failure fall back to Process? | SAFE | `test_docker_worker_backend.py::test_docker_unavailable_no_fallback`; `test_docker_product_e2e.py::test_missing_image_fails_closed_without_process_fallback`. |
| 6 | Can Process backend execute untrusted code without authorization? | SAFE | `execution_policy.py` requires `WORKER_BACKEND=process` **and** `ALLOW_UNTRUSTED_PROCESS_EXECUTION=1` for `UNTRUSTED`; otherwise it raises a policy error. |
| 7 | Can worker configuration be injected through the job? | SAFE | Worker accepts exactly one envelope path, validates kind/fields/size and clears/rebuilds the environment from `SAFE_ENV`; untrusted code gets only the bounded analysis namespace. |
| 8 | Can the worker image be silently replaced? | FIXED | Fix B resolves the tag to a content-addressed image ID before creation, optionally enforces `DEEPANALYZE_WORKER_IMAGE_DIGEST`, and fails closed on mismatch: `test_e2e_n20_image_identity_mismatch_fail_closed`, `test_digest_pinning_wrong_digest_fail_closed`. |
| 9 | Does the actual executing image match the expected image? | FIXED | `run_container()` verifies the created container's `Image` against the resolved ID; `test_image_id_recorded_and_matches_engine` checks the engine identity and `WorkerResult.image_id`. |
| 10 | Can container isolation be overridden by caller configuration? | SAFE | `run_container()` hard-codes `NetworkingConfig` with no endpoints, read-only rootfs, fixed `/workspace/input:ro` and `/workspace/output` binds; no caller-provided HostConfig merge path exists in the product dispatcher. |
| 11 | Can a host mount escape its intended boundary? | SAFE | Mounts are fixed to the manager-created per-job input/output/tmp directories; `path_safety` and manifest identity checks reject traversal, reuse and escape during staging, publication and cleanup. |
| 12 | Is any container network open? | SAFE | `container_manager.py` creates the container with `EndpointsConfig: {}` (network disabled); the worker entrypoint and product E2Es assume no network. |
| 13 | Can artifacts escape the workspace? | SAFE | Worker rejects dotfiles, symlinks, over-deep paths, disallowed extensions and oversized files; host publication validates names and content hashes before copying into the job workspace. |
| 14 | Can a job access another job's files? | SAFE | Workspaces are manager-owned per job and reuse is forbidden; `test_two_jobs_cannot_access_each_others_workspace` and Gate E2E-09 cover job/session isolation. |
| 15 | Can crash or timeout forge success? | SAFE | Worker emits a protocol result only after the job returns; exceptions/timeout surface as `EXECUTION_ERROR`; tests cover failure propagation with no artifact/evidence. |
| 16 | Does artifact validation really occur? | SAFE | Two independent layers: worker `_collect_artifacts` policy and host `publish_artifacts` extension/size/hash/path validation. |
| 17 | Is there a validation bypass? | SAFE | Artifact bytes are the bytes hashed and published; no path accepts caller-claimed status/hash without content. Cross-job replay is rejected by Gate E2E-N18. |
| 18 | Is hash bound to real content? | SAFE | `publish_artifacts()` hashes returned bytes and rejects mismatches before writing; evidence registration also requires content hash. |
| 19 | Are path, symlink and traversal attacks handled? | SAFE | Worker rejects all symlink members; `safe_workspace_join`/`is_safe_published_filename` and cleanup identity checks prevent traversal and manifest substitution. |
| 20 | Can an invalid artifact enter Evidence? | SAFE | Invalid/mismatched artifacts fail before publication or registration; `test_docker_product_e2e.py` and backend hash tests cover rejection. |
| 21 | Can Evidence be created directly as verified? | SAFE | New evidence defaults unverified; registry `register` never preserves caller `verified` (`test_register_never_preserves_caller_verified`). |
| 22 | Can the evidence registry be bypassed? | SAFE | Consumers require a runtime-recorded `VerificationResult` linked to the evidence; findings/report paths do not accept a status assertion alone. |
| 23 | Can a VerificationResult ID be forged? | SAFE | `finding_builder` cross-checks the recorded result, passed flag and evidence linkage; Gate E2E-03 covers a valid linkage and denial of unverified support. |
| 24 | Does missing validator fail closed? | SAFE | `test_verify_without_validator_fails_closed` (Golden N11). |
| 25 | Does validator exception fail closed? | SAFE | `test_validator_exception_never_promotes` (Golden N12); UNKNOWN results are also rejected (N13). |
| 26 | Does restore grant trust? | SAFE | Restored records reset/downgrade trust and are revalidated; `test_restore_record_resets_verified_state`, `test_restored_verified_evidence_is_downgraded`. |
| 27 | Can persistence inject trust? | SAFE | Tampered persisted state cannot inject verified (`test_tampered_state_file_cannot_inject_verified`); Gate E2E-03 repeats live revalidation after restore. |
| 28 | Is hash mistaken for truth/authenticity? | SAFE | Hash is integrity only; trust additionally requires registry validation, execution lineage and live revalidation. This distinction is documented in `07-evidence-lifecycle-persistence-restore.md`. |
| 29 | Can domain evidence bypass internal collection? | SAFE | Domain/canonical adaptation remains unverified until Runtime content verification; no consumer treats the adapted record as trusted. |
| 30 | Do canonical and domain trust conflict? | SAFE | Canonical evidence is separately registered/verified; domain provenance remains attached rather than inheriting trust. |
| 31 | Can State impersonate Evidence? | FIXED/SAFE | Gate `test_e2e_n14_state_never_evidence` proves persisted state stays in the state channel and cannot enter the evidence registry. |
| 32 | Can state restore contaminate another session? | SAFE | State and evidence persistence are per-session/per-workspace; restore revalidates content and session context, with downgrade on mismatch. |
| 33 | Can state restore contaminate another job? | SAFE | Docker state is staged into a manager-owned per-job workspace with manifest identity checks; cross-job replay is rejected (Gate E2E-N18). |
| 34 | Is state written back for a failed job? | SAFE | Worker `_emit_state()` runs only on the success path; failed jobs emit an error envelope with no state writeback. |
| 35 | Is serialization/deserialization dangerous? | KNOWN LIMITATION | State formats are limited to parquet/npy/JSON and `np.load` does not enable pickle, but parser vulnerabilities in pandas/PyArrow/NumPy are outside R0a's ability to eliminate. No arbitrary-pickle channel exists. |
| 36 | Can Finding bypass trust? | SAFE | Finding consumption requires verified evidence plus a linked passed verification; metadata-only promotion was removed by Fix A. |
| 37 | Can Insight bypass trust? | SAFE | `report_claims` lowers confidence when supporting evidence is not verified; verified boost requires the registered status. |
| 38 | Can Report bypass trust? | SAFE | `report_validator` collects report numbers only from verified evidence and requires verification linkage. |
| 39 | Can Runtime turn unverified evidence into a trusted claim? | SAFE | Gate E2E-03 explicitly asserts both acceptance of properly verified evidence and rejection of unverified evidence. |
| 40 | Does B11 Multi-Agent elevate trust? | SAFE | Multi-agent evidence references are not trust grants; B11 contracts remain unchanged and regression suites stay green. |
| 41 | Does the API expose unverified trust as trusted? | SAFE | The API health surface reports worker readiness only; no endpoint converts evidence/finding status into an unconditional trusted claim. |
| 42 | Does metadata-only Finding become a trusted claim? | FIXED | Fix A keeps `evidence_existence` traceability but leaves evidence unverified; Gate `test_e2e_n16_metadata_only_finding_not_verified` and affected finding/report suites pass. |
| 43 | Are key links real end-to-end? | SAFE | Gate E2E-03 runs real Docker execution, artifact publication, evidence, verification, persistence, restore and consumption in one flow with no core mock. |
| 44 | Are tests only component-level without product entry? | SAFE | `test_docker_product_e2e.py` exercises all three product entries against the real daemon in addition to component suites. |
| 45 | Are security assumptions missing negative tests? | SAFE | The Gate Golden Matrix includes 20 negative scenarios (N01-N20), including fallback, tampering, injection, replay, trust downgrade and image identity failure. |
| 46 | Do tests contaminate each other? | SAFE | E2E tests use per-test `tmp_path`, per-session workspaces and explicit cleanup; no shared mutable workspace was used in Gate tests. |
| 47 | Can security config differ from runtime config? | SAFE | Isolation is set in the same `run_container()` call that executes the job; image identity is verified on the created object rather than trusting separate configuration. |
| 48 | Are results reproducible across environments? | KNOWN LIMITATION | Results require the Linux Docker daemon and `deepanalyze-worker:1.1.0`; digest pinning is operator-controlled, and different host/user namespaces can affect isolation guarantees. The baseline environment is documented. |
| 49 | Did Docker E2E catch issues missed by unit tests? | FIXED/SAFE | Yes: Fix A was a V1 integration trust issue and Fix B closed resolution/creation race plus engine identity provenance; both now have targeted unit and E2E regression. |
| 50 | Which key boundaries remain unverifiable in R0a? | KNOWN LIMITATION | See completion report: registry signature/provenance, forced digest pinning, host kernel/container runtime hardening, and downstream consumer storage security are outside this Gate. None currently breaks the implemented trust boundary. |

## Result

- BLOCKER: 0
- P0/P1 unfixed: 0
- FIXED: #8, #9, #31, #42, #49
- KNOWN LIMITATION: #35, #48, #50 (explicit, non-core, documented)
- All other reviewed items: SAFE with code or test evidence.

No adversarial item requires blocking the R0a Gate.

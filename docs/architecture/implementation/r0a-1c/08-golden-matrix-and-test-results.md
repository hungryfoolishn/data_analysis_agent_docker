# R0a-1C Golden Matrix and Test Results

## Golden Trust Matrix (implemented scenarios)

| Scenario | Create | Validate | Persist | Restore | Consume | Expected trust | Test |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Valid canonical evidence | C3 | references pass | runtime state | revalidate | verified | PASS | `test_evidence_trust_boundary.py` + restore suites |
| Caller-injected verified | — | register resets | — | — | — | reference_validated (never verified) | `test_register_never_preserves_caller_verified` |
| Missing content validator | — | verify | — | — | — | failed + raise | `test_verify_without_validator_fails_closed` |
| Validator exception | — | verify | — | — | — | no promotion | `test_validator_exception_never_promotes` |
| Validator FAIL | — | verify | — | — | — | failed + raise | `test_validator_failures_reject_and_mark_failed` |
| Validator UNKNOWN | — | verify | — | — | — | failed + raise | `test_validator_unknown_results_reject` |
| User-injected verified (session state) | C8 | — | JSON | restore | — | unverified | `test_tampered_state_file_cannot_inject_verified` |
| Restored verified (session) | — | — | JSON | downgrade | blocked | unverified | `test_restored_verified_evidence_is_downgraded` |
| Restored failed (session) | — | — | JSON | downgrade | blocked | unverified | `test_restored_failed_evidence_is_unverified` |
| Hash-less operation evidence | C1 | guard | — | — | — | rejected | `test_operation_evidence_requires_content_hash` |
| Hash-less sqlite evidence | C2 | guard | — | — | — | rejected | `test_sqlite_evidence_requires_content_hash` |
| Caller trust assignment (sqlite) | C2 | API | — | — | — | parameter removed | `test_sqlite_evidence_trust_is_not_caller_assignable` |
| Canonical restore reset | C3 | — | — | restore_record | — | unverified | `test_restore_record_resets_verified_state` / `test_registry_canonical_restore_resets_trust` |

All real production-reachable evidence types (analysis_operation, sqlite_query, legacy, runtime
EvidenceItem, financial domain) are covered by a combination of the new suite and the existing
per-type suites (`test_sqlite_sql_tool.py`, `test_b8_integration.py`, macro integration, financial
golden, runtime_v2 restore integrity).

## Test Execution Record

Environment: server `docker-host`, conda `analysis` (Python 3.10.18), branch
`codex/r0a-1c-evidence-trust-boundary`, HEAD `4878adc` + R0a-1C changes.

| Suite | Command | Result |
| --- | --- | --- |
| New trust boundary suite | `pytest tests/test_evidence_trust_boundary.py -q` | 19 passed |
| Evidence/sqlite/persistence affected set | `pytest tests/test_sqlite_sql_tool.py tests/test_session_persistence.py tests/test_evidence_binding.py tests/test_data_acquisition.py tests/test_b8_integration.py tests/test_b11_1_runtime_closure.py tests/test_phase1_runtime_integration.py tests/test_runtime_state_restore_evidence_views.py tests/runtime_v2/test_restore_integrity.py -q` | 128 passed (1 pre-existing failure fixed by T1, see below) |
| Financial/report/validator affected set | `pytest tests/test_financial_golden.py tests/test_financial_domain_workflow.py tests/test_financial_china_pdf_runtime.py tests/test_report_rebuild.py tests/test_validator_e2e.py tests/test_verification_commit6.py tests/test_evidence_commit7.py tests/test_analysis_package.py tests/test_platform_runtime.py tests/test_runtime_runner_hooks.py -q` | 108 passed |
| Macro integration + runtime_v2 | included in affected set run | green |
| Full regression | `pytest tests/ -q` | see completion report (final run) |

## Defects Found During Testing

| Defect | Attribution | Resolution |
| --- | --- | --- |
| `test_restored_runtime_artifacts_visible_to_evidence_registry` failed after R3 | test constructed a verified evidence record from a **non-existent** output file (no content hash) — exactly the bypass R0a-1C closes | test updated (T1) to write the output file before registration, preserving its original intent (post-restore artifact visibility) |

### Final Full Regression (recorded after completion)

```
python -m pytest tests/ -q
```

**1935 passed / 2 failed / 13 skipped** (15 min 09 s). The 2 failures are the frozen-baseline
Dashboard failures. Net change vs R0a-1B freeze (1916/2/13): +19 tests, no new failures.

### Harness Hardening Note

The benchmark ground-truth harness previously attached "production operation evidence" to a
synthetic asset whose path (`benchmark://...`) did not exist, i.e. verified evidence without a
content identity. Under R0a-1C the harness now registers the metric output with a dataframe
content hash (same binding as `register_dataframe`), keeping the collector's content-binding
rule strict while fixing the harness rather than weakening it.

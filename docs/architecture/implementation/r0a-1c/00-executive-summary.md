# R0a-1C Executive Summary

## Scope

R0a-1C hardens the Evidence trust boundary: construction, verification, persistence, restore and
consumption of evidence must not allow trust injection, trust bypass, or trust elevation outside
the controlled authority.

## Baseline

- Branch `codex/r0a-1c-evidence-trust-boundary` created from `4878adc` (R0a-1B freeze) in the
  server worktree `/python/pragrams/data_analysis_agent_r0a1a`.
- Working tree was clean; no user modifications were overwritten.

## Audit Result (Phase 1)

The evidence system has **two coexisting trust planes** (confirmed by code, not by docs):

1. **Canonical plane** — `CanonicalEvidence` + `EvidenceRegistry` (Runtime V2):
   default `unverified`; `register` forces `reference_validated`; `verify` is the single
   promotion point and fails closed (missing validator → failed, validator error → no
   promotion, errors → failed); `restore_record` resets to `unverified`. This plane was
   already converged (A1.4.1 restore-integrity work) and is retained as the Trust Authority.
2. **Domain records plane** — `OperationEvidence` / `SqliteQueryEvidence` / runtime `EvidenceItem`
   / `FinancialEvidence`: deterministic internal collectors whose `verified` value is
   *earned by construction* from internal execution results, never from user/LLM input.
   These records are adapted to `unverified` canonical evidence before registry verification.

## Fixes Implemented (Phase 3–6)

| Fix | File | Change |
| --- | --- | --- |
| R1 (restore boundary, P0) | `session_persistence.py` | `deserialize_findings` now **downgrades all restored evidence** to `unverified` and drops `verification_result_id`. A tampered state file can no longer inject `verified` evidence into a resumed session. |
| R2 (creation API hardening) | `evidence/sqlite.py` | Removed the caller-suppliable `validation_status` parameter from `collect_sqlite_query_evidence`; trust status is produced by the deterministic collector only. `verified` requires an output content hash. |
| R3 (content binding) | `evidence/operations.py` | `collect_operation_evidence` now requires a **content hash** on the registered output asset (previously a bare file location sufficed), closing a `verified`-without-content-identity bypass. |
| T1 (test correction) | `tests/test_runtime_state_restore_evidence_views.py` | Output file now written before registration, matching the production write-then-register flow (verified evidence requires content binding). |

## Verification

- New regression suite `tests/test_evidence_trust_boundary.py`: **19 passed** (creation defaults,
  registry authority, fail-closed verification, restore downgrade, tamper injection).
- Affected suites (sqlite tool, session persistence, evidence binding, B8/B11, macro integration,
  runtime_v2 restore integrity, financial, report/validator): **all green** (183 + 108 passed).
- Full regression: see `08-golden-matrix-and-test-results.md`.

## Status

PASS_WITH_KNOWN_LIMITATIONS — limitations in `10-risk-register-and-r0a-gate-handoff.md`
(worker image digest anchoring, metadata-only finding compatibility path, execution-environment
provenance fields).

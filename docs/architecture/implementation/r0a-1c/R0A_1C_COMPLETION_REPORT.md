# R0a-1C Completion Report

## A. Task

R0a-1C — Evidence Trust Boundary Hardening: converge evidence trust to a single authority, close
creation/restore/consumption bypasses, and prove the boundary with security regression tests.

## B. Git Baseline

- Branch `codex/r0a-1c-evidence-trust-boundary` from `4878adc` (R0a-1B freeze).
- Implementation in server worktree; final code synchronized and committed per workflow.

## C. Audit Findings (Phase 1, code-verified)

- Two trust planes exist: canonical (`EvidenceRegistry` = Trust Authority, already converged) and
  domain records (deterministic collectors, earned-by-construction).
- **One real bypass found and fixed**: V1 session restore trusted persisted
  `verification_status` verbatim (`session_persistence.deserialize_findings`).
- **Two hardening gaps closed**: caller-assignable `validation_status` on the sqlite collector;
  hash-less verified operation evidence (location-only binding).
- All other suspected bypasses (default verified in factories, registry bypass, SQLite write
  bypass, validator fail-open, Docker success→evidence, state→evidence) were checked against real
  code and found absent or already fail-closed.

## D. Implemented Fixes

R1 (P0): restore trust downgrade (`session_persistence.py`);
R2: sqlite collector trust param removed + hash required (`evidence/sqlite.py`);
R3: operation evidence content-hash requirement (`evidence/operations.py`);
T1: restore-evidence-views test corrected to production write-then-register flow.

## E. Verification Evidence

- New suite `tests/test_evidence_trust_boundary.py`: 19 passed.
- Affected suites: 128 + 108 passed (sqlite/session/evidence/B8/B11/macro/runtime_v2/financial/
  report/validator/platform/runner).
- Full regression: 1935 passed / 2 failed / 13 skipped (`tests/ -q`, 910 s). The only 2 failures are the frozen-baseline Dashboard failures (reproduced unchanged at `4a9b254`). Net +19 tests vs R0a-1B (1916), zero regressions. One intermediate run exposed 7 harness failures (benchmark/golden ground truth used hash-less synthetic assets); resolved by content-binding the benchmark asset (dataframe hash), preserving R3 semantics.

## F. Gate Status

| Gate | Result |
| --- | --- |
| Single trust authority (registry) | PASS |
| No user/LLM trust assignment | PASS |
| Creation defaults safe / earned | PASS |
| Restore never elevates | PASS (R1 + existing revalidation) |
| Consumption enforced beyond status field | PASS |
| State ≠ evidence | PASS |
| Docker success ≠ evidence trust | PASS |
| Security regression suite | PASS (19 tests) |
| Worker image digest anchoring | DEFERRED (R0a Gate) |
| Metadata-only finding path strengthening | DEFERRED (R0a Gate) |

**Final status: PASS_WITH_KNOWN_LIMITATIONS**

## G. R0a Gate Handoff

See `10-risk-register-and-r0a-gate-handoff.md` (K-1..K-5 + remaining Gate work).

## H. Rollback

Single-commit revert; no data migrations.

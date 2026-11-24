# R0a-1C Risk Register and R0a Gate Handoff

## Known Limitations

| ID | Risk | Severity | Disposition |
| --- | --- | --- | --- |
| K-1 | Worker image identity anchored by tag (`deepanalyze-worker:1.1.0`), digest anchoring pending | Medium | No digest is recorded or claimed anywhere; R0a Gate item: record real image digest/container image ID in worker provenance once anchoring exists |
| K-2 | `tool_record_finding` metadata-only compatibility path promotes evidence with an existence-only check (sessions without a V2 scheduler) | Medium (V1 compat) | Retained for B11/V1 compatibility; disclosed; candidate for R0a Gate strengthening |
| K-3 | Evidence execution-environment provenance (worker backend/image/protocol) not recorded on evidence records | Low | Follow-up contract work; no speculative fields added |
| K-4 | Domain evidence records (`OperationEvidence`/`SqliteQueryEvidence`) carry `verified` as earned-by-construction claims outside the canonical registry | Low (by design) | Adapters convert to `unverified` before registry; the two planes are documented and test-locked |
| K-5 | Evaluation/golden harnesses construct evidence outside runtime flows | Low | Offline-only; no production reach |

## Regression Watchlist

- `session_persistence` restore now downgrades evidence — sessions resuming with previously
  verified findings will re-run analysis steps; report gates still accept findings with unverified
  evidence textually in V1, but provenance is no longer spoofable.
- `collect_operation_evidence` now requires content-hash-backed assets — any future caller
  registering location-only assets must register real content (production already does).

## R0a Gate Remaining Work (after 1C)

1. Full-chain independent verification (R0a Gate task).
2. Worker image digest anchoring + provenance recording (K-1/K-3).
3. Metadata-only finding path strengthening decision (K-2).
4. Execution-environment provenance contract for evidence (K-3).

## Rollback

Single-commit revert restores R0a-1B behavior; all R0a-1C changes are additive constraints with
tests, no data migrations required.

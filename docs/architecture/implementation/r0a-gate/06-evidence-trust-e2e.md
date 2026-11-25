# R0a Gate Evidence Lifecycle and Persistence/Restore

## Lifecycle separation (Phase 9)

Gate E2E-03 (`test_e2e03_trusted_evidence_lifecycle`) proves the full chain over real docker:
execution succeeded → artifact accepted (hash verified) → operation evidence (hash-bound) →
canonical adaptation (`unverified`) → registry registration (`reference_validated`) → runtime
content verification (`verified`) → persistence (`_persist`) → restore + revalidation
(`verified` retained because all checks pass) → consumption (report gate accepts verified,
rejects unverified). Each stage transition is asserted independently.

## Persistence / Restore / Tampering (Phase 10)

- Restored canonical evidence re-runs registry verification against current content (A1.4.1);
  restored legacy evidence revalidates against asset restore states with explicit downgrade
  reasons; V1 session restore downgrades everything (R0a-1C R1).
- Tampering matrix implemented/covered: status→verified (downgraded: session restore test,
  registry register reset), forged verification ID (finding lineage gate cross-check),
  content change (hash mismatch → STALE → downgrade), unknown type/validator-missing
  (fail closed), truncated/invalid session state (restore returns None, session does not resume).
- Hash semantics documented: integrity of registered content only — not source authenticity, not
  conclusion correctness. Unkeyed hashes are vulnerable to a full-record forger; the trust model
  therefore never derives trust from hashes alone — registry verification + execution linkage +
  live revalidation are required.

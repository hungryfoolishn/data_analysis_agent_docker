# R0a-1C Restore Trust Boundary

## Principle Implemented

> Restore is data recovery, not trust re-granting.

## Restore Paths and Their Trust Semantics (after R0a-1C)

| Restore path | Behavior before | Behavior after R0a-1C |
| --- | --- | --- |
| `EvidenceRegistry.restore_record` (canonical) | reset to `unverified` | unchanged (already correct); locked by test |
| Runtime V2 context restore | A1.4.1 revalidation: canonical evidence re-verified via registry content validator; legacy evidence re-validated against asset restore states; verified retained only when linked verification passed + executions succeeded + artifacts exist + assets pass integrity; otherwise `failed`/`unverified` with recorded reason | unchanged (already correct); regression tests exist |
| V1 session restore (`session_persistence.deserialize_findings`) | **preserved persisted `verification_status` verbatim** — a tampered or stale state file could inject `verified` evidence | **FIXED**: all restored evidence downgraded to `unverified`; `verification_result_id` dropped; trust can only be re-established by the live runtime |

## Failure Handling Matrix

| Restored record condition | Result |
| --- | --- |
| `verified` + intact content (runtime plane) | revalidated by runtime context; retained only if every check passes |
| `verified` (V1 session plane) | downgraded to `unverified` (R0a-1C) |
| `failed` (V1 session plane) | downgraded to `unverified` (re-verification is a runtime decision, not restore) |
| missing verification link | `verification_result_id` dropped; evidence remains unverified |
| unknown evidence type | runtime content validator refuses metadata-only verification → not promotable |
| schema-incompatible record | session restore validates minimum fields (`SessionPersistence.restore`) and returns `None` (session does not resume) |

## Legacy Records Strategy

Persisted records are never deleted. Legacy `verified` records are downgraded or revalidated
according to the paths above:
- canonical: re-run registry verification against current content (A1.4.1);
- runtime legacy evidence: re-validated against source-asset restore states with explicit
  downgrade reasons (`legacy_evidence_not_recomputable`,
  `source_assets_verified_legacy_proof_not_recomputable`);
- V1 session: downgraded to `unverified` (R0a-1C).

## Restore Tests

- New: `TestRestoreTrustBoundary` in `tests/test_evidence_trust_boundary.py` (verified downgrade,
  tamper injection, failed→unverified, unverified stays unverified, canonical restore reset).
- Existing: `tests/runtime_v2/test_restore_integrity.py`,
  `tests/test_runtime_state_restore_evidence_views.py` (updated for content binding),
  runtime context restore revalidation tests (A1.4.1).

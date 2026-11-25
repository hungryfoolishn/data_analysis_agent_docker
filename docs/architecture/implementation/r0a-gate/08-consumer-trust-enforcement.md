# R0a Gate Consumer Trust Enforcement and Isolation

## Consumer table (Phase 12)

| Consumer | Evidence source | Trust check | Verification check | Hash/provenance | Fail behavior |
| --- | --- | --- | --- | --- | --- |
| `runtime/report_validator.py` | EvidenceItem/Canonical records | verified required | `verification_result_id` required | numbers collected only from verified | report validation errors |
| `runtime/finding_builder.py` | runtime EvidenceItem | verified required | linked VerificationResult recorded + passed + evidence linkage | lineage to executions/assets | finding rejected |
| `runtime/report_claims.py` | report claims | verified boosts confidence | n/a | n/a | confidence ×0.8 without verified |
| `runtime/context.py` restore | persisted records | revalidation before use | linked verification re-checked | asset restore integrity | downgrade/failed with reason |
| `tools/tool_record_finding.py` | LLM-authored finding | metadata-only path now stays unverified (gate fix) | existence check recorded, no promotion | — | finding recorded as unverified |
| `runtime/multi_agent.py` | evidence IDs | none (benchmark) | n/a | n/a | no trust elevation |
| API `/health` | worker readiness only | n/a | n/a | backend/image not exposed as trust | informational |

## Consumer negative tests (Phase 12.1)

Unverified/failed/restored/tampered/missing-verification paths are exercised by
`test_evidence_trust_boundary.py`, `test_r0a_gate_e2e.py` (E2E-N16, E2E-03 deny path),
`test_report_rebuild.py`, `test_validator_e2e.py`, `runtime_v2` restore suites. Unverified
evidence never yields a trusted claim; exploratory V1 findings remain explicitly unverified
(state machine progress preserved, report trust unchanged).

## Isolation (Phase 6.6)

- Cross-session: per-session workspaces, registries and state directories (Gate E2E-09);
  persisted state restore cannot pull another session's evidence (state file is per-workspace;
  restore downgrades).
- Cross-job: manager-owned per-job workspaces with manifest identity checks (reuse forbidden),
  content-copy inputs, output collection scoped to the job's own container (Gate E2E-N18).
- Cross-evidence: duplicate `evidence_id` rejected by the registry unless `replace=True`.

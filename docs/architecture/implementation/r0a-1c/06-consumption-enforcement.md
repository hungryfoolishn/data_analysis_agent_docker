# R0a-1C Consumption Enforcement

## Consumer Inventory

| Consumer | Evidence type | Required trust | Validation performed | Failure behavior |
| --- | --- | --- | --- | --- |
| `runtime/report_validator.py` | EvidenceItem / Canonical report records | verified + verification_result_id | per-finding supported_by check; unverified evidence is a validation error; numeric proofs only collected from verified evidence | report validation errors (report rejected) |
| `runtime/finding_builder.py` | runtime EvidenceItem | verified + linked verification recorded + passed + evidence linkage | full lineage gate | finding rejected with errors |
| `runtime/report_claims.py` | report claims | verified evidence boosts confidence | verified count factor | confidence ×0.8 without verified evidence |
| `runtime/context.py` (restore-time consumption) | legacy + canonical | revalidated before use | restore revalidation (A1.4.1) | downgrade/failed with reason |
| `tools/tool_record_finding.py` | finding evidence | runtime path: source execution has passing verification; metadata-only path: evidence existence | verification records | finding not recorded (runtime path) |
| `runtime/multi_agent.py` | evidence_ids (benchmark harness) | none (comparison metrics only) | presence checks | no trust elevation (IDs are not promoted) |
| `evaluation/golden_phase1.py` | domain evidence records | deterministic harness | n/a | offline evaluation only |

## Enforcement Rule Assessment

The existing consumers do not merely check `status == "verified"`: the finding lineage gate
cross-checks the linked `VerificationResult` inside Runtime records (status, passed flag,
evidence linkage), and report validation requires the verification link. This satisfies the
"verify the trust claim, not just the field" requirement without inventing a new consumer API.
R0a-1C adds regression tests locking these behaviors (unverified consumption is rejected at the
finding/report gates).

## Anti-Elevation Rules (verified by scan)

- Multi-agent forwarding passes evidence **IDs** for benchmark comparison only; no trust field is
  transferred or upgraded.
- Reports never silently upgrade unverified evidence: report_validator counts it as an error;
  report_claims degrades confidence.
- Docker `success=true` (R0a-1B worker result) never creates or promotes evidence: worker
  results carry artifacts + state only; evidence creation happens through the runtime paths
  above.

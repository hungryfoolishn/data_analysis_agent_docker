# R0a-1C Evidence Trust Model

## Trust State Contract (per plane, no mixing)

| Plane | States | Dimension |
| --- | --- | --- |
| Canonical | `unverified` → `reference_validated` → `verified`; terminal `failed` | Trust state only |
| Runtime EvidenceItem | `unverified` / `verified` / `failed` | Trust state only |
| Asset restore | `VERIFIED` / `STALE` / `MISSING` / `UNVERIFIABLE` / `VALIDATION_FAILED` | Restore/validation result (not evidence trust) |
| VerificationResult | `passed` / `failed` / `skipped` | Verification outcome (input to trust promotion) |
| Execution | `succeeded` / `failed` / `timed_out` / ... | Execution result (never trust) |

State dimensions are kept separate: execution success, artifact validation, verification outcome,
restore integrity and evidence trust are distinct fields in the existing models. No new enum was
introduced; the existing contract was validated and locked with tests.

## Trust Authority

`EvidenceRegistry` is the single promotion authority for canonical evidence:

- `register()` — never preserves a caller-supplied trust state; forces `reference_validated`
  after reference validation (execution linkage, task/tool match, success status, asset/artifact
  existence).
- `verify(evidence_id, content_validator=...)` — the only method that sets `verified`:
  - missing validator → status `failed` + raise (no silent pass);
  - validator exception → exception propagates, status unchanged (no promotion);
  - validator errors (incl. UNKNOWN-style returns) → `failed` + raise;
  - legacy evidence type cannot be verified from metadata alone (content validator must handle
    it explicitly; the runtime context validator refuses it).
- `restore_record()` — restore resets to `unverified`; restoration is not trust re-granting.
- `mark_failed()` — explicit downgrade path.

## Verified Generation Conditions

| Evidence type | Conditions for `verified` |
| --- | --- |
| `analysis_operation` (canonical) | references valid + execution succeeded + content validator (operation content: inputs/outputs/artifacts, row/column match, execution linkage) |
| `sqlite_query` (canonical) | references valid + execution succeeded + content validator (provenance, snapshot reference, hash) |
| `legacy` (canonical) | references valid + **explicit** content validator; runtime context validator refuses metadata-only verification |
| runtime `EvidenceItem` | collector path: execution succeeded + related verification passed + artifacts exist; finding path: runtime verification record with passing status |
| domain `OperationEvidence` / `SqliteQueryEvidence` | deterministic collector guards (registered asset, content hash); these records are **claims** until adapted to the canonical plane, where they start `unverified` |
| `FinancialEvidence` | financial verification engine passed for the calculation |

## Consumer Enforcement

- `runtime/report_validator.py`: findings must cite evidence with `verification_status ==
  "verified"` **and** a `verification_result_id`; unverified evidence is a validation error.
- `runtime/finding_builder.py`: evidence must be verified + linked verification recorded in
  Runtime + verification passed + verification must reference that evidence.
- `runtime/report_claims.py`: confidence is multiplied down (×0.8) when no verified evidence
  supports the claim.
- `runtime/context.py` (restore): verified status retained only when linked verification passed,
  executions succeeded, artifacts exist and source assets pass restore integrity; otherwise
  `failed` or `unverified` with a recorded reason.

# R0a-1C Baseline and Evidence Inventory

## Git Baseline

- Repository worktree: `docker-host:/python/pragrams/data_analysis_agent_r0a1a`
- Branch: `codex/r0a-1c-evidence-trust-boundary` (from `4878adc`, R0a-1B)
- Working tree clean at start; R0a-1B remote branch untouched.

## Evidence Model Inventory (real code)

| Model | File | Status values | Default at construction |
| --- | --- | --- | --- |
| `CanonicalEvidence` | `evidence/models.py` | unverified / reference_validated / verified / failed | `unverified` |
| `OperationEvidence` | `evidence/operations.py` | verified / unverified | `verified` (earned by construction, hash-bound after R0a-1C) |
| `SqliteQueryEvidence` | `evidence/sqlite.py` | verified / unverified | `verified` (earned by construction, hash-bound) |
| `EvidenceItem` | `schemas.py` | unverified / verified / failed | `unverified` |
| `FinancialEvidence` | `runtime/financial/models.py` | verified (gated) | verified only after verification engine passes |

## Creation Entry Inventory

| ID | Evidence Type | Creation Entry | Caller | Default Status | Validator | Trust Authority | R0a-1C Action |
| --- | --- | --- | --- | --- | --- | --- | --- |
| C1 | analysis_operation (domain) | `collect_operation_evidence` | `tools/tool_analysis_operation.py`, `evaluation/golden_phase1.py`, `runtime/research_benchmark.py` | verified | guard: registered output asset (+ content hash after R0a-1C) | none (record plane; adapted to unverified for registry) | hash requirement tightened |
| C2 | sqlite_query (domain) | `collect_sqlite_query_evidence` | `tools/tool_sqlite_query.py`, `evaluation/golden_phase1.py` | verified | guard: real BoundedReadResult + sqlite asset + content hash | none (record plane) | caller `validation_status` parameter removed |
| C3 | canonical | `EvidenceRegistry.register` | `runtime/context.register_canonical_evidence`, adapters | reference_validated (forced) | `validate_references` | **EvidenceRegistry** | retained |
| C4 | canonical verify | `EvidenceRegistry.verify` | `runtime/context.verify_canonical_evidence`, restore revalidation | verified (single promotion point) | caller content validator + references | **EvidenceRegistry** | retained; fail-closed behavior locked by tests |
| C5 | runtime EvidenceItem | `EvidenceCollector.collect` | runtime runner `evidence_factory`, `langgraph_agent` | verified (only if execution succeeded + related verification passed + artifacts exist) | `EvidenceCollectionError` guards | collector (deterministic) | retained; tested |
| C6 | runtime EvidenceItem | `tool_record_finding.py` | agent tool | unverified → verified after provenance checks | runtime-path: source execution must have passing verification; metadata-only compat path: evidence existence | runtime verification record | retained (documented compat), risk-tracked |
| C7 | financial | `runtime/financial/workflow.py`, `runtime/financial_agent/task_executor.py` | financial pipeline | verified | verification engine must pass; skipped otherwise | financial verification engine | retained (domain contract) |
| C8 | legacy restore | `session_persistence.deserialize_findings` | `_Session._apply_restore_state` | **was: preserved from JSON** | none | none — bypass | **FIXED: downgraded to unverified** |

No production path allows user/LLM input to set a trust field: all creation entries are internal
tool/runtime code; LLM-facing tool parameters were checked (none expose status/trust fields).

## Lifecycle Map (as implemented)

```
Evidence input (internal tool/runtime results, never LLM-controlled trust)
  -> construction: collect_* / EvidenceItem (default unverified or earned-by-construction)
  -> adaptation: canonical_from_* -> CanonicalEvidence(unverified)
  -> registry validation: EvidenceRegistry.register -> reference_validated (references, execution
     linkage, asset/artifact existence)
  -> trust verification: EvidenceRegistry.verify(content_validator) -> verified | failed
  -> persistence: runtime state / session state JSON (status serialized as data)
  -> restore: registry.restore_record -> unverified (canonical)
              runtime context revalidation (A1.4.1) -> verified retained only if all checks pass,
              else failed/unverified
              session restore (deserialize_findings) -> unverified (R0a-1C fix R1)
  -> consumption: report_validator / finding_builder / report_claims
     (verified + verification_result_id + linked passing verification)
```

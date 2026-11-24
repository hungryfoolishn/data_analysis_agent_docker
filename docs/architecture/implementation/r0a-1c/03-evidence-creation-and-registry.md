# R0a-1C Evidence Creation and Registry

## Creation Bypass Audit Results

1. **Default `verified` in collectors is earned, not injected.** Both deterministic collectors
   construct evidence exclusively from internal execution results (`BoundedReadResult`,
   registered `DataAsset`) and reject incomplete input. They are not reachable from user/LLM
   parameters. Their output is deliberately converted to `unverified` canonical evidence by the
   adapters before registry verification (`evidence/adapters.py`).
2. **Caller-assigned trust removed.** `collect_sqlite_query_evidence(validation_status=...)` was
   a public parameter accepting a trust value from any internal caller. No production or test
   caller passed it; the parameter is removed (R2), making the trust status non-assignable.
3. **Content binding enforced.** `collect_operation_evidence` previously accepted a verified
   record backed only by a file location (`content_hash is None and location is None` raised,
   but hash-less + location-present assets passed). Now a content hash is mandatory (R3), so a
   `verified` operation evidence always binds to an analyzable content identity.
4. **Registry is the only canonical promotion point.** `report_records()` / `list()` /
   `get()` never change status; there is no parallel registry and no factory `verified=True`
   parameter anywhere in the codebase (verified by scan).
5. **Direct status mutation scan.** All `verification_status = "verified"` assignments were
   traced:
   - `evidence/registry.py:182` — Trust Authority promotion (kept);
   - `evidence/collector.py:200` — deterministic collector with guards (kept, tested);
   - `tools/tool_record_finding.py:457` — runtime path, requires source execution with passing
     verification (kept);
   - `tools/tool_record_finding.py:487` — metadata-only compatibility path (kept, risk-tracked:
     weaker existence-only check for sessions without a V2 scheduler);
   - `runtime/context.py:795` — restore revalidation retaining verified only after full checks
     (kept);
   - `runtime/financial/workflow.py:404`, `runtime/financial_agent/task_executor.py:373` — gated
     by verification engine `passed` (kept).

## Registry Validation Rules (existing, verified)

- unknown execution_id → error; execution must be `succeeded`;
- task_id / tool_name must match the execution;
- evidence_text must be non-empty;
- input/output assets and artifacts must exist in the registry;
- duplicate evidence_id rejected unless `replace=True`.

## New Tests

`tests/test_evidence_trust_boundary.py` (19 tests) locks: construction defaults, hash binding,
non-assignable trust parameter, registry fail-closed verification (missing validator, validator
exception, FAIL/UNKNOWN results), restore trust reset, and tamper-injection resistance.

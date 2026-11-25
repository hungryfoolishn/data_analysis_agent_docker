# Multi-turn Orchestration & Evidence-driven Reasoning (P5)

Entry: `langgraph_langchain/analysis_intelligence/reasoning.py`
Tests: `tests/test_analysis_intelligence_reasoning.py` (14 passed)

## Claim contract

`Claim` binds claim_type (FACT / OBSERVATION / HYPOTHESIS / CONCLUSION / LIMITATION),
evidence_ids, metric, dimension, data_scope, method, numeric value, confidence and
limitations. `ClaimVerifier` performs deterministic consistency checks against
`EvidenceRecord`s:

| Check | Failure behavior |
| --- | --- |
| FACT/CONCLUSION without evidence | REJECTED |
| Evidence id not found | REJECTED |
| Evidence not verified | claim WEAKENED (conclusion downgraded to OBSERVATION) |
| Claim value != evidence value (numeric) | REJECTED |
| Claim scope broader than evidence scope | REJECTED (scope overreach) |
| Metric mismatch | REJECTED |
| Correlation-based conclusion without causality limitation | WEAKENED → OBSERVATION |
| Hypothesis with observations | retained, never promoted |

## Orchestration state & termination contract

`AnalysisState` records task outcomes exactly once (duplicate side effects rejected),
auto-verifies claims at insertion, tracks rejected claims, and exposes `decide_next`:

```
failed + revisions exhausted        -> STOP
failed + retryable                  -> REVISE (bounded by PlanRevisionLedger)
failed + non-retryable + succeeded  -> PARTIAL_RESULT (valid results preserved)
failed + non-retryable only         -> STOP
plan incomplete                     -> EXECUTE
plan complete, no claims            -> VERIFY
claims present                      -> FINALIZE (or PARTIAL_RESULT if all rejected)
```

Budget: revisions share the P3 `PlanRevisionLedger` (max 3); exploration budget is the
P4 ledger; no path bypasses them. Integration with the frozen runtime: results in
production are V2 `ExecutionResult`s recorded through `on_task_finish`; this state module
orchestrates at the same granularity without touching V1.1 governance.

## Scenario coverage (executed tests)

- Scenario A basic metric: single task, no over-exploration → VERIFY → FINALIZE.
- Scenario B ranking/collection: covered in P2/P3 (ranking plan + top_n).
- Scenario C drill-down: P3 verified-relationship chain.
- Scenario D dynamic exploration: P4 fold-back.
- Scenario E multi-metric: P2 multi-metric resolution (plans via group_aggregate).
- Scenario F trend: P3 trend operation with verified time column.
- Scenario G anomaly/anomaly-evidence: numeric mismatch + unverified-evidence checks.
- Scenario H unsupported: P2 UNSUPPORTED + P3 PlanningBlockedError.
- Failure recovery: retryable → REVISE → exhausted → STOP; non-retryable + partial
  success → PARTIAL_RESULT.

**P5 Gate: PASS** (multi-turn execution ✓ intermediate feedback ✓ dynamic decision ✓
revision ✓ evidence binding ✓ claim verification ✓ failure recovery ✓ budget ✓
termination ✓).

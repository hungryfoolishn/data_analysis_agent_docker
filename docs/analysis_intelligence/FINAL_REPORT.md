# Analysis Intelligence V1.0 — FINAL REPORT

## 1. Overall Verdict

**ACCEPTED.** All 7 phases (P0-P6) implemented, tested, and committed on
`codex/analysis-intelligence-v1.0`. Full regression: 2040 passed / 2 failed
(frozen dashboard baseline pair) / 13 skipped — identical failure set to the
pre-branch baseline, zero new failures. The 10-case evaluation suite passes
with real executions and honest rejection gates.

## 2. Product Capabilities Delivered

1. Deterministic semantic contract layer (metrics, dimensions, hierarchies,
   compatibility) with trust levels and a real registry consumer chain.
2. Deterministic intent understanding with per-field provenance and a
   5-state completeness contract (READY / READY_WITH_ASSUMPTIONS /
   NEEDS_EXPLORATION / NEEDS_CLARIFICATION / UNSUPPORTED).
3. Constraint-aware planning: metric x dimension compatibility gate, required
   column checks, granularity checks, bounded plan-revision ledger with
   goal-rewrite guard.
4. Dynamic schema exploration with verified fold-back into intents
   (dimensions AND time columns) and a bounded exploration ledger.
5. Claim/evidence verification with correlation-to-causality discipline and a
   termination contract (`decide_next`).
6. Evaluation harness proving execution success ≠ analysis correctness
   (`wrong_analysis_detected` critical gate).

## 3. Existing Capability Reused

- `runtime/analysis_operations.py` allowlisted operations (real execution).
- `runtime/general_planner.py::validate_general_plan` (authoritative plan
  validation, v2 output-reference syntax).
- `semantic/models.py` bridged (not rewritten) via semantic_contracts.
- `AnalysisPlan` / planner regex layer reused under the new resolver.
- Evidence/verification patterns from the frozen evidence pipeline
  (`EvidenceRecord`, claim binding) — no second evidence system.

## 4. Architecture Changes

Additive only, one new package: `langgraph_langchain/analysis_intelligence/`
(semantic_contracts, intent, constraints, exploration, reasoning, evaluation)
plus `docs/analysis_intelligence/` and 8 test modules. No frozen-file
semantics changed; two runtime defects fixed additively (see §14).

## 5. Semantic Model

`semantic_contracts.py`: MetricContract (kind, aggregation, required columns,
compatible dimensions, granularity), DimensionContract (semantic type,
cardinality, source column), DimensionRelationship (PARENT_CHILD with mapping
key). ValidationStatus: VERIFIED / DECLARED / INFERRED / UNKNOWN / INVALID.
Invariant: INFERRED never passes deterministic checks. 13 tests.

## 6. Intent Understanding

`intent.py`: deterministic Chinese/English matching (longest-prefix group_by,
metric aliases), per-field provenance, invalid metric-dimension combos become
UNSUPPORTED, unknown dimensions/metrics route to NEEDS_EXPLORATION /
NEEDS_CLARIFICATION. Ambiguity is surfaced, never silently resolved. 17 tests.

## 7. Planner

`constraints.py`: IntentPlanner emits GeneralAnalysisPlan through the
compatibility + column + granularity gates; PlanningBlockedError carries
structured reasons; PlanRevisionLedger caps revisions at 3 and rejects goals
that drift from the user question. Drill-down requires a VERIFIED
parent-child relationship path. 10 tests.

## 8. Dynamic Exploration

`exploration.py`: SchemaExplorationEngine consumes SchemaProfile only (no
asset I/O); ExplorationDecider folds results back into intents (dimensions
registered as DECLARED with runtime_discovered provenance; time columns
captured for trend); ExplorationLedger caps at 5 with redundancy detection.
Divergent-path proof tests included. 8 tests.

## 9. Orchestration

`reasoning.py::AnalysisState`: task outcomes recorded exactly once (duplicate
side effects rejected), claims auto-verified at insertion, rejected claims
tracked, `decide_next` implements the REVISE / PARTIAL_RESULT / STOP / VERIFY
/ FINALIZE termination contract. 14 tests.

## 10. Evidence Reasoning

Claim (FACT/OBSERVATION/HYPOTHESIS/CONCLUSION/LIMITATION) binds evidence_ids,
metric, scope, method, numeric value. ClaimVerifier rejects: missing evidence,
unknown evidence ids, unverified evidence for conclusions, value mismatch,
scope overreach, metric mismatch; correlation conclusions without causality
limitation are downgraded to observations; hypotheses are never promoted.

## 11. Evaluation

`evaluation.py`: 10 deterministic cases (positive / ranking / trend /
multi-step drill-down / dynamic dimension / 4 negative rejections / null
boundary). Weights: intent 0.2, planning 0.2, computation 0.3, evidence 0.2,
behavior 0.1. Result: 10/10 pass; 6 real executions; 4 rejections with
`executed=False`; wrong-claim gate rejects in all ground-truth cases.
Details: `07-analysis-quality-evaluation.md`.

## 12. E2E

The chain intent -> exploration -> constraint-checked plan -> REAL
`execute_analysis_operation` execution -> claim verification runs end-to-end
inside the evaluation harness on real pandas frames (no mocks in the
execution path). Ground truths verified numerically:
group agg {dept_a: 350.0, dept_b: 650.0} with null exclusion, ranking
{dept_a: 3.0, dept_b: 3.0}, dynamic region {east: 350.0, west: 650.0}.

## 13. Regression

FULL `pytest tests/ -q`: **2 failed, 2040 passed, 13 skipped** (967.96s).
Failures are exactly the frozen baseline pair:
`tests/test_dashboard.py::test_individual_metrics_endpoints`,
`tests/test_dashboard.py::test_dashboard_display_format`.
Baseline math: 1970 pre-existing passed + 70 new tests = 2040. Zero new
failures, zero new skips.

## 14. Findings

Real defects exposed by real execution and fixed in `d6c3532`:

1. **[P1 fixed] Sort referenced a dropped column** — sort emitted the
   pre-aggregation name while `group_aggregate` renames the value column to
   `{metric}_{agg}`; plan-shape tests had codified the bug. Fixed: planner
   emits the aggregated column name and binds the sort to the producer-output
   reference (`{step_id}.output`), supported by GeneralPlan v2 validation.
2. **[P1 fixed] Drill-down plan unexecutable** — chained single-key
   aggregations lose the child column after the first transform. Fixed as one
   hierarchical multi-key `group_aggregate` along the verified path; runtime
   `group_aggregate` now supports list `group_by` (string behavior unchanged).
3. **[P1 fixed] GeneralPlan validation rejected list `group_by`** —
   `_valid_parameters` now accepts a non-empty list of non-empty strings.
4. **[P1 fixed] Hollow pass in the harness** — the trend case "passed" with
   no executed plan because a blocked plan was masked as a behavior pass.
   Fixed: blocked plans on execution-expected cases are failures; trend now
   resolves its time column through the same exploration trust path.
5. **[Documented, not fixed] Trend/drill-down lack numeric ground truth** —
   honestly reflected in the evaluation score (0.7) and limitations.

## 15. Limitations

- Deterministic regression floor of 10 cases, not a statistical benchmark.
- Ratio/growth metric kinds are gated but not yet computed end-to-end.
- Scope: aggregation / count-ranking / trend / drill-down verified; other
  analysis methods (funnel, cohort, sensitivity...) untouched.
- Evaluation weights credit only verified evidence (trend/drill-down pass
  with score 0.7 — documented, not hidden).

## 16. Deferred Items

- API/UI surfacing of intent status, claims, and evidence bindings.
- LLM-assisted intent parsing behind the deterministic contract layer.
- Numeric ground truth for trend and drill-down cases.
- Metric expansion (ratio, growth, per-share kinds) with compatibility rules.
- Persistent exploration/revision ledgers across sessions.

## 17. Commit Chain

| Phase | Commit | Subject |
| --- | --- | --- |
| P0 | `16de0ab` | docs: record analysis capability baseline |
| P1 | `dfbbc72` | feat(analysis): introduce semantic contracts |
| P2 | `4d6f567` | feat(analysis): add deterministic intent resolution |
| P3 | `bab1d64` | feat(planner): add constraint-aware planning |
| P4 | `7d0e6d2` | feat(analysis): implement dynamic exploration feedback |
| P5 | `4c9b880` | feat(analysis): improve evidence reasoning |
| P6 fix | `d6c3532` | fix(analysis): make intelligence plans executable end-to-end |
| P6 | `b43d218` | test(analysis): establish intelligence evaluation baseline |
| docs | (this commit) | docs(analysis): complete analysis intelligence v1.0 delivery |

Base: `13d02f4` (audit tip; freeze commits cfa6c79, 3b13a96, 5f5be80 all in
ancestry). No history rewritten, no force push.

## 18. Branch

`codex/analysis-intelligence-v1.0`

## 19. Remote

Pushed to `origin/codex/analysis-intelligence-v1.0` via bundle transfer
(server worktree -> bundle -> local fetch -> push). Remote HEAD verified with
`git ls-remote` after push.

## 20. Working Tree

Clean after final docs commit (machine-specific and ignored data preserved).
Verified with `git status --short` before each commit and push.

## 21. Frozen Architecture Impact

None semantic. Untouched: `security.py`, `execution_policy.py`,
`container_manager.py`, `path_safety.py`, `capability_adapter.py`,
`runtime/executor.py` budget semantics, `RUNTIME_V2_ENABLED` default-off,
B11 frozen contracts, dashboard freeze (2 failures preserved as-is).
Additive runtime fixes (`analysis_operations.py` list group_by,
`general_planner.py` list-group_by validation) are backwards compatible;
`test_general_planner.py` and `test_planner_v2_contract.py` pass unchanged
except the accepted list-group_by validation extension.

## 22. Recommended Next Stage

1. Surface the intent/claim/evidence chain through the analysis report gate
   (consume AnalysisState.decide_next outputs in report synthesis).
2. Add numeric ground truth for trend and drill-down evaluation cases.
3. Extend metrics (ratio/growth) with contract-driven computation.
4. Wire LLM intent parsing as a candidate generator under the deterministic
   resolver (candidates must still pass the same gates).
5. Persist exploration/revision ledgers for auditability in multi-turn runs.

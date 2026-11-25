# Implementation & Validation Report (Analysis Intelligence V1.0)

Branch `codex/analysis-intelligence-v1.0`, base `13d02f4` (audit tip; F-1 fix in ancestry).
Worktree: server-side only; V1.1 frozen architecture untouched.

## Actual changes by phase

| Phase | Commit | Files | Content |
| --- | --- | --- | --- |
| P0 | `16de0ab` | 3 docs | capability baseline, gap map, progress tracking |
| P1 | `dfbbc72` | semantic_contracts.py + 13 tests + 02 doc | Metric/Dimension/Relationship contracts, CompatibilityValidator (VALID/INVALID/CONDITIONAL/UNKNOWN), SemanticRegistry, trust discipline |
| P2 | `4d6f567` | intent.py + 17 tests + 03 doc | deterministic IntentResolver, provenance per field, 5-state completeness |
| P3 | `bab1d64` | constraints.py + 10 tests + 04 doc | IntentPlanner, compatibility + column gates, PlanningBlockedError, PlanRevisionLedger (max 3, goal-rewrite guard) |
| P4 | `7d0e6d2` | exploration.py + 8 tests + 05 doc | SchemaExplorationEngine, ExplorationDecider fold-back, ledger (max 5) + redundancy detection |
| P5 | `4c9b880` | reasoning.py + 14 tests + 06 doc | Claim/ClaimVerifier, AnalysisState.decide_next termination contract |
| P6 fix | `d6c3532` | constraints.py, analysis_operations.py, general_planner.py + planning test updates | executability fixes found by real execution (sort column binding, hierarchical drill-down, list group_by validation) |
| P6 | `b43d218` | evaluation.py + 8 tests + 07 doc | 10-case evaluation suite with critical gates |

## Test evidence

| Suite | Result |
| --- | --- |
| analysis_intelligence (semantic/intent/planning/exploration/reasoning/evaluation) | 62 passed (P1-P5) + 8 passed (P6) |
| general_planner + planner_v2_contract | pass (regression-checked after runtime fixes) |
| FULL `pytest tests/` | **2 failed, 2040 passed, 13 skipped** in 967.96s |

The only 2 failures are the frozen dashboard baseline pair
(`tests/test_dashboard.py::test_individual_metrics_endpoints`,
`tests/test_dashboard.py::test_dashboard_display_format`) — identical to the
pre-branch baseline. Baseline math: 1970 pre-existing passed + 70 new
analysis-intelligence tests = 2040 passed. Zero new failures, zero new skips,
no assertion weakened.

## Evaluation result (P6)

10/10 cases pass (6 real executions with ground truth / plan verification,
4 negative rejections at intent stage with `executed=False`).
Critical gate `wrong_analysis_detected` rejects corrupted claims in all
ground-truth cases. Details: `07-analysis-quality-evaluation.md`.

## Real defects found by real execution (fixed in `d6c3532`)

1. Sort step referenced the pre-aggregation column (`amount`) while the
   aggregation output is renamed to `amount_sum` — unexecutable plan that
   plan-shape tests accepted. Fixed by emitting the aggregated column name and
   binding the sort to the producer-output reference.
2. Drill-down emitted two chained single-key aggregations; the second lost the
   child column. Fixed as one hierarchical multi-key `group_aggregate`.
3. `validate_general_plan` rejected list `group_by` and
   `analysis_operations._group_aggregate` did not support it. Fixed additively
   (string behavior unchanged; general_planner/planner_v2 suites still pass).

## Limitations

- Trend and drill-down verified by real execution + plan shape, not numeric
  ground truth (documented in the evaluation score).
- The evaluation suite is a deterministic regression floor, not a statistical
  benchmark; LLM NL variance is out of scope by design.
- The intelligence chain is wired end-to-end at unit/integration level; UI/API
  surfacing of intents/claims is deferred (see FINAL_REPORT Deferred Items).

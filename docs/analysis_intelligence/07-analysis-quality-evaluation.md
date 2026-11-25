# Analysis Quality Evaluation (P6)

Entry: `langgraph_langchain/analysis_intelligence/evaluation.py`
Tests: `tests/test_analysis_intelligence_evaluation.py` (8 tests, 10 evaluation cases)

## Purpose

Deterministic end-to-end evaluation of the intelligence chain:
intent -> exploration -> constraint-checked plan -> REAL allowlisted execution ->
evidence/claim verification. The suite proves two non-negotiable properties:

1. Positive cases compute the exact ground truth through real data operations
   (no mocks in the execution path; every positive case executes a real plan).
2. A successful execution never substitutes for analysis correctness: a
   deliberately wrong claim must be REJECTED by the evaluator
   (`wrong_analysis_detected` critical gate).

## Scoring

| Component | Weight | Gate(s) |
| --- | --- | --- |
| Intent | 0.2 | intent_type, intent_metrics, intent_dimensions |
| Planning | 0.2 | planning_ops (real plan steps executed) |
| Computation | 0.3 | computation (output equals ground truth) |
| Evidence | 0.2 | evidence_binding |
| Behavior | 0.1 | rejection at correct stage + wrong_analysis_detected |

Pass/fail is gate-based (every applicable gate must pass). The numeric score
credits only verified evidence: cases without a numeric ground truth (trend,
drill-down shape) cannot earn the computation weight even when they pass —
the score honestly reflects evidence coverage, not just success.

## Case matrix (10 cases)

| Case | Kind | Question (essence) | Verifies |
| --- | --- | --- | --- |
| pos_group_agg | positive | 每个部门的收入 | plan inspect -> group_aggregate; output == {dept_a: 350.0, dept_b: 650.0} (null excluded by documented rule) |
| pos_ranking_topn | positive | 部门订单数排名前3名 | plan inspect -> group_aggregate -> sort; sort binds the aggregated column `order_id_count` via the producer-output reference; output == {dept_a: 3.0, dept_b: 3.0} |
| pos_trend | positive | 收入逐月趋势 | planning contract: verified time column obtained through the SAME exploration trust path (time-column question -> fold-back), then real `trend` execution |
| pos_drilldown | multi_step | 先按部门再按应用下钻 | hierarchical drill-down: one executable multi-key group_aggregate [department, application] along the verified parent-child path |
| pos_dynamic_dimension | dynamic | 每个region的收入 | NEEDS_EXPLORATION -> exploration discovers `region` column -> DECLARED fold-back to READY -> full real execution {east: 350.0, west: 650.0} |
| neg_unknown_metric | negative | 客户终身价值 | rejected at intent (no metric) |
| neg_ambiguous_dimension | negative | 每个片区的收入 | rejected at intent (unresolved dimension) |
| neg_unsupported_type | negative | 预测下个季度的收入 | FORECAST rejected at intent (not supported) |
| neg_unregistered_dimension | negative | 每个zone的收入 | rejected at intent (unresolved dimension) |
| boundary_null_excluded_from_sum | boundary | 每个部门的收入 | null amount row excluded from dept_b sum (350.0, not 450.0) |

Rejection cases additionally assert `executed == False`: a rejected analysis
must never reach the execution path. A blocked plan on a case that expects
execution is recorded as a FAILURE — there are no hollow passes (this was a
real defect found and fixed during P6: the trend case previously "passed"
without any executed plan).

## Critical integrity gate

`wrong_analysis_detected`: for every case with ground truth the runner submits
a deliberately corrupted claim (`value + 12345.0`). The verifier must reject it.
This is the formal proof that "execution succeeded" is never conflated with
"the analysis is correct" (audit finding F-5 closure).

## Trust discipline in the dynamic and trend cases

- Dynamically discovered dimensions register as DECLARED (schema-verified,
  confidence 0.7, `source=runtime_discovered`) and fold back into the intent
  via `ExplorationDecider.apply_result`. Semantic compatibility (`revenue` is
  groupable by `region`) is a domain fact held in the metric contract; what
  exploration proves is the existence and usability of the `region` column in
  the actual data. The planner's deterministic compatibility gate remains in
  force for all cases.
- The trend time column follows the same trust path: the planner blocks on a
  missing *verified* time column; the harness runs the time-column exploration
  question, records the evidence in the exploration ledger, folds the result
  back into the intent, and retries planning exactly once.

## Real defects found and fixed by real execution (P6 outcome)

Real execution exposed three planner/runtime defects that plan-shape tests
could not see:

1. **Sort referenced a dropped column** — `sort` was emitted with the
   pre-aggregation column name (`amount`) while `group_aggregate` renames the
   value column to `{metric}_{agg}` (`amount_sum`). Fix: the planner emits the
   aggregated column name and binds the sort step to the aggregation step's
   declared output reference (`{step_id}.output`), which the GeneralPlan v2
   validator natively supports. (`constraints.py`, updated in
   `tests/test_analysis_intelligence_planning.py`.)
2. **Drill-down plan was unexecutable** — two chained single-key aggregations
   lose the child dimension column after the first transform. Fix: drill-down
   is emitted as one hierarchical multi-key `group_aggregate`
   ([parent, child]) along the verified relationship path;
   `analysis_operations._group_aggregate` now supports list `group_by`.
3. **GeneralPlan validation rejected list group_by** — `_valid_parameters`
   required `group_by` to be a string. Fix: list-of-strings is accepted for
   `group_aggregate` (`general_planner.py`).

None of these fixes weaken any gate: the planning tests now assert the
corrected, executable parameters (e.g. `sort_params["by"] == "amount_sum"`,
hierarchical `group_by == ["department", "application"]`).

## Results (this branch)

- 10/10 evaluation cases pass; 8/8 pytest tests pass.
- 6/6 positive/boundary/dynamic cases execute real plans; 4/4 negative
  rejections occur at the intent stage with `executed=False`.
- Numeric ground truths match real `execute_analysis_operation` output exactly
  (including the null-exclusion boundary rule).
- Wrong-claim gate rejects in all ground-truth cases.

## Limitations

- The suite is deterministic and small (10 cases); it is a regression floor
  for the intelligence chain, not a statistical benchmark.
- Ground truth covers sum aggregation and count ranking; trend and drill-down
  are verified by real execution and plan shape but not by numeric ground
  truth yet (documented as score limitation, not hidden).
- Ratio/growth metrics are deferred to the metric expansion roadmap (planner
  already gates them).
- LLM-driven natural-language variance is out of scope by design: V1.0
  establishes the deterministic contract layer underneath any NL frontend.

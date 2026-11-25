# Constraint-Aware Planning & Plan Revision (P3)

Entry: `langgraph_langchain/analysis_intelligence/constraints.py`
Tests: `tests/test_analysis_intelligence_planning.py` (10 passed)

## Planning logic

```
Intent (validated by P2)
  -> status gate (READY / READY_WITH_ASSUMPTIONS only; NEEDS_* and UNSUPPORTED refuse to plan)
  -> trusted metric contracts required (inferred candidates never plan)
  -> required columns present in the registered asset
  -> metric x dimension compatibility (INVALID/UNKNOWN block; CONDITIONAL -> recorded preconditions)
  -> drill-down gate: verified relationship only
  -> deterministic intent -> GeneralAnalysisPlan (allowlisted operations)
  -> validate_general_plan (existing authoritative validation)
```

Blocked planning raises `PlanningBlockedError` with explicit reasons — an illegal plan
can never enter execution.

## Plan contract

Reuses `GeneralAnalysisPlan`/`GeneralPlanStep` so plans remain compatible with
`general_plan_to_runtime_plan` -> `initialize_plan` -> scheduler (V1.1 governance,
ToolCallBudget at TaskExecutor, EvidenceCollector). Every plan preserves
`user_goal == intent.user_goal` (tested).

Operation mapping: aggregation -> group_aggregate/summarize; ranking -> group_aggregate +
sort (top_n recorded, direction honored); trend -> trend (requires a verified time column);
drill-down -> chained group_aggregate steps with depends_on and filter propagation.

## Plan revision

`PlanRevisionLedger`: MAX_REVISIONS = 3, every revision records reason, trigger evidence,
previous/new plan ids, changed tasks, and preserved successful results. Goal rewrite is
refused (`assert_goal_unchanged`); exhaustion refuses further revision with a mandated
termination.

## P3 Gate evidence

Plan contract ✓ goal ✓ dependency (drill-down chain) ✓ plan validated (validate_general_plan)
✓ constraint validation real (columns, compatibility, trusted metrics, drill-down) ✓
plan from intent ✓ revision bounded (3) ✓ failure explicit (PlanningBlockedError) ✓
intent preserved (user_goal equality test) ✓ V1.1 governance untouched ✓
**P3 Gate: PASS.**

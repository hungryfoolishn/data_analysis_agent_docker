# ADR-016: Execution-Before-Compute Structured Plan

## Decision

New analysis sessions create a persisted, reviewable AnalysisPlan before the first
tool call. The plan contains the goal, success criteria, input assets, metrics,
dimensions, time field, ordered runtime steps, dependencies, and resource budget.

Plans submitted through POST /analysis/runs/{run_id}/plan/propose are validated
deterministically against the registered tool names, known input assets, asset
columns, dependency DAG, and step budget. Strict plans reject an unplanned method
or a step whose dependencies are incomplete.

## Compatibility

Legacy sessions and the default compatibility plan retain dynamic runtime steps.
The complete default plan is stored in run.plan, while run.steps continues to
represent only actual executions. Existing pause, revise, confirm, and resume APIs
remain compatible. Strict plans mirror approved steps into run.steps.

## Boundaries

This batch does not infer business semantics, call an LLM to validate fields, or
replace the agent's planning prompt. It establishes the deterministic plan
contract; later batches can add richer proposal and revision UX.

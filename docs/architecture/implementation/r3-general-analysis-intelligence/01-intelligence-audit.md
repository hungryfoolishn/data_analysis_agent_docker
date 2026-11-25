# R3 Intelligence Audit — Actual Code, Not Aspirations

Baseline: `codex/architecture-convergence-v1.1-e2e` @ `da2004a`.
Each V1.1 §9.1 item is audited against the real implementation; verdicts state what the
code does today, not what a planner prompt promises.

| # | Capability | Code anchors | Verdict | Notes |
| --- | --- | --- | --- | --- |
| 1 | Planner | `runtime/general_planner.py` (allowlisted PLAN_OPERATIONS), `runtime/llm_planner.py` (frozen PlanningOutcome state machine) | VERIFIED | LLM plans are re-validated in full; deterministic fallback never rewrites user intent; unmatched goal becomes needs_clarification (B4.2.1) |
| 2 | Task decomposition | `GeneralAnalysisPlan` steps + `validate_general_plan` (duplicate ids, unknown inputs, forward/self refs, cycles, output-name mismatch, declared depends_on) | VERIFIED | Decomposition is deterministic and schema-checked |
| 3 | Data understanding | `runtime/planner_models.py` PlannerInput assets + schema snapshot columns | VERIFIED | Planner receives registered asset columns only |
| 4 | Schema reasoning | `_valid_parameters` column checks; execution-time column validation in `analysis_operations` | VERIFIED | Unknown initial-asset columns rejected at plan time; upstream outputs checked at execution |
| 5 | Metric selection | `_preferred_numeric_columns`, heuristic metric matching in `_operation_for_goal` | PARTIAL | Heuristic, explainable, deterministic; no learned metric inference — acceptable, no silent guessing |
| 6 | Dimension selection | group_by regex parsing + validation | PARTIAL | Same reasoning as #5 |
| 7 | Skill retrieval | `runtime/skill_retriever.py`, wired into execution controller | VERIFIED | Retrieval feeds governed execution, not direct report injection |
| 8 | Tool selection | `tool_map` in `_run_runtime_v2_graph_stream`; unsupported methods hard-stop before execution | VERIFIED | No undeclared tool can run |
| 9 | Execution planning | `AnalysisPlan.budget.max_steps` (default 20, cap 200), sequential scheduler (`max_running_tasks=1`), output bindings | VERIFIED | Plan-size budget enforced by `validate_steps` |
| 10 | Dynamic exploration | `runtime/capability_adapter.py` ToolCallBudget; R3 unified accounting in `runtime/executor.py` TaskExecutor | VERIFIED (R3 closed gap) | R2.5 counted structured calls only; R3 counts structured + react + python at the single dispatch point |
| 11 | Intermediate result reasoning | shared `session.ns` namespace; `runtime.record_execution_result`; named output bindings | VERIFIED | Results are recorded objects, not free-text memory |
| 12 | Plan revision | fixed plan per V2 stream; react steps as controlled escape | GAP (deferred) | Automated re-planning would require new state machine; intentionally not implemented in this convergence run |
| 13 | Evidence-aware reasoning | `EvidenceCollector` + `VerificationPolicy` wired per execution result | VERIFIED | Evidence lifecycle untouched by convergence |
| 14 | Verification | `runtime/verification_policy.py`; report validator gate | VERIFIED | Unverified evidence cannot enter strict-mode reports |
| 15 | Failure recovery | per-task failed/needs_revision records; strict-mode hard stop; infra-only fallback policy; cancellation propagation | VERIFIED with noted gap | No automatic retry budget exists; retries are user-initiated, which is the safe default |
| 16 | Report synthesis | `runtime/analysis_report.py` build/validate/render | VERIFIED | Same gate as B4/B11 |
| 17 | Evaluation | `runtime/evaluation/` (runner, comparator, cross_task, golden_loader) | VERIFIED | Weighted scoring, execution/verification/numeric/evidence/finding/report components |

## Constraint compliance (V1.1 §9.3)

- No infinite agent loop: plan max_steps cap + per-run tool budget.
- No unbounded exploration: unified budget fail-closed before side effects.
- Planner cannot bypass execution policy: plans must pass `validate_general_plan` and
  `initialize_plan`; executors receive only declared tools.
- No unauthorized data access: data boundary is R0a/R0b; planner sees registered assets only.
- LLM cannot mark evidence verified: verification policy is code-owned.
- Prompt constraints are not the security boundary: enforcement lives in executors/scheduler.
- Fallbacks preserved: deterministic planner fallback + V1/V2 route policy untouched.

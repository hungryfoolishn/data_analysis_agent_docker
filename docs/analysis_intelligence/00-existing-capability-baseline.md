# Existing Capability Baseline (P0)

Baseline: branch `codex/analysis-intelligence-v1.0` @ `13d02f4` (freezed audit tip).
Freeze integrity: `cfa6c79` (implementation final) + `3b13a96` (F-1 budget fix, audit-only
commit) are both ancestors of this baseline — verified with `git merge-base --is-ancestor`.
No V1.1 history rewritten; the audit branch tip is the implementation base.

## 1. Real execution path (code-verified)

```
API (api_server_langgraph.py, owner/session checks)
  -> run_analysis_stream (langgraph_agent.py)
  -> RuntimeSelectionPolicy (runtime/runtime_route.py)  [RUNTIME_V2_ENABLED default OFF]
  -> V1: ReAct loop (recursion_limit 60) | V2: _run_runtime_v2_graph_stream
       -> AnalysisRuntime.initialize_plan(AnalysisPlan)  [max_steps<=200]
       -> RuntimeV2Controller(scheduler -> TaskRunner -> TaskExecutor)
       -> TaskExecutor (unified ToolCallBudget, dispatch)
            -> StructuredTaskExecutor | ReactTaskExecutor | PythonTaskExecutor
       -> AssetResolver (bounded reads, SnapshotStore)
       -> VerificationPolicy -> EvidenceCollector -> analysis report gate
```

Planning entry points:
- Deterministic: `runtime/analysis_workflow.py::prepare_file_analysis` -> `DeterministicAnalysisPlanner().plan(goal, asset_ids, asset_columns)` -> `general_plan_to_runtime_plan` -> `initialize_plan`.
- LLM-assisted: `runtime/llm_planner.py::run_planning` — frozen PlanningOutcome state machine
  (planned / fallback_planned / needs_clarification / planning_failed), LLM plans fully
  re-validated by `validate_general_plan`; fallback never rewrites user intent.

## 2. Prompt inventory

| Prompt | File | Caller | Role | Status |
| --- | --- | --- | --- | --- |
| ReAct system/agent prompt | `langgraph_agent.py` (react agent construction, ~line 2129) | V1 loop + V2 react steps | tool orchestration | ACTIVE |
| Planning prompts | `runtime/llm_planner.py` planner_fn contract | V2 planning | LLM plan generation (validated) | ACTIVE (contract) |
| Semantic context | `semantic/formatting.py::format_semantic_context` | prompt builder (langgraph_agent.py:2090) | injects SemanticResolution text | ACTIVE |
| Skill instructions | `skills_loader.py` + `tools/tool_skills.py` | both runtimes | skill_view/skill_reference | ACTIVE |
| Report synthesis/validation | `runtime/analysis_report.py` | report stage | report gate | ACTIVE (code, not prompt) |

No orphan production prompt found; semantic context is currently TEXT-ONLY injection —
no deterministic constraint checking consumes it (key gap, P1).

## 3. Tool inventory (26, registry-locked)

Locked by `tests/test_capability_inventory.py`: analysis 23, memory 1, skills 2.
Analytical coverage: inspect/select/filter/group_aggregate/sort/trend/detect_anomalies/
correlation/data_quality/summarize/macro_transform (structured operations), load_data,
run_sqlite_query, eda_profile, python_repl, delegate_analysis, 12 analysis-method tools
(compare_groups, analyze_time_trend, decompose_contribution, profile_distribution,
analyze_correlation, compare_periods, calculate_ratio, analyze_funnel, analyze_retention,
analyze_concentration, test_group_difference, run_sensitivity_check), governance
(record_finding, finish_report, declare_metric, declare_assumption).

## 4. Skill inventory

| Skill | Domain | Notes |
| --- | --- | --- |
| funnel-analysis | data-analysis | funnel stages |
| cohort-analysis | data-analysis | cohort retention |
| rfm-segmentation | data-analysis | RFM segments |
| rd-efficiency | domain | RD efficiency metrics |

Single-object vs collection semantics are NOT contractually distinguished; ranking/TopN
is handled by the deterministic `sort` operation only when the goal regex matches.
Skill preconditions (required columns) are prose in SKILL.md, not machine-checkable (gap).

## 5. Data semantic inventory

| Capability | Status | Evidence |
| --- | --- | --- |
| Metric registry | EXISTING_PARTIAL | `langgraph_langchain/semantic/models.py` SemanticMetric (id, allowed_dimensions, additivity, unit) — WrenAI-style, provider-injected |
| Dimension registry | EXISTING_PARTIAL | SemanticDimension (id, synonyms, semantic_role) — same caveat |
| Entity/grain | EXISTING_PARTIAL | SemanticEntity.grain — model only |
| Relationship | EXISTING_UNRELIABLE | SemanticRelationship (from_entity/to_entity/cardinality) — no drill-down semantics, no verification status |
| Hierarchy | NOT_IMPLEMENTED | no parent/child relationship contract |
| Drill-down validation | NOT_IMPLEMENTED | none |
| Metric-Dimension compatibility | EXISTING_UNRELIABLE | SemanticMetric.allowed_dimensions exists but nothing enforces it deterministically |
| Discovery | NOT_IMPLEMENTED | no column profiling / candidate derivation |
| Resolution | EXISTING_PARTIAL | providers resolve a whole question -> SemanticResolution; no per-term metric/dimension resolution with provenance |

Trusted-source discipline: LLM/semantic providers can inject context but cannot create
Trusted definitions today (good baseline to preserve).

## 6. Evidence & verification

- Deterministic VerificationPolicy (contract-driven checks per task type).
- EvidenceCollector only after verification passes and artifacts exist.
- 50-case golden evaluation with numeric ground truth + weighted scoring; NO negative
  cases (V1.1 audit F-5).

## 7. Existing capability matrix (P0)

| Capability | Status |
| --- | --- |
| Intent understanding | NOT_IMPLEMENTED (goal regexes inside planner only) |
| Semantic model | EXISTING_PARTIAL |
| Constraint-aware planning | NOT_IMPLEMENTED (plan validation is structural, not semantic) |
| Dynamic exploration | EXISTING_PARTIAL (tools exist; no question-driven exploration loop) |
| Multi-turn orchestration | EXISTING_PARTIAL (sequential plan; no revision) |
| Evidence reasoning | EXISTING_PARTIAL (evidence exists; no claim binding) |
| Quality evaluation | EXISTING_PARTIAL (positive cases only) |

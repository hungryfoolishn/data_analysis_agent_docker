# R2.5 Legacy Capability Analysis

Grounded in real code at `36a3900` plus the R2 inventory. Each capability below is the V1.1
Stage E checklist; the verdict is what the code and tests actually show.

## Analysis

| Capability | Code anchors | Legacy mechanism | V2 mechanism | Verdict |
| --- | --- | --- | --- | --- |
| Dynamic data exploration | `langgraph_agent.py` ReAct loop; `_run_runtime_v2_graph_stream`; `execution/python_task_executor.py`; new `runtime/capability_adapter.py` | ReAct tool loop bounded only by `recursion_limit: 60` | Plan steps + python executor; structured calls bounded by `ToolCallBudget` | Migrated with governance (new) |
| Dynamic schema exploration | `tools/tool_load_data.py`; `tools/tool_eda_profile.py`; stage hooks in `on_task_finish` | load_data + eda_profile + ad-hoc inspection | Same real tools via structured executor; stages advance identically | Preserved |
| Multi-round tool calls | `StructuredTaskExecutor.execute` (budget consume added); `runtime.build_execution_controller` | Native ReAct multi-round | Sequential plan steps, one shared budget per V2 stream | Migrated with governance (new) |
| Intermediate result inspection | `session.ns`; `runtime.execution_namespace = session.ns`; `register_session_artifact` | Persistent python namespace | Same namespace + artifact registration | Preserved |
| Context-aware tool selection | `runtime/plans.py`; `runtime/skill_retriever.py`; react executor bridge | LLM-driven per-turn selection | Planner-generated plan; `react_*` steps still available | Preserved (selection intelligence remains R3) |
| Tool result feedback | `StructuredTaskExecutor._prepare_arguments` (output binding); `runtime.record_execution_result` | ReAct observations | Recorded execution results, output assets extracted | Preserved (automated plan revision deferred to R3) |
| Iterative analysis | Budget adapter + plan scheduler | ReAct iteration | Sequential plan under fail-closed budget | Migrated with governance |
| Dynamic query | `tools/tool_sqlite_query.py` | Ad-hoc SQL | Structured executor supports the same tool | Preserved |
| Plan adaptation | `_run_runtime_v2_graph_stream` (plan validated once) | Deviation-with-disclosure | Fixed plan; react steps as controlled escape hatch | Partially preserved; full adaptive planning assigned to R3 |
| Multi-step reasoning | `runtime/scheduler` (sequential), plan dependency graph | ReAct reasoning | Same step ordering, same real tools | Preserved |
| Failure recovery | `on_task_finish`; `_set_runtime_run_status`; strict-mode policy in `_run_runtime_v2_graph_stream` docstring | Retry/resume, consecutive-error accounting | Failed/needs_revision recorded; strict mode hard-stop; infra-only fallback | Preserved (no recovery bypass) |
| Reporting | `runtime/analysis_report.py`; `finish_report` gate | finish_report + synthesis | build/validate/render report + verified-evidence gate | Preserved |

## Explicitly not migrated (by design)

1. Unbounded ReAct-style exploration inside V2. V2 exploration must be bounded; the
   `ToolCallBudget` converts the Legacy "explore until the recursion limit" pattern into an
   explicit, auditable budget that fails closed.
2. Legacy unsafe execution mechanics. No Legacy code path is copied into V2; V2 keeps the R0a
   execution boundary, R0b identity/ownership checks, and the unified Evidence pipeline.

## Migration principles verification

- Tool calls enter unified governance: budget consume happens before tool resolution inside
  `StructuredTaskExecutor.execute`; results still flow through `build_execution_result`.
- Data access boundary unchanged: tools are the same objects; workspace/ownership checks are
  the R0a/R0b implementations, untouched by R2.5.
- Evidence lifecycle unchanged: `EvidenceCollector` / verification policy wiring untouched.
- Multi-round calls bounded: single budget per V2 stream, `max(24, len(plan.steps)*3)`.
- Infinite-loop prevention: budget exhaustion raises `CapabilityBudgetExceededError` before
  any tool side effect (tested).
- Context lifecycle/size: unchanged python namespace semantics; R3 owns size limits.
- Plan updates have explicit state changes: R2.5 introduces no plan mutation path.

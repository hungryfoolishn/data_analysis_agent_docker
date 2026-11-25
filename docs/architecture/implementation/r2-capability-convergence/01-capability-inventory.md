# R2 Capability Inventory

This inventory is generated from the actual tool registry, Runtime modules and Domain Pack
contracts at HEAD `689744c`. It is locked by `tests/test_capability_inventory.py`.

## Tool capabilities

| Capability ID | Name | Entry | Purpose | Legacy ReAct | Runtime V2 | Evidence | Tests / notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| CAP-EXEC-001 | python_repl | `tools/tool_python_repl.py` | Persistent user-code execution | Native | Via Python executor path | Artifacts/evidence via runtime integration | R0a execution policy and Docker E2E |
| CAP-EXEC-002 | delegate_analysis | `tools/tool_delegate.py` | Fresh-namespace focused sub-task | Native | Uses governed worker job | Artifacts returned; evidence not auto-promoted | Docker worker integration tests |
| CAP-EXEC-003 | run_analysis_operation | `tools/tool_analysis_operation.py`, `runtime/analysis_operations.py` | Deterministic dataframe operation | Native | Native structured executor | Runtime artifact/evidence | Operation/output-binding suites |
| CAP-DATA-001 | load_data | `tools/tool_load_data.py`, `runtime/analysis_workflow.py` | File load, schema preview, asset registration | Native | Native | DataAsset content hash | File/asset resolver suites |
| CAP-DATA-002 | run_sqlite_query | `tools/tool_sqlite_query.py`, `runtime/sqlite_workflow.py` | Read-only SQL and snapshot-backed asset | Native | Native | Snapshot-backed DataAsset | SQLite query/asset suites |
| CAP-EDA-001 | eda_profile | `tools/tool_eda_profile.py` | Automated EDA and charts | Native | Legacy-only until parity | Charts/artifacts; findings require tool calls | EDA suites |
| CAP-ANALYSIS-001 | compare_groups | `tools/tool_analysis_methods.py` | Group comparison and artifact | Native | Method candidate | Artifact/evidence via runtime | Method suites |
| CAP-ANALYSIS-002 | analyze_time_trend | same | Period trend | Native | Method candidate | same | Method suites |
| CAP-ANALYSIS-003 | decompose_contribution | same | Arithmetic attribution | Native | Method candidate | same | Method suites |
| CAP-ANALYSIS-004 | detect_anomalies | same / `analysis_operations` | IQR anomaly detection | Native | Native | same | Analysis operation suites |
| CAP-ANALYSIS-005 | profile_distribution | same | Distribution profile | Native | Method candidate | same | Method suites |
| CAP-ANALYSIS-006 | analyze_correlation | same | Association, non-causal | Native | Method candidate | same | Method suites |
| CAP-ANALYSIS-007 | compare_periods | same | Adjacent-period comparison | Native | Method candidate | same | Method suites |
| CAP-ANALYSIS-008 | calculate_ratio | same | Explicit ratio | Native | Method candidate | same | Method suites |
| CAP-ANALYSIS-009 | analyze_funnel | same | Ordered funnel | Native | Method candidate | same | Method suites |
| CAP-ANALYSIS-010 | analyze_retention | same | Cohort retention | Native | Method candidate | same | Method suites |
| CAP-ANALYSIS-011 | analyze_concentration | same | Top-N/HHI | Native | Method candidate | same | Method suites |
| CAP-ANALYSIS-012 | test_group_difference | same | Descriptive effect size | Native | Method candidate | same | Method suites |
| CAP-ANALYSIS-013 | run_sensitivity_check | same | Sensitivity check | Native | Method candidate | same | Method suites |
| CAP-GOV-001 | record_finding | `tools/tool_record_finding.py` | Structured finding/evidence | Native | Native | R0a trust gate | R0a Gate N16 |
| CAP-GOV-002 | finish_report | `tools/tool_finish_report.py` | Report submission/validation | Native | Native | Report gate | Report validator suites |
| CAP-GOV-003 | declare_metric | `tools/tool_declare_metric.py` | Metric definition | Native | Runtime model supported | Finding lineage | Metric suites |
| CAP-GOV-004 | declare_assumption | `tools/tool_declare_assumption.py` | Assumption record | Native | Runtime model supported | Finding lineage | Assumption suites |
| CAP-SKILL-001 | skill_view | `tools/tool_skills.py`, `runtime/skill_retriever.py` | Load skill instructions | Native | Retrieval available | n/a | Skill retriever suites |
| CAP-SKILL-002 | skill_reference | same | Load reference document | Native | Retrieval available | n/a | Skill retriever suites |
| CAP-MEMORY-001 | memory | `tools/tool_memory.py`, `runtime/learning.py` | Session/cross-session notes | Native | Learning memory supported | State, not Evidence | Learning suites |

## Runtime/platform capabilities

| Capability | Entry | Status | Evidence / tests |
| --- | --- | --- | --- |
| Runtime planning | `runtime/general_planner.py`, `runtime/plans.py`, `runtime/scheduler.py` | V2 native; V1 compatibility | Runtime golden/planner suites |
| Task graph/state machine | `runtime/models.py`, `runtime/scheduler.py`, `runtime/context.py` | V2 native; frozen contract tests | Scheduler/restore suites |
| Structured execution | `execution/structured_executor.py`, `runtime/executor.py` | V2 native | Structured executor suites |
| Governed Python execution | `execution/python_task_executor.py`, `execution/worker_dispatcher.py` | Shared | R0a policy/Docker E2E |
| Evidence verification | `evidence/registry.py`, `runtime/context.py` | Shared | R0a/R1 trust suites |
| Report rebuild | `runtime/report_rebuild.py` | Shared | Report rebuild suites |
| Run history/package | `runtime/history.py`, `runtime/package.py` | Shared | Run history/rebuild suites |
| Evaluation | `runtime/evaluation/*` | V2/evaluation platform | Evaluation suites |
| Learning memory | `runtime/learning.py` | Shared/state channel | Learning suites |
| Multi-agent orchestration | `runtime/multi_agent.py` | B11 frozen, V2-based | B11 suites |

## Domain capabilities

| Pack | Contract / entry | Capabilities | Status |
| --- | --- | --- | --- |
| Financial | `runtime/financial/pack.py`; B10 frozen code | financial_models, financial_normalization | Registered Domain Pack contract; frozen |
| Macro | `runtime/macro/*`; B9 frozen code | macro intent, transforms, comparison, statistics | Capability code present; not a DomainPackContract yet |
| B8 Acquisition | `data/` acquisition modules and contracts | source acquisition/history/provenance | Frozen platform capability, not yet R4 unified |
| China Financial PDF | `runtime/financial/china_pdf.py`, adapter | China PDF ingestion/validation | B10.9 production path; financial pack scope |
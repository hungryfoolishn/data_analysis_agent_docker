# Phase 0 Module Classification

Classification basis: actual imports, runtime call paths, and current tests.
Originally reviewed at commit `5314055`; reconciled with the current code by
the B0 review at branch `codex/phase1-m2.4-sqlite-schema`, commit `0f4001e`.

## Legend

| Category | Meaning |
| --- | --- |
| General Runtime | Useful for any tabular data analysis task |
| Financial Domain | Finance-specific semantics or execution |
| Reusable But Coupled | Correct capability, wrong location or excessive coupling |
| Legacy / Experimental | Keep for now, but do not expand |
| Blocking Issue | Blocks Phase 1 and must be addressed in Phase 1 |

## General Runtime — Keep And Stabilize

| Module | Responsibility | Notes |
| --- | --- | --- |
| `langgraph_langchain/data/sources.py` | File, memory, and query-result data source contract | Core Phase 1 foundation |
| `langgraph_langchain/data/database.py` | SQLite read-only connection, introspection, bounded execution (M2.4/M2.5) | Keep; added after the original review |
| `langgraph_langchain/data/snapshots.py` | Immutable Parquet SnapshotStore for query-result DataAssets (M2.7.2) | Keep; not yet consumed by the Executor (M2.7.3) |
| `langgraph_langchain/data/assets.py` | Data assets and schema snapshots | Keep |
| `langgraph_langchain/data/cache.py` | Data cache | Keep |
| `langgraph_langchain/data/sampling.py` | Deterministic sampling | Keep |
| `langgraph_langchain/runtime/plans.py` | Generic `AnalysisPlan` model and validation | Keep |
| `langgraph_langchain/runtime/models.py` | Runtime plan steps, tasks, executions, artifacts | Keep |
| `langgraph_langchain/runtime/scheduler.py` | Task scheduling | Keep |
| `langgraph_langchain/runtime/runner.py` | Task runner | Keep |
| `langgraph_langchain/runtime/graph.py` | Runtime V2 LangGraph controller | Keep |
| `langgraph_langchain/runtime/context.py` | `AnalysisRuntime` state and orchestration; registers SQLite results with verified snapshots (M2.6/M2.7.2) | Large; needs Phase 1 refactor |
| `langgraph_langchain/execution/structured_executor.py` | Deterministic structured tool execution | Keep |
| `langgraph_langchain/execution/python_executor.py` | Isolated Python execution | Keep |
| `langgraph_langchain/execution/python_task_executor.py` | Runtime Python task adapter | Keep |
| `langgraph_langchain/verification/verifier.py` | Deterministic verification checks | Platform core |
| `langgraph_langchain/evidence/collector.py` | Evidence creation from verified executions | Platform core |
| `langgraph_langchain/runtime/finding_builder.py` | Findings from verified evidence | Platform core |
| `langgraph_langchain/runtime/report_claims.py` | Report claim lineage | Platform core |
| `langgraph_langchain/runtime/report_rebuild.py` | Rebuild report from evidence | Platform core |
| `langgraph_langchain/runtime/package.py` | Audit package | Platform core |
| `langgraph_langchain/runtime/history.py` | Run history | Keep |
| `langgraph_langchain/runtime/sqlite_store.py` | Metadata persistence | Keep |
| `langgraph_langchain/tools/tool_load_data.py` | File loading tool | General |
| `langgraph_langchain/tools/tool_eda_profile.py` | Data profiling tool | General |
| `langgraph_langchain/tools/tool_analysis_methods.py` | Generic tabular analysis methods | General |
| `langgraph_langchain/tools/tool_python_repl.py` | Python execution tool | General |
| `langgraph_langchain/tools/tool_record_finding.py` | Evidence-backed findings | General |
| `langgraph_langchain/tools/tool_declare_metric.py` | Metric definition | General |
| `langgraph_langchain/tools/tool_declare_assumption.py` | Assumptions | General |
| `langgraph_langchain/tools/tool_finish_report.py` | Report generation and validation | General but oversized |
| `langgraph_langchain/api_server_langgraph.py` | HTTP API | General but oversized |
| `webui/app.py` | Current UI | Keep for MVP |

## Financial Domain — Move To Financial Domain Pack Later

| Module | Responsibility | Phase 1 Action |
| --- | --- | --- |
| `langgraph_langchain/runtime/financial/data_service.py` | Financial statements service | Freeze |
| `langgraph_langchain/runtime/financial/metrics.py` | Financial metric engine | Freeze |
| `langgraph_langchain/runtime/financial/period.py` | Financial period semantics | Freeze |
| `langgraph_langchain/runtime/financial/period_basis.py` | Cumulative/single-period conversion | Freeze |
| `langgraph_langchain/runtime/financial/workflow.py` | Financial analysis workflow | Freeze |
| `langgraph_langchain/runtime/financial/verification_engine.py` | Financial verification | Freeze |
| `langgraph_langchain/runtime/financial/evidence.py` | Financial evidence | Freeze |
| `langgraph_langchain/runtime/financial/finding_engine.py` | Financial findings | Freeze |
| `langgraph_langchain/runtime/financial/golden.py` | Financial golden tests | Freeze |
| `langgraph_langchain/runtime/financial/evaluation.py` | Financial evaluation | Freeze |
| `langgraph_langchain/runtime/financial/real_data.py` | Real financial data loader | Freeze |
| `langgraph_langchain/runtime/financial_agent/understanding.py` | Financial question understanding | Freeze |
| `langgraph_langchain/runtime/financial_agent/semantic.py` | Financial semantic resolver | Freeze |
| `langgraph_langchain/runtime/financial_agent/execution_plan.py` | Financial execution plan | Freeze |
| `langgraph_langchain/runtime/financial_agent/scheduler.py` | Financial DAG scheduler | Freeze |
| `langgraph_langchain/runtime/financial_agent/task_executor.py` | Financial task executor | Freeze |
| `langgraph_langchain/runtime/financial_agent/runtime_integration.py` | Financial runtime integration | Freeze |
| `tests/financial/**` | Financial engine tests | Freeze |
| `tests/financial_agent/**` | Financial planner/runtime tests | Freeze |

## Reusable But Coupled

| Module | Problem | Phase 1 Direction |
| --- | --- | --- |
| `langgraph_langchain/langgraph_agent.py` | 2,099-line monolith containing session setup, prompts, helper functions, tool closures, runtime bridge, fallback, recovery, and streaming | Extract modules in Phase 1 only after tests exist |
| `langgraph_langchain/api_server_langgraph.py` | 1,926-line API file mixing HTTP, sessions, uploads, plans, lineage, metrics, retries | Split routers incrementally |
| `langgraph_langchain/runtime/context.py` | 1,230-line `AnalysisRuntime` mixing state, persistence, plans, execution, findings, artifacts | Extract focused services |
| `langgraph_langchain/tools/tool_finish_report.py` | 539-line tool with validation and report contract logic | Extract validators |
| `langgraph_langchain/runtime/financial_agent` | Financial concepts inside generic `runtime` package | Move under Domain Pack in Phase 4 |
| `langgraph_langchain/runtime/financial` | Same as above | Move under Domain Pack in Phase 4 |
| `langgraph_langchain/rd_*` modules | R&D domain logic in global package | Keep isolated; do not expand |
| `langgraph_langchain/tools/tool_delegate.py` | Broad delegation capability | Audit for Phase 1 relevance |

## Legacy / Experimental

| Module / Area | Notes |
| --- | --- |
| `langgraph_langchain/test_reliability.py` | Tests inside package; move to `tests/` later |
| `langgraph_langchain/test_findings.py` | Same |
| `docs/Runtime/**` | Historical version-by-version design docs |
| `docs/roadmap/runtime-*` | Historical financial Runtime roadmap |
| `scripts/generate_financial_golden.py` | Financial helper; freeze |
| `scripts/generate_real_financial_golden.py` | Financial helper; freeze |

## Blocking Issues For Phase 1

See `phase0-issue-register.md`.

## Architecture Boundary Rule

After Phase 1, a new financial-specific import should be allowed only inside a
Financial Domain Pack. Generic Runtime modules should not import:

- financial metric registries,
- financial period semantics,
- financial report templates,
- macro data sources,
- trading/research Agent roles.

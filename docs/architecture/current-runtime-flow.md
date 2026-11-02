# Current Runtime Flow — Code Archaeology

## Baseline

- Commit: `5314055`
- Date of audit: 2026-11-02
- Audit method: code and test reading, entry-point tracing, full pytest run.
- Full test result: `930 passed, 11 warnings, 4 subtests passed`.

This document records what the code currently does, not what older design
documents claim.

## Entry Points

| Entry Point | Path | Role |
| --- | --- | --- |
| FastAPI backend | `langgraph_langchain/api_server_langgraph.py` | Upload, sessions, streaming analysis, run history, plan control, artifacts, lineage, metrics |
| Streamlit UI | `webui/app.py` | User-facing file upload and chat interface |
| Legacy ReAct agent | `langgraph_langchain/langgraph_agent.py` | Default agent execution path today |
| Runtime V2 graph | `langgraph_langchain/runtime/graph.py` | Opt-in structured plan execution graph |
| Financial Agent | `langgraph_langchain/runtime/financial_agent` | Domain-specific financial planning/execution subsystem |

## 1. Default User-facing File-analysis Path

Today the default request path is still the legacy ReAct flow:

```text
POST /v1/chat/completions
  -> api_server_langgraph.py
  -> get_or_create_session()
  -> _resolve_data_file()
  -> _run_analysis_stream()
  -> run_analysis_stream()
  -> _Session + tools + create_react_agent()
  -> agent.astream_events()
  -> load_data / eda_profile / analysis_methods / python_repl /
     record_finding / declare_metric / declare_assumption / finish_report
  -> final Markdown report + artifacts
```

### Important code locations

| Concern | Location |
| --- | --- |
| Chat endpoint | `langgraph_langchain/api_server_langgraph.py` (`/v1/chat/completions`) |
| Session upload | `langgraph_langchain/api_server_langgraph.py` (`/workspace/upload`) |
| File resolution | `_resolve_data_file()` in `api_server_langgraph.py` |
| Streaming entry | `run_analysis_stream()` in `langgraph_langchain/langgraph_agent.py` |
| Tool factory | `langgraph_langchain/tools/__init__.py` |
| File loading | `langgraph_langchain/tools/tool_load_data.py` |
| Profiling | `langgraph_langchain/tools/tool_eda_profile.py` |
| Formal analysis tools | `langgraph_langchain/tools/tool_analysis_methods.py` |
| Python execution | `langgraph_langchain/tools/tool_python_repl.py` |
| Findings | `langgraph_langchain/tools/tool_record_finding.py` |
| Final report | `langgraph_langchain/tools/tool_finish_report.py` |
| State machine | `langgraph_langchain/state_machine.py` |

## 2. Structured Runtime V2 Path

Runtime V2 exists and has substantial tests, but is opt-in.

```text
RUNTIME_V2_ENABLED=true
  -> run_analysis_stream()
  -> _run_runtime_v2_graph_stream()
  -> AnalysisPlan.model_validate(runtime.run.plan)
  -> AnalysisRuntime.initialize_plan()
  -> StructuredTaskExecutor / PythonTaskExecutor / ReactTaskExecutor
  -> VerificationPolicy
  -> EvidenceCollector
  -> RuntimeV2Controller.graph.invoke()
  -> findings + report + artifacts
```

### Important code locations

| Concern | Location |
| --- | --- |
| Runtime bridge | `_run_runtime_v2_graph_stream()` in `langgraph_agent.py` |
| Plan model | `langgraph_langchain/runtime/plans.py` |
| Runtime state | `langgraph_langchain/runtime/context.py` (`AnalysisRuntime`) |
| Scheduler | `langgraph_langchain/runtime/scheduler.py` |
| Runner | `langgraph_langchain/runtime/runner.py` |
| LangGraph controller | `langgraph_langchain/runtime/graph.py` |
| Structured tool execution | `langgraph_langchain/execution/structured_executor.py` |
| Isolated Python execution | `langgraph_langchain/execution/python_executor.py` |
| Deterministic verification | `langgraph_langchain/verification/verifier.py` |
| Evidence construction | `langgraph_langchain/evidence/collector.py` |
| Findings | `langgraph_langchain/runtime/finding_builder.py` |
| Report claims | `langgraph_langchain/runtime/report_claims.py` |
| Report rebuild | `langgraph_langchain/runtime/report_rebuild.py` |
| Audit package | `langgraph_langchain/runtime/package.py` |

### Current Runtime V2 Gaps

1. `RUNTIME_V2_ENABLED` defaults to `false` in `langgraph_langchain/config.py`.
2. The generic chat path does not automatically create a structured
   `AnalysisPlan`; Runtime V2 falls back if `runtime.run.plan` is absent.
3. `default_analysis_plan()` in `runtime/plans.py` is a fixed five-step
   compatibility plan, not a data-aware planner.
4. The structured Runtime path is mostly file-oriented today; no SQL data
   source adapter is implemented.

## 3. Data Layer

### Existing file support

`langgraph_langchain/data/sources.py` defines:

- `DataSource`
- `FileDataSource`
- `MemoryDataSource`
- `QueryResultDataSource`

Supported file formats in `FileDataSource`:

- CSV
- Excel `.xlsx` / `.xls`
- Parquet

It supports:

- Schema scanning.
- Hashing.
- Row estimation.
- Preview.
- Materialization.
- Deterministic sampling.
- Chunked CSV iteration.
- Local cache.

### Current SQL boundary

The code explicitly says SQL support is intentionally outside the current P0
contract:

```python
class DataSource(ABC):
    """Read-only data source contract. SQL support intentionally stays out of P0."""
```

There is no production database adapter, connection manager, read-only SQL
executor, or database-backed `DataSource` implementation today.

## 4. Evidence And Report Path

### Deterministic verification

`langgraph_langchain/verification/verifier.py` provides deterministic checks
such as:

- Numeric consistency.
- Artifact existence.
- Schema/shape checks.
- Time consistency.
- Report quality checks.

These are intended to block evidence creation when verification fails.

### Evidence

`langgraph_langchain/evidence/collector.py` creates `EvidenceItem` objects only
from successful executions with acceptable verification results. It records:

- Execution identity.
- Task and step identity.
- Artifact IDs.
- Input asset IDs.
- Evidence text.
- Source fields.
- Calculation method.
- Trace span ID.

### Findings And Report

`record_finding` resolves evidence references through runtime artifacts and
executions. `finish_report` validates report structure, evidence, quantitative
claims, causal claims, visualizations, and minimum findings before publishing
the final Markdown report.

## 5. Financial Subsystem

A separate financial subsystem exists under:

- `langgraph_langchain/runtime/financial`
- `langgraph_langchain/runtime/financial_agent`
- `tests/financial`
- `tests/financial_agent`

It includes:

- Financial data service.
- Metric definitions and calculation engine.
- Period normalization and comparison semantics.
- Financial semantic resolver.
- Financial execution plan and DAG scheduler.
- Financial task executor.
- Verification and evidence models.
- Financial golden datasets.

This is currently physically coupled to `langgraph_langchain.runtime` but is
conceptually a Domain Pack, not the generic Runtime.

## 6. Current Test Evidence

Command:

```bash
PYTHONPATH=. pytest -q
```

Result on docker-host, Conda `analysis`, Python 3.10:

```text
930 passed, 11 warnings, 4 subtests passed
```

Important existing suites:

| Suite | Purpose |
| --- | --- |
| `tests/test_data_sources.py` | File source scan, sampling, cache |
| `tests/runtime_v2/test_runtime_e2e.py` | Structured Runtime end-to-end execution |
| `tests/runtime_v2/test_runtime_v7_trusted_analysis.py` | Verified evidence lineage |
| `tests/test_plan_control.py` | Plan pause/confirm/resume |
| `tests/test_state_machine_enforcement.py` | Tool sequencing constraints |
| `tests/test_evidence_binding_integration.py` | Finding/evidence binding |
| `tests/financial_agent` | Financial planner/runtime semantics |
| `tests/financial` | Financial engine and golden tests |

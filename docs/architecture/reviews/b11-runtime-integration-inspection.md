# B11.0 Runtime Production Integration Inspection

Status: inspection only
Baseline: `1524c6c94e50e7b019f159263934f4890138ba26` (`1524c6c`)
Branch: `codex/phase1-m2.4-sqlite-schema`
Scope: real call-path inspection for file, SQLite, Adapter, Evidence, Report, API/SSE/UI.
Constraint honored: no business code was modified.

## 1. Executive conclusion

B4-B8 modules are substantial, but the product path is still a hybrid:

- Session initialization now registers file/SQLite DataAssets and creates a structured plan.
- Runtime V2 can execute `run_analysis_operation` and `run_sqlite_query` when `RUNTIME_V2_ENABLED=true`.
- The deterministic file/SQLite plans do not yet guarantee a report step, so a successful tool plan can still end without `session.report`.
- The B7 `AnalysisReport` model is not used by the real Runtime/API path.
- Public-data Adapter output is not yet connected to an ingestion service or Runtime DataAsset.
- API background tasks can mark a stream as completed even when Runtime V2 reported failure or fallback.
- API cancellation is not propagated through Runtime V2.

Therefore B11 is necessary before any default-switch discussion.

## 2. Inspection baseline

- Branch: `codex/phase1-m2.4-sqlite-schema`
- HEAD: `1524c6c94e50e7b019f159263934f4890138ba26`
- Remote: same commit at inspection time.
- B10.1 fix was present.
- No business code was changed for this inspection.

Primary files reviewed:

- `langgraph_langchain/api_server_langgraph.py`
- `langgraph_langchain/langgraph_agent.py`
- `langgraph_langchain/runtime/context.py`
- `langgraph_langchain/runtime/graph.py`
- `langgraph_langchain/runtime/analysis_workflow.py`
- `langgraph_langchain/runtime/sqlite_workflow.py`
- `langgraph_langchain/runtime/analysis_operations.py`
- `langgraph_langchain/runtime/general_planner.py`
- `langgraph_langchain/runtime/analysis_report.py`
- `langgraph_langchain/tools/tool_analysis_operation.py`
- `langgraph_langchain/tools/tool_sqlite_query.py`
- `langgraph_langchain/tools/tool_finish_report.py`
- `langgraph_langchain/tools/registry.py`
- `langgraph_langchain/data/adapters/base.py`
- `langgraph_langchain/data/adapters/registry.py`
- `langgraph_langchain/data/adapters/public.py`

## 3. Current real architecture

This is the actual architecture, not the target architecture.

```text
POST /v1/chat/completions
  or POST /analysis/tasks
      |
      v
api_server_langgraph._run_analysis_stream()
      |
      v
langgraph_agent.run_analysis_stream()
      |
      +--> _Session.__init__()
      |       |
      |       +--> AnalysisRuntime(..., plan_first=True)
      |       |
      |       +--> file suffix:
      |       |       prepare_file_analysis()
      |       |
      |       +--> .sqlite / .db suffix:
      |               prepare_sqlite_analysis()
      |
      +--> if RUNTIME_V2_ENABLED:
      |       _run_runtime_v2_graph_stream()
      |
      +--> otherwise:
              legacy ReAct agent stream
```

Session initialization is common to both paths. Therefore a plan and assets are created even when the request later executes in Legacy mode. Creating the plan is not the same as executing it.

## 4. Runtime selection

Current selection is direct configuration checking:

```text
langgraph_agent.run_analysis_stream()
  -> _RUNTIME_V2_ENABLED
  -> true:  _run_runtime_v2_graph_stream()
  -> false: legacy ReAct stream
```

`config.RUNTIME_V2_ENABLED` remains `false` by default. This was intentional in B11 and must not change during B11.1.

`runtime/runtime_route.py` contains `RuntimeSelectionPolicy`, `decide_runtime_route()`, and `log_runtime_route()`, but the real API/Agent path still reads `_RUNTIME_V2_ENABLED` and `_RUNTIME_V2_FALLBACK_ENABLED` directly. Strict mode is therefore not wired into the real route.

## 5. Legacy execution chain

When Runtime V2 is disabled:

```text
_Session
  -> AnalysisRuntime + DataAsset + structured plan
  -> _make_tools(session)
  -> tools.registry.get_tools_for_session()
  -> ReAct agent stream
  -> LLM chooses tools
  -> load_data / run_analysis_operation / run_sqlite_query / record_finding / finish_report
  -> SessionExecutionRecorder
  -> legacy Finding / EvidenceItem
  -> finish_report
  -> runtime.report_validator.validate_report()
  -> session.report
  -> data_analysis_report.md
```

State classification:

- `finish_report()` can set `session.report`.
- Legacy stream checks cancellation only in the ReAct event loop.
- API background tasks currently set `task["status"] = "completed"` after the generator finishes, even when the stream reported Runtime failure or fallback.

Legacy execution is the only path that currently reaches the real `finish_report()` and legacy report validator by design.

## 6. Runtime V2 execution chain

When Runtime V2 is enabled:

```text
_Session
  -> runtime.run.plan and registered assets
  -> _run_runtime_v2_graph_stream()
  -> AnalysisPlan.model_validate(runtime.run.plan)
  -> runtime.initialize_plan(plan)
  -> tool_map built from tools.registry
  -> unsupported tool check
  -> runtime.build_execution_controller()
      -> StructuredTaskExecutor
      -> ReactTaskExecutor
      -> PythonTaskExecutor
      -> VerificationPolicy
      -> EvidenceCollector
  -> TaskRunner
      -> AnalysisRuntime.build_execution_request()
          -> AssetResolver.resolve_many(task.input_asset_ids)
          -> runtime_inputs / runtime_input_metadata
      -> TaskExecutor.execute()
      -> StructuredTaskExecutor invokes mapped tool
      -> build_execution_result()
  -> on_task_finish()
      -> runtime.record_execution_result()
  -> all tasks succeeded?
  -> session.report?
```

### File path

`_Session.__init__()` calls:

```text
prepare_file_analysis()
  -> FileDataSource.scan_schema()
  -> FileDataSource.materialize(max_rows=...)
  -> AnalysisRuntime.register_dataframe()
  -> DeterministicAnalysisPlanner().plan()
  -> validate_general_plan()
  -> general_plan_to_runtime_plan()
  -> AnalysisRuntime.initialize_plan()
```

The generated Runtime step method is `run_analysis_operation`. Runtime V2 can call it through `StructuredTaskExecutor`.

`tool_analysis_operation.run_analysis_operation` then:

1. resolves the input DataAsset with `AssetResolver`;
2. executes an allowlisted operation;
3. writes Parquet output;
4. registers an output DataAsset;
5. registers a RuntimeArtifact;
6. records execution through `SessionExecutionRecorder`.

This is implemented and called by Runtime V2, but B11.0 found no real API/SSE E2E that proves the full chain to a user-facing report.

### SQLite path

`_Session.__init__()` calls:

```text
prepare_sqlite_analysis()
  -> SQLiteDataSource.introspect_schema()
  -> register sqlite_database DataAsset
  -> build_sqlite_plan()
  -> AnalysisRuntime.initialize_plan()
```

The generated step method is `run_sqlite_query`. Runtime V2 can call it through `StructuredTaskExecutor`.

`tool_sqlite_query.run_sqlite_query` then:

1. loads the database asset by ID;
2. opens a bounded read-only SQLite source;
3. executes SQL through `SQLiteQueryTool`;
4. registers QueryResultAsset and SnapshotStore snapshot;
5. writes `SqliteQueryEvidence` as an artifact;
6. records execution through `SessionExecutionRecorder`.

However, if the task's `input_asset_ids` contains the `sqlite_database` asset, `AssetResolver` rejects that source type. This is acceptable for the SQL step itself, because SQL takes an asset ID argument, but it means `runtime_inputs` cannot expose the database and the input contract is inconsistent.

### Evidence / Report gap

The deterministic file/SQLite plan contains only analysis/query steps. It does not include:

- `record_finding`;
- `finish_report`;
- a report-producing task.

After all analysis tasks succeed, `_run_runtime_v2_graph_stream()` checks `session.report`. If no report exists, it either marks fallback or returns failure depending on configuration. Thus the current deterministic Runtime V2 chain can execute tools but cannot yet reliably complete the product workflow.

`finish_report()` uses:

- `runtime.report_validator.validate_report()`;
- legacy `Finding` / `EvidenceItem` records;
- Markdown output.

The B7 `AnalysisReport` / `validate_analysis_report()` model is not called by either Legacy or Runtime V2.

## 7. Adapter chain

Current Adapter state:

```text
DataSourceAdapter.fetch()
  -> cache lookup
  -> _fetch_raw()
  -> normalize()
  -> validate()
  -> FetchResult
```

`AdapterRegistry.fetch()` dispatches by dataset ID. `DatasetCatalog` can list entries. Four adapters are registered in tests.

Gaps:

- There is no `DataIngestionService`.
- No API/UI entry constructs `FetchRequest` or calls an Adapter.
- No business path persists `FetchResult.dataframe` and registers a DataAsset.
- `AdapterRegistry.fetch()` does not pass `refresh=True` to the underlying adapter, so Registry-level manual refresh is broken.
- Adapter catalog persistence is not implemented.
- No real public API smoke test was executed in this environment.

## 8. Evidence and report state

Evidence and report have three separate generations:

| Generation | Exists | Real usage |
| --- | ---: | --- |
| Legacy `EvidenceItem` / `Finding` | Yes | Used by `finish_report`, Legacy findings, Runtime evidence hooks |
| SQLite `SqliteQueryEvidence` | Yes | Written by `run_sqlite_query` as artifact; not converted to legacy EvidenceItem or added to Runtime evidence registry |
| B7 `AnalysisReport` / `validate_analysis_report()` | Yes | Not used by Legacy or Runtime V2 |

Consequently, the product does not yet have one canonical Evidence/Report contract.

## 9. API / SSE / Streamlit state

API task flow:

```text
POST /analysis/tasks
  -> task status running
  -> asyncio.create_task(_background_analysis_task())
  -> _run_analysis_stream()
  -> task events appended
  -> run_id discovered from runtime JSON
  -> generator returns
  -> task["status"] = "completed"
```

Gaps:

- API task status is inferred from stream completion, not from Runtime outcome.
- Runtime failure, fallback, or missing report can still become API `completed`.
- There is no `partial_success`.
- API cancellation sets an event, but Runtime V2 does not consume it.
- Backend restart marks in-flight tasks failed as non-recoverable.
- Streamlit receives SSE chunks and artifacts, but does not expose a unified `AnalysisTaskOutcome`.

## 10. Implementation / registration / call / E2E matrix

| Capability | Implemented | Registered | Called by real path | E2E verified |
| --- | ---: | ---: | ---: | ---: |
| File DataAsset registration | Yes | N/A | Yes during `_Session` | Partial |
| General Planner | Yes | N/A | Yes during `_Session` for file input | Partial |
| General plan validation | Yes | N/A | Yes inside planner | No real API E2E |
| Runtime Plan conversion | Yes | N/A | Yes for file input | Partial |
| `run_analysis_operation` tool | Yes | Yes | Yes when Runtime V2 enabled | No full report E2E |
| SQLite schema planner | Yes | N/A | Yes during `_Session` | No real API E2E |
| `run_sqlite_query` tool | Yes | Yes | Yes when Runtime V2 enabled | No full report E2E |
| SnapshotStore | Yes | N/A | Yes for SQLite query result | Unit/integration only |
| SQLite evidence | Yes | N/A | Yes in SQL tool | Unit/integration only |
| Legacy EvidenceItem | Yes | N/A | Yes via Runtime hook / Legacy tools | Yes in Runtime V2 tests |
| B7 AnalysisReport | Yes | N/A | No | No |
| Adapter fetch | Yes | Partial | No | Mock only |
| Adapter ingestion | No | N/A | No | No |
| Runtime route policy | Yes | N/A | No | Unit only |
| Cancellation propagation | Partial | N/A | Legacy only | No Runtime V2 E2E |
| API task outcome | Partial | N/A | Yes | Gap |

Legend: "Partial" means the capability is covered by focused tests or one segment, not the complete real API-to-report path.

## 11. Findings

### P1

| ID | Issue | Location | Current behavior | Expected behavior | Suggested stage |
| --- | --- | --- | --- | --- | --- |
| B11-P1-01 | Deterministic Runtime plans do not guarantee a report-producing step. | `runtime/analysis_workflow.py`, `runtime/sqlite_workflow.py`, `langgraph_agent.py:_run_runtime_v2_graph_stream()` | All analysis steps can succeed but `session.report` remains empty, causing fallback/failure. | A valid Runtime plan must end in a report-producing step or Runtime must synthesize a validated report from execution evidence. | B11.1 |
| B11-P1-02 | B7 `AnalysisReport` is not integrated. | `runtime/analysis_report.py`, `tools/tool_finish_report.py`, `langgraph_agent.py` | Real path uses legacy Markdown and legacy validator. | One canonical report builder/validator should be used and exposed to API/UI. | B11.1 |
| B11-P1-03 | Evidence models are fragmented. | `runtime/context.py`, `tools/tool_sqlite_query.py`, `runtime/analysis_report.py` | SQLite evidence becomes artifact; operation output has no unified evidence; Runtime may create legacy evidence separately. | Every successful analysis/query step should create one canonical Evidence record linked to execution/assets. | B11.2 |
| B11-P1-04 | Multi-step operation output is not propagated as a dependent task input. | `runtime/analysis_workflow.py`, `runtime/general_planner.py`, `runtime/context.py` | Dependent steps still reference original inputs; output asset IDs are not dynamically injected. | Next step should resolve the prior step's output DataAsset. | B11.2 |
| B11-P1-05 | SQLite database assets cannot be resolved by AssetResolver. | `runtime/asset_resolver.py`, `runtime/sqlite_workflow.py` | SQL tool can use the asset ID directly, but `build_execution_request()` fails if that asset is in `task.input_asset_ids`. | Resolver should distinguish database source assets from query-result snapshot assets and expose safe schema metadata. | B11.3 |
| B11-P1-06 | API task status is not derived from Runtime outcome. | `api_server_langgraph.py:_background_analysis_task()` | Stream end marks API task completed regardless of Runtime failure/fallback/missing report. | Derive status from Runtime run, step outcomes, evidence, report, and cancellation. | B11.4 |
| B11-P1-07 | API cancellation is not propagated to Runtime V2. | `api_server_langgraph.py`, `langgraph_agent.py`, `runtime/context.py`, `execution/*` | Cancel endpoint sets an event ignored by V2. | Cancel token should reach TaskRunner and current executor. | B12 |

### P2

| ID | Issue | Location | Suggested stage |
| --- | --- | --- | --- |
| B11-P2-01 | Runtime route policy is not wired into real API path. | `runtime/runtime_route.py`, `langgraph_agent.py` | B11.1 |
| B11-P2-02 | Planner failure is caught and can fall back silently to a generic inspect/data-quality plan. | `langgraph_agent.py:_Session.__init__()` | B11.1 |
| B11-P2-03 | Plan initialization does not run the legacy `validate_plan()` tool whitelist. | `runtime/context.py:initialize_plan()` | B11.1 |
| B11-P2-04 | Adapter Registry does not forward `refresh`. | `data/adapters/registry.py:fetch()` | B11.4 |
| B11-P2-05 | Adapter output has no ingestion service. | `data/adapters/*` | B11.4 |
| B11-P2-06 | Adapter catalog has no persistence. | `data/adapters/registry.py` | B11.4 |
| B11-P2-07 | SQLite database asset registration uses an empty preview DataFrame before mutating to `sqlite_database`. | `runtime/sqlite_workflow.py` | B11.3 |
| B11-P2-08 | SEC adapter does not set SEC-required User-Agent. | `data/adapters/public.py` | B11.4 |
| B11-P2-09 | API does not expose a unified report ID/outcome object. | `api_server_langgraph.py` | B11.4 or B12 |

### P3

| ID | Issue |
| --- | --- |
| B11-P3-01 | FRED adapter contains an unused placeholder import related to credentials. |
| B11-P3-02 | Some legacy and new report terms overlap and should be renamed during migration. |
| B11-P3-03 | Golden deterministic harness is not a real LLM/API harness and should be labeled accordingly. |

No P0 was found in this inspection. The most serious issues are P1 integration gaps rather than security regressions.

## 12. Target architecture for B11

This is the B11 target, not current state.

```text
API/UI request
  |
  v
RuntimeSelectionPolicy
  |
  v
Unified Analysis Workflow
  |
  +--> FileDataSource -> File DataAsset
  +--> SQLite database -> Database Asset + Schema Context
  +--> Adapter FetchResult -> Ingestion Service -> DataAsset
  |
  v
Data Understanding
  |
  v
General Planner / SQLite Planner
  |
  v
General Plan Validation
  |
  v
Legacy AnalysisPlan Validation
  |
  v
Runtime V2 TaskRunner
  |
  +--> run_analysis_operation
  +--> run_sqlite_query
  +--> report/finalize step
  |
  v
Execution + Output DataAsset + Artifact
  |
  v
Canonical Evidence
  |
  v
AnalysisReport Builder + Validator
  |
  v
API Task Outcome + SSE/UI
```

## 13. Suggested B11 implementation split

### B11.1 Runtime selection, planning, and report closure

Scope:

- Route the real API path through `RuntimeSelectionPolicy` without changing defaults.
- Ensure all generated plans pass both General Plan validation and legacy tool-registry validation.
- Add a report/finalize step or deterministic report synthesis to Runtime V2.
- Integrate one canonical report builder/validator into Runtime V2.
- Prevent silent fallback when planner fails; return a clear clarification or failure.

Do not change `RUNTIME_V2_ENABLED`.

### B11.2 File and deterministic analysis chain

Scope:

- Preserve output DataAsset IDs as task outputs.
- Inject prior-step output assets into dependent tasks.
- Create canonical evidence for each successful operation.
- Add real API/SSE integration tests for CSV, Excel, and Parquet.
- Verify failure blocks dependent steps and prevents false success reports.

### B11.3 SQLite chain

Scope:

- Introduce a proper database-source asset registration model.
- Make SQL tool input use a database asset reference.
- Register query result as a snapshot-backed DataAsset.
- Create canonical SQL Evidence linked to execution/source/output.
- Allow later deterministic analysis to consume the snapshot, not re-run SQL.
- Add real API/SSE tests for SELECT, CTE, JOIN, aggregate, and truncation.

### B11.4 Adapter ingestion

Scope:

- Implement `DataIngestionService`.
- Forward `refresh` through `AdapterRegistry.fetch()`.
- Persist adapter output as Parquet or another supported snapshot format.
- Register adapter output as a DataAsset with complete provenance.
- Persist a lightweight dataset catalog.
- Add adapter-to-Runtime ingestion tests.
- Add SEC User-Agent and credential handling validation.

## 14. Explicitly out of scope for B11

- Changing `RUNTIME_V2_ENABLED` default.
- Changing `RUNTIME_V2_FALLBACK_ENABLED` default.
- Removing Legacy ReAct.
- Rewriting the entire Agent architecture.
- Adding macroeconomic or financial domain semantics.
- Adding new public-data Adapters.
- Implementing full multi-user authorization.
- Docker deployment E2E; belongs to B13.
- Cancellation/lifecycle hardening; mainly belongs to B12, though B11 should not make it worse.
- Product default switch; belongs to B14.

## 15. Verification limitations

This B11.0 inspection was static. No test suite was modified or run as part of this document. The following remain unverified:

- Live API/SSE call with `RUNTIME_V2_ENABLED=true`.
- Real LLM-driven natural-language E2E.
- Runtime V2 cancellation behavior.
- Live public-data adapter requests.
- Docker deployment and restart recovery.
- Multi-session concurrent execution.

These should be covered by later B11 implementation tests and B13 deployment E2E.

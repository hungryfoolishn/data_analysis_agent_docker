# B4 / Phase 1A: File Analysis Runtime Chain

## Implemented chain

```text
Upload / selected file
  -> secure upload endpoint
  -> prepare_file_analysis()
     -> FileDataSource.scan_schema()
     -> DataAsset registration (bounded preview + file hash)
     -> DeterministicAnalysisPlanner()
     -> GeneralAnalysisPlan
     -> general_plan_to_runtime_plan()
     -> AnalysisRuntime.initialize_plan()
  -> Runtime V2 TaskScheduler / TaskExecutor
  -> StructuredTaskExecutor("run_analysis_operation")
  -> AssetResolver.resolve(asset_id)
  -> execute_analysis_operation()
  -> Parquet output DataAsset + RuntimeArtifact
  -> SessionExecutionRecorder
  -> Evidence / Report path
```

## Changes

- Secure upload endpoints:
  - use server-generated unique filenames;
  - sanitize uploaded filename to basename;
  - reject unsupported extensions;
  - reject overwrite;
  - accept CSV, XLSX, XLS, Parquet, SQLite, and DB.
- File DataAsset registration uses a bounded preview for planning and preserves the real file path, source hash, row estimate, and schema.
- General Planner now supports `correlation` and `data_quality` in addition to the original allowlist.
- `RuntimePlanStep.parameters` is persisted and propagated to task arguments.
- New `run_analysis_operation` tool executes only allowlisted operations and registers the output DataAsset.
- New file workflow uses AssetResolver and does not let Planner construct local paths.
- Session initialization registers the selected file asset and replaces the default plan with a validated general plan when possible; ambiguous goals fall back to inspect/data-quality.

## Acceptance tests

`tests/test_phase1_runtime_integration.py` verifies:

- file asset registration and plan input asset IDs;
- AssetResolver returns the original DataFrame;
- deterministic `group_aggregate` result is numerically correct;
- phase 1 tools are registered.

`tests/test_golden_phase1.py` freezes the golden suite and verifies catalog size, numeric correctness, evidence completeness, and report validity.

## Known boundary

Runtime V2 remains disabled by default. This batch closes a P0 upload path traversal issue, but the B9 P0 findings around default authentication and global workspace file exposure still require the dedicated B10 security phase before real multi-user upload exposure.

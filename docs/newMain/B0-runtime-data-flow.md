# B0 Runtime 数据流（当前真实链路）

Status: B0 audit。以下均为 `d11ded6` 实测调用链，非设计图。

## 1. 输入与数据理解链路

### 文件（CSV/Excel/Parquet）
```
POST /v1/files 或 /workspace/upload（api_server_langgraph.py）
  → _Session.__init__（langgraph_agent.py）
    → AnalysisRuntime（runtime/context.py）
    → prepare_file_analysis（runtime/analysis_workflow.py）
      → FileDataSource.scan_schema/materialize（data/sources.py，CSV/Excel/Parquet）
      → runtime.register_dataframe → DataAsset（data/assets.py，content_hash）
      → DeterministicAnalysisPlanner.plan（runtime/general_planner.py）
      → general_plan_to_runtime_plan → AnalysisPlan
```
- 文件类资产无快照，由 `AssetResolver._resolve_file_asset` 校验 `hash_file` 后重读（源文件变更/缺失显式失败）。

### SQLite
```
Session 初始化 → prepare_sqlite_analysis（runtime/sqlite_workflow.py）
  → register_sqlite_database_asset（source_type="sqlite_database"）
  → build_sqlite_plan（模板 SQL：趋势/TopN/质量）
```
运行期查询：
```
run_sqlite_query（tools/tool_sqlite_query.py）
  → SQLiteQueryTool.execute（tools/sqlite_sql_tool.py，只读/超时/行数上限/query_sha256）
  → runtime.register_sqlite_result（context.py）
    → SnapshotStore.save（data/snapshots.py：Parquet + pyarrow，物理 hash + DataFrame 内容 hash + 行列数，临时文件原子替换）
    → DataAsset.source_metadata["snapshot_reference"]
    （dedup：provenance+content hash 命中时复用旧资产，快照必须存在，见 _sqlite_provenance_matches）
```

## 2. DataAsset 生命周期

| 阶段 | 位置 | 行为 |
| --- | --- | --- |
| 注册 | `register_dataframe` / `register_sqlite_result`（context.py） | 内容+位置去重；sqlite 写入快照引用；持久化进 `.analysis_runtime.json` 与 `.analysis_runs/{run_id}.json` |
| 快照 | `data/snapshots.py` | 保存/加载双 hash 校验、路径 containment、失败删除 |
| 恢复 | `AssetResolver.resolve/resolve_many`（runtime/asset_resolver.py） | sqlite→快照校验加载；file→存在性+hash 校验重读；未知类型/损坏→结构化错误（不重跑 SQL） |
| 消费 | `build_execution_request()`（context.py） | 物理 ID → `namespace.runtime_inputs`；逻辑引用（`step_x.output`）→ OutputBindingRegistry → 物理 ID；产出 `ResolvedTaskInputs` |
| 产出 | 工具（B11.2.1 契约） | `register_output` 语义：`register_dataframe`/`register_sqlite_result` + JSON `output_assets` 命名映射 |
| 绑定 | `record_execution_result()`（context.py，唯一写点） | 校验（名称∈expected_outputs、资产已注册、provenance、无冲突）→ `OutputBindingRegistry.register_many`（all-or-nothing），键 `(run_id, step_id, output_name, attempt)` |
| 归档 | `.analysis_runs/{run_id}.json`（snapshot v2） | 含 assets/executions/artifacts/output_bindings/verifications/evidence/failures/evaluations；restore 时孤儿 Binding 清理 |

## 3. Runtime 执行链路

```
decide_runtime_route（runtime_route.py）——RUNTIME_V2_ENABLED 默认 False → 文件分析默认 Legacy ReAct
（V2 开启或 sqlite 显式路径时）：
initialize_plan → validate_runtime_plan（物理 ID/逻辑引用分型校验）
  → TaskScheduler.from_plan（DAG + 状态就绪 + skip 级联）
  → RuntimeV2Controller.graph（runtime/graph.py，LangGraph 薄节点）
    → AnalysisRuntime.execute_next_task（context.py）
      → TaskRunner.execute_next（runner.py）→ mark_running
      → build_execution_request：
          canonical execution_id = new_id("exec")
          _resolve_task_inputs：物理种子 / 逻辑引用（Binding 精确 attempt 匹配）
          asset_resolver.resolve_many → runtime_inputs / runtime_input_metadata
      → StructuredTaskExecutor（execution/task_models.py + structured_executor.py）
          注入 arguments["execution_id"] → 工具 invoke
          extract_artifact_ids + extract_output_assets（output_assets 命名映射）
      → record_execution_result：
          ExecutionIdMismatch 校验（canonical vs 工具记录）
          Binding 单点写入（all-or-nothing）→ input_bindings/output_assets/output_asset_ids 落库
          verification / evidence / failure payload / task_evaluation
      → 失败：demote_to_failed + skip_dependents（闭包内 pending → skipped）
```

## 4. Evidence / Report 链路

```
工具侧：collect_operation_evidence / collect_sqlite_query_evidence（evidence/）
  → canonical evidence（evidence/models.py，CanonicalEvidence）
  → runtime.register_canonical_evidence → EvidenceRegistry（evidence/registry.py）
Runtime 侧：verifier（verification/）→ verify_artifact_existence / 数值门禁
  → TaskRunner._review_result（失败即 RuntimeVerificationError）
  → EvidenceItem 挂到 ExecutionResult.evidence
报告：build_verified_report（context.py）→ build_analysis_report + validate_analysis_report
  （runtime/analysis_report.py：finding.claim 必须引用已验证 evidence、资产/工件存在性）
  → render_analysis_report；历史重建 report_rebuild.rebuild_report_markdown
评估：evaluation/golden_phase1.py（组件级 harness，见下）+ evaluation/runner.py/gate.py
```

## 5. Golden harness 的真实形态（重要边界）

`evaluation/golden_phase1.py::run_golden_suite`（10 datasets / 34 questions）的 `_run_file_case` / `_run_sqlite_case`：
- 走 **AnalysisRuntime + prepare_*_analysis + AssetResolver + execute_analysis_operation / SQLiteQueryTool + EvidenceRegistry + build_analysis_report**；
- **不经过** TaskScheduler / TaskRunner / StructuredTaskExecutor / record_execution_result（execution_id 为 `exec_{question_id}` 合成值）；
- 即：数据资产、恢复、确定性操作、证据对象、报告校验是真实的；**调度与执行收敛链路未被 Golden 覆盖**。

## 6. Adapter 与 Domain Pack 接入关系

- **Adapter（Phase 2）**：`data/adapters/base.py`（ABC + HttpJsonAdapter + Cache）与 `registry.py`（AdapterRegistry/DatasetCatalog）存在但**无主链路调用者**；`public.py` 提供 FRED/BLS/BEA/SEC 四个 HttpJsonAdapter（DatasetSpec/URL/参数），仅 smoke/契约测试覆盖。
- **财务 Pack（Phase 4）**：`runtime/financial/workflow.py::FinancialAnalysisWorkflow.run(FinancialQuery)` 为独立编排（自带 verification/golden/risk 引擎），`runtime/financial_agent/` 提供财务规划/语义；二者内嵌于 runtime 包，未经 4A 定义的 Domain Pack 接口解耦，数据源依赖 golden/文件数据而非采集平台。
- **宏观 Pack（Phase 3）/多 Agent（Phase 5）**：无对应模块。

## 7. Legacy 与路由

- Legacy ReAct（`langgraph_agent.py` 主流程 + `tool_python_repl` 隔离执行）仍是**默认路径**（`RUNTIME_V2_ENABLED` 默认 False，`config.py:86`）。
- Runtime V2 通过 `RUNTIME_V2_ENABLED=1` 显式开启；`runtime_route.py` 提供 completed/disabled/infrastructure_failure 的决策与 strict/fallback 模式；切换门槛未满足（Golden 全链路化 + 默认值变更未做）。

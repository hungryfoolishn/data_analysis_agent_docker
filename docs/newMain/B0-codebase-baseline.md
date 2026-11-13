# B0 代码基线与总计划差异复核 — Git 基线与模块地图

Status: B0 audit (documentation-only; no business code changed)
Audit date: 2026-11-13
Audited HEAD: `d11ded67f277f521d52b79446b0b14bdf95028b0`

## 1. Git 基线（B0.1 实测记录）

| 命令 | 结果 |
| --- | --- |
| `git branch --show-current` | `codex/phase1-m2.4-sqlite-schema` |
| `git rev-parse HEAD` | `d11ded67f277f521d52b79446b0b14bdf95028b0` |
| `git remote -v` | `origin git@github.com:hungryfoolishn/data_analysis_agent_docker.git` |
| `git fetch origin` + `git status -sb` | `codex/phase1-m2.4-sqlite-schema...origin/codex/phase1-m2.4-sqlite-schema`，`git rev-list --left-right --count` = `0 0`（本地与远端完全同步） |
| `git log -10 --oneline` | `d11ded6` fix(runtime): align logical output reference resolution → `bf82650` feat(runtime): add output asset binding → `910ee3a` / `5877b27` / `da7b3c3` docs(runtime): B11.2.1.0 设计与修订 → `a75a459` docs(runtime): B11.2.0 检查报告 → `86adc01` → `d3c6d67` fix(evidence) → `472f86f` → `cec706b` fix(evidence) |

**Output Asset Binding / Evidence 完整性 / B11.2 相关工作确认**：当前分支包含 B11.2.0 检查报告（`a75a459`）、B11.2.1.0 冻结设计（`910ee3a`）、B11.2.1 实现（`bf82650`）与 B11.2.1.1 修复（`d11ded6`）；Evidence 完整性加固位于 `cec706b` / `d3c6d67`。

## 2. 工作区状态（不清理，只记录）

`git status --short` 显示以下**与本审计无关的用户工作区内容，全部保留未动**：

- 已修改：`docs/newMain/1112底层执行规约.md`（AM）、`docs/newMain/B1-B8方案.md`（M）
- 未跟踪：`docs/interview/`、`docs/newMain/plan.md`、`docs/newMain/toDo.md`、`docs/newMain/最新方案.md`、`docs/other/个人知识库技术选型深度复核.md`、`example.db`、`runtime_v5_remote_sync.sh`

**重要发现**：`docs/newMain/toDo.md`（总计划，612 行）是**未跟踪文件**——远端分支 `docs/newMain/` 下仅跟踪 `B1-B8方案.md`。即总计划目前只存在于用户工作区副本，未进入 Git。本审计以该工作区副本为准（与提示词指定路径一致），并建议尽快将其纳入版本控制（不在 B0 内执行，避免混入用户内容）。

**版本时效性**：`toDo.md` 开头自述基于 `f695bf0`（inspect sqlite asset consumption）编写，而当前 HEAD 已推进 20+ 提交。因此 toDo.md 对 M2.7.2–M2.7.4 的状态描述已过时（详见 `B0-capability-matrix.md`）。

## 3. 目录结构与模块地图

```
langgraph_langchain/
├── schemas.py                      # schema 级契约：AnalysisTask/VerificationResult/EvidenceItem/Finding
│                                   #   + B11.2.1: OutputAssetBinding/ExecutionOutputAsset/ResolvedLogicalRef/ResolvedTaskInputs
├── state_machine.py                # 分析阶段状态机（stage 门禁）
├── config.py                       # 环境配置（RUNTIME_V2_ENABLED 默认 False）
├── langgraph_agent.py              # Legacy ReAct 会话/工具编排 + Runtime V2 路由入口（~2400 行）
├── api_server_langgraph.py         # FastAPI：/v1/files、/workspace/upload、/sessions/*、/analysis/runs/*、/metrics/*
├── data/
│   ├── sources.py                  # DataSource 协议 + FileDataSource（CSV/Excel/Parquet）
│   ├── database.py                 # SQLiteDatabase/SQLiteDataSource（只读、bounded read，~900 行）
│   ├── assets.py                   # DataAsset 模型 + hash（from_dataframe/from_sqlite_query）
│   ├── snapshots.py                # SnapshotStore（Parquet + 物理/逻辑双 hash + 原子替换）
│   ├── cache.py / sampling.py      # DataCache（pickle，legacy 路径用）、抽样
│   └── adapters/                   # Phase 2 基础设施：base.py（DataSourceAdapter ABC/HttpJsonAdapter）
│                                   #   registry.py（AdapterRegistry/DatasetCatalog）、public.py（FRED/BLS/BEA/SEC）
├── runtime/
│   ├── context.py                  # AnalysisRuntime：资产/执行/证据/Runtime 状态权威（~2000 行）
│   ├── models.py                   # AnalysisRun/RuntimePlanStep/ExecutionResult/RuntimeArtifact
│   ├── output_bindings.py          # B11.2.1: OutputBindingRegistry + 逻辑引用分类器
│   ├── asset_resolver.py           # AssetResolver（sqlite 快照 + 文件资产恢复）
│   ├── scheduler.py / runner.py    # TaskScheduler（DAG）+ TaskRunner（执行循环 + skip 级联）
│   ├── executor.py                 # TaskExecutor（structured/react/python 分派）
│   ├── graph.py                    # RuntimeV2Controller（LangGraph 薄控制器）
│   ├── runtime_route.py            # 路由决策（RUNTIME_V2_ENABLED 默认 False）
│   ├── plans.py                    # AnalysisPlan + validate_plan/validate_runtime_plan（分型校验）
│   ├── general_planner.py          # DeterministicAnalysisPlanner（确定性窄集 Planner）
│   ├── analysis_operations.py      # 允许列表 dataframe 操作
│   ├── analysis_report.py / report_rebuild.py / report_validator.py / report_claims.py
│   ├── finding_builder.py / finding_lineage.py / outcome.py / quality.py / history.py / learning.py
│   ├── sqlite_store.py / sqlite_workflow.py / analysis_workflow.py
│   ├── evaluation/                 # Golden runner/comparator/cross_task/golden_loader
│   ├── financial/                  # Phase 4 财务能力（未解耦，见 B0-gap-analysis）
│   └── financial_agent/            # 财务 Agent 规划/语义/执行（未解耦）
├── execution/
│   ├── task_models.py              # TaskExecutionRequest/ResolvedTaskInputs + 结构化输出提取
│   ├── structured_executor.py      # 结构化工具执行（canonical execution_id 注入）
│   ├── python_executor.py / python_task_executor.py / react_executor.py
├── evidence/                       # collector/models/registry/sqlite/adapters/operations（canonical evidence）
├── verification/                   # 工件/执行校验
├── tools/
│   ├── registry.py                 # 工具注册表（@tool 工厂 + session 绑定）
│   ├── tool_load_data.py / tool_sqlite_query.py / tool_analysis_operation.py  # B11.2.1 契约工具
│   ├── sqlite_sql_tool.py          # SQLiteQueryTool（只读 bounded）
│   ├── analysis_method_tools.py    # 正式分析方法工具（趋势/对比/贡献/异常/集中度等）
│   ├── tool_eda_profile.py / tool_record_finding.py / tool_finish_report.py / tool_delegate.py 等
│   └── tool_python_repl.py         # 隔离 Python 执行（legacy/兜底）
├── evaluation/                     # golden_phase1（10 datasets / 30+ questions，确定性 harness）
│                                   #   case_loader/gate/release_candidate/report/cli/runner/models/deterministic
└── security.py / tracing.py / stability_metrics.py / skills_loader.py / recovery.py 等
webui/                              # FastAPI dashboard + workbench（app.py/dashboard.py/config.py/workbench.py）
tests/                              # 112 个测试文件，1057 个测试函数
scripts/                            # golden 生成、迁移、校验脚本
docs/                               # product/ architecture/ roadmap/ Runtime/ analysis/ newMain/ …
```

## 4. 核心调用入口（真实调用关系）

1. **API 会话入口**：`api_server_langgraph.py` → `_Session`（`langgraph_agent.py`）→ 会话初始化时 `AnalysisRuntime` + `prepare_file_analysis` / `prepare_sqlite_analysis` 注册源资产并生成计划。
2. **Runtime V2 路由**：`decide_runtime_route(policy)`（`runtime_route.py`）——`RUNTIME_V2_ENABLED` 默认 `False`（`config.py:86`，空环境变量即关闭），即**文件分析默认仍走 Legacy ReAct**；Runtime V2 需显式开启（`langgraph_agent.py:1681/1838` 消费路由决策）。
3. **Runtime V2 执行链**：`initialize_plan()` → `TaskScheduler.from_plan()` → `RuntimeV2Controller`（`graph.py`）→ `AnalysisRuntime.execute_next_task()` → `TaskRunner.execute_next()` → `build_execution_request()`（canonical execution_id + ResolvedTaskInputs + Binding 解析）→ `StructuredTaskExecutor` → 工具 → `record_execution_result()`（Binding 单点写入）。
4. **数据链路**：`FileDataSource` / `SQLiteDataSource` → `register_dataframe` / `register_sqlite_result` → `DataAsset`（+ `SnapshotStore`）→ `AssetResolver.resolve_many()` → `namespace.runtime_inputs` → executor。
5. **证据/报告链路**：`collect_operation_evidence` / `collect_sqlite_query_evidence` → canonical evidence registry（`evidence/registry.py`）→ `verify_*` → `build_analysis_report` / `validate_analysis_report`（`runtime/analysis_report.py`）→ `build_verified_report()`（`context.py`）。

## 5. 与总计划的关系说明

`toDo.md` 的批次编号 B1–B11（B1=M2.7.2、B2=M2.7.3、B3=M2.7.4、B4=Phase 1A、B5=Phase 1B、B6=Phase 1C、B7/B8=Phase 2、B9/B10=Domain Pack、B11=Phase 5）在本审计中保持不变，逐项状态见 `B0-capability-matrix.md`。注意：仓库近期提交中的 "B11.2.x" 命名（Runtime 输出传播）与 toDo.md 的 "B11"（多 Agent）**不是同一编号体系**，前者属于通用 Runtime 基础设施横向批次，后者是 Phase 5 研究型多 Agent。

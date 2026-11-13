# B0 能力差距矩阵（B1–B11）

Status: B0 audit, based on HEAD `d11ded6` + 实测测试证据（docker-host, conda analysis）。
判定标准：只有通过真实调用链验证且存在测试/运行证据的能力才计为"已具备"；有模型/接口但未接入主链路计为"接口实现"。

| 批次 | 总计划内容 | 状态 | 代码依据（真实调用链） | 测试证据 | 仍缺少 |
| --- | --- | --- | --- | --- | --- |
| **B1** | M2.7.2 SnapshotStore | **已具备** | `data/snapshots.py::SnapshotStore`（Parquet、物理+逻辑双 hash、原子替换、containment 校验）；调用链 `SQLiteQueryTool.execute` → `runtime.register_sqlite_result()`（`context.py`）→ `snapshot_store.save()`；失败清理（`_persist` 异常回滚删除快照） | `tests/test_snapshot_store.py`（14+ 用例：损坏/hash 不匹配/NULL/空 DF/逃逸路径）；`tests/test_runtime_sqlite_recovery_e2e.py`（重启恢复/dedup 复用/显式失败 6 用例，84 passed 证据之一） | 无阻塞缺口 |
| **B2** | M2.7.3 AssetResolver | **已具备** | `runtime/asset_resolver.py::AssetResolver.resolve/resolve_many`（sqlite→SnapshotStore 校验恢复；文件→FileDataSource；未知类型/缺失快照结构化错误；多输入保序）；调用链 `build_execution_request()` → `resolve_many()` → `namespace.runtime_inputs` | `tests/test_asset_resolver.py`；`tests/test_file_asset_resolver.py` | 无阻塞缺口（PostgreSQL/MySQL 不在本批次范围） |
| **B3** | M2.7.4 Runtime 输入接入 | **已具备（超出计划）** | `context.build_execution_request()` 解析输入；`PythonTaskExecutor`/结构化工具消费 `runtime_inputs`；输入资产版本/Schema/provenance 写入 request metadata；**B11.2.1 追加**：OutputAssetBinding、canonical execution_id、命名多输出、下游逻辑引用解析（`d11ded6`） | `tests/test_asset_resolver.py`；`tests/runtime_v2/*`（219 passed 回归证据）；`tests/test_output_binding_*.py`（78 用例） | 无阻塞缺口 |
| **B4** | Phase 1A 文件分析闭环 | **部分具备** | 真实 Runtime V2 全链路测试：`tests/runtime_v2/test_runtime_e2e.py`（load_data→declare_metric→eda→趋势/对比/贡献→record_finding×3→finish_report，10 步 DAG，含验证+证据+报告断言）；确定性工具 `tools/analysis_method_tools.py`（趋势/对比/贡献/异常/集中度/比率/TopN）；文件理解 `tool_eda_profile` + `analysis_workflow.prepare_file_analysis`；上传 API `/v1/files`、`/workspace/upload` | e2e passed；`tests/test_general_planner.py`、`tests/test_analysis_method_tools.py`、`tests/test_report_rebuild.py` | ① 通用 Planner 仅确定性窄集（`general_planner.py::DeterministicAnalysisPlanner`，支持 summarize/group_aggregate/trend/anomaly/sort 等 `PLAN_OPERATIONS`），无 NL 通用规划；② Parquet 输入无专门 e2e；③ 多轮追问/会话续跑未在文件闭环内验证；④ 默认路由未切 V2（见 B0-gap-analysis P1-2） |
| **B5** | Phase 1B SQLite 分析闭环 | **部分具备** | `run_sqlite_query` → 快照资产 → Binding/`runtime_inputs` → `run_analysis_operation` 消费查询结果 → Evidence → `build_verified_report()`（`context.py::build_verified_report`）；`tests/test_b11_1_runtime_closure.py`（file+sqlite 闭环、报告校验、outcome）；`tests/test_runtime_sqlite_recovery_e2e.py` | b11_1 闭包 12 用例 passed（84 passed 证据之一） | ① SQLite 场景未进入 Golden 全量问题集的真实 Runtime V2 执行；② Schema-aware Planner 对 SQLite 仍为模板 SQL（`sqlite_workflow.py` 关键词匹配）；③ PostgreSQL/MySQL 未开始 |
| **B6** | Phase 1C Golden 验收与冻结 | **部分具备** | `evaluation/golden_phase1.py`：10 datasets / 34 questions（file+sqlite），`run_golden_suite()` 产出指标门禁（numeric≥0.9、evidence==1.0、report≥0.9）+ JSON/MD 报告；`evaluation/gate.py`/`release_candidate.py` 门禁机制 | `tests/test_golden_phase1.py` 2 用例 passed（84 passed 证据之一） | **关键缺口**：harness 是组件级编排（`_run_file_case` 直接调 `prepare_file_analysis`+`AssetResolver`+`execute_analysis_operation`，execution_id 为合成值），**未经完整 Runtime V2 调度链（Scheduler/Runner/Executor/record_execution_result）**；计划门禁口径需按 toDo.md 十、7 校准为全链路 |
| **B7** | Phase 2A Adapter 基础设施 | **接口实现** | `data/adapters/base.py`（`DataSourceAdapter` ABC、`FetchRequest/FetchResult/QualityReport/AdapterCache`、`HttpJsonAdapter`）；`registry.py`（`AdapterRegistry.register/fetch`、`DatasetCatalog.from_registry`） | `tests/test_public_adapter_smoke.py`、`tests/test_public_adapters.py` | **未接入平台主链路**：全仓库仅 `data/adapters/` 内部引用 AdapterRegistry，runtime/agent/API 无任何调用；无缓存刷新任务、限流、数据版本与更新记录的运行闭环 |
| **B8** | 第一批公开数据源 | **接口实现** | `data/adapters/public.py`：`FREDAdapter`/`BLSAdapter`/`BEAAdapter`/`SECAdapter`（4 个，含 DatasetSpec 与 provider URL） | `tests/test_public_adapters.py`（smoke/契约） | 无真实网络采集 e2e、无标准化→质量检查→DataAsset 注册→Runtime 的贯通场景；AKShare/Tushare、国家统计局、人民银行未开始 |
| **B9** | Phase 3 宏观 Domain Pack | **未开始** | 全仓库仅 `data/adapters/public.py` 的 dataset spec 提及 GDP/CPI；无指标目录模型、单位/频率/季调/名义实际口径、宏观分析模板 | 无 | 全部待实施 |
| **B10** | Phase 4 财务 Domain Pack | **部分具备（未解耦）** | `runtime/financial/`（models 25 个：三大报表/指标/证据/风险/异常；`FinancialAnalysisWorkflow.run()`；verification_engine/golden/risk_detector）；`runtime/financial_agent/`（规划/语义/执行）；`tests/financial*`、`tests/financial_agent/`（本次全量回归排除目录，未在本轮执行） | 财务测试目录存在（本轮未执行，标注待确认） | ① 未按 toDo.md 4A 收敛为 Domain Pack 接口（仍内嵌 runtime/）；② 财报数据源（SEC EDGAR 贯通、XBRL/公告原文定位）未接入采集平台；③ 累计/单季、会计准则、修订版本等标准化未与 Adapter 层打通 |
| **B11** | Phase 5 研究型多 Agent | **未开始** | 仅 `tools/tool_delegate.py::delegate_analysis`（单次委托工具）与 `tests/test_tool_delegate.py`；无 Macro/Fundamental/Statistical/Risk/Synthesizer 角色、无并行编排、无结论冲突检测、无多 Agent vs 单 Agent 基线对比 | 无多 Agent 测试 | 全部待实施 |

## 汇总

- **已具备**：B1、B2、B3（且 B3 被 B11.2.1 大幅增强）。
- **部分具备**：B4、B5、B6、B10。
- **接口实现（未接入主链路）**：B7、B8。
- **未开始**：B9、B11。
- **待确认**：B10 的财务测试目录执行情况（本轮全量回归按范围排除，未收集证据）。

> 提醒：toDo.md 编写时的假设（"M2.7.2 仍是实施方案阶段"）已过时——B1–B3 实际已完成并被 B11.2.1 扩展。B0 后的推进重心应从"补 SQLite 链路"转为"补 Phase 1 闭环的完整性与 Golden 全链路化"。

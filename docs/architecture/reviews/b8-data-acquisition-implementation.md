# B8 — Data Acquisition Platform 实施与验收报告

Status: B8.1–B8.6 implemented（B8.7 验收报告即本文档）；真实 API 验证为凭据门控。
Baseline: `7127349` → 本提交；前置设计：`b8.0-data-source-readiness.md`、`b4.2.0-general-planner-design.md` v2。

## 1. 交付摘要（对照 B0 审计的 P1/P2）

| B0 缺口 | 本批次闭环方式 |
| --- | --- |
| P1-1 FetchResult → DataAsset 注册桥缺失 | 新增 `AnalysisRuntime.register_fetch_result()`（runtime/context.py）+ `data/acquisition.py::AcquisitionService.acquire()`；复用 DataAsset + SnapshotStore（无第二套注册系统），事务语义镜像 register_sqlite_result（失败回滚快照与注册） |
| P1-2 契约缺 observation_period/release_date/revision | `FetchResult` 新增三字段（base.py）；HttpJsonAdapter 自动填充 observation_period（归一化帧实际期间）；release_date 经 `_release_dates` 由源声明；revision 预留 |
| P1-3 中国国家统计局 Adapter 缺失 | 新增 `NBSAdapter`（public.py）：data.stats.gov.cn easyquery 协议（dbcode=hgnd/zb 编码/sj 期间），nbs_gdp + nbs_cpi 两个 DatasetSpec，returncode 校验、无效值过滤、去重排序 |
| P1-4 Rate limit 声明未实现 | `HttpJsonAdapter._enforce_rate_limit()`：按 `descriptor.rate_limit_seconds` 强制最小请求间隔 |
| P1-5 数据版本/更新记录缺失 | `data/acquisition.py::AcquisitionHistory`：per-dataset 持久化版本记录（content_hash/fetched_at/observation_period/revision/asset_id/version_changed）于 `<workspace>/.data_acquisition/history/` |
| P2-2 BLS normalize 弱 | 维持现状（记录为后续改进，未扩大范围） |
| 观察缺口检测（Phase 2C） | `base.py::detect_observation_gaps`：按 frequency 生成期望期间并报告缺口；HttpJsonAdapter.fetch 自动降级 QualityReport → warning |
| Adapter 健康状态（Phase 2C） | `acquisition.py::AdapterHealthTracker`（consecutive failures → failing/degraded/healthy） |

## 2. 新增/修改文件

| 文件 | 变更 |
| --- | --- |
| `langgraph_langchain/data/adapters/base.py` | FetchResult 三契约字段；`detect_observation_gaps`；rate limit 强制；fetch 自动观察期间与缺口降级 |
| `langgraph_langchain/data/adapters/registry.py` | `fetch(request, *, refresh=False)` 透传 |
| `langgraph_langchain/data/adapters/public.py` | 新增 `NBSAdapter`；default_adapter_registry 扩至 5 Adapter / 10 数据集 |
| `langgraph_langchain/data/acquisition.py`（新） | AcquisitionService / AcquisitionHistory / AdapterHealthTracker / AcquisitionOutcome |
| `langgraph_langchain/runtime/asset_resolver.py` | 通用 snapshot-backed 资产解析（任何 source_type 携带 snapshot_reference 即经 SnapshotStore 校验加载） |
| `langgraph_langchain/runtime/context.py` | `register_fetch_result()`（dedup + 快照事务 + 契约 metadata）；`build_verified_report` 报告引用同时覆盖输入资产（数据来源披露） |
| `tests/test_data_acquisition.py`（新） | 13 用例 |
| `tests/test_public_adapters.py` | registry 5/10 断言 + NBS 契约测试 |

## 3. 完整数据链路（B8 验收场景，已由测试覆盖）

```
Dataset Request（FetchRequest）
  → AdapterRegistry.fetch（rate limit + 缓存/refresh + retry/backoff）
  → Normalize → Validate（quality）→ 缺口检测（observation gaps → warning）
  → FetchResult（content_hash + observation_period + release_date/revision）
  → AcquisitionService.acquire
      → AcquisitionHistory.record（版本记录 / 更新检测 version_changed）
      → AdapterHealthTracker（健康状态）
      → runtime.register_fetch_result
          → SnapshotStore.save（Parquet，hash 校验，事务回滚）
          → DataAsset（source_metadata 全契约：source_id/dataset_id/request_parameters/
            source_url/fetched_at/observation_period/release_date/revision/
            unit/frequency/quality_report/content_hash）
          → _persist（Runtime 快照持久化）
  → AssetResolver._resolve_snapshot_asset（verified 恢复）
  → Runtime V2：initialize_plan → Scheduler → Executor（canonical execution_id
    + output_name 注入）→ record_execution_result → OutputAssetBinding
  → Evidence（collect/canonical/verify）→ build_verified_report
    （findings.referenced_asset_ids 同时覆盖输出与输入来源资产）
```

## 4. 实际测试命令与结果

| 命令（docker-host / conda analysis） | 结果 |
| --- | --- |
| `pytest tests/test_data_acquisition.py tests/test_public_adapters.py` | **21 passed** |
| `pytest tests/test_planner_v2_contract.py tests/test_data_profile.py` | 33 passed（B4.2.1 回归） |
| B8+B4+Binding+Runtime 回归组合（24 文件） | **251 passed** |
| 全量 `pytest tests`（排除 financial） | 2 个失败 = 既有 dashboard 失败（与基线一致，零新增） |

关键新增测试（`tests/test_data_acquisition.py`）：
- `test_acquire_registers_contract_complete_asset`：12 项契约字段逐一断言；
- `test_persistence_survives_runtime_restore`：Runtime 销毁→恢复→快照解析；
- `test_dedup_and_version_change`：dedup 同资产 / 内容变更 → 新资产 + version_changed + 三条版本记录；
- `test_full_chain_adapter_asset_to_report`：采集资产 → V2 summarize 执行（output_name 注入）→ Evidence verified → Report 引用输入来源资产；
- `test_rate_limit_enforced` / `test_gap_detection_*` / `test_quality_downgrades_on_observation_gap` / `test_nbs_adapter_*` / `test_adapter_health_tracking`。

## 5. 凭据门控的真实 API 验证（待用户提供凭据后执行）

真实网络验证仍为凭据门控（离线 CI 不依赖外部服务）：

```
FRED_API_KEY=... pytest tests/test_public_adapter_smoke.py::test_fred_real_smoke
RUN_BLS_SMOKE=1 pytest tests/test_public_adapter_smoke.py::test_bls_real_smoke
BEA_API_KEY=... pytest tests/test_public_adapter_smoke.py::test_bea_real_smoke
RUN_NBS_SMOKE=1 pytest tests/test_data_acquisition.py（NBS smoke 可按同模式添加）
```

**如实声明**：本批次的"端到端"验证使用 transport 注入的确定性响应（离线），真实网络调用未执行——需凭据后按上述命令运行 smoke 并接入 `tests/test_data_acquisition.py` 的链路测试。

## 6. toDo.md Phase 2 验收场景状态

> “对比中国和美国近十年 CPI 变化”——自动：识别指标→选择数据源→拉取→标准化→校验→注册 DataAsset→进入通用 Runtime→输出带来源的报告

**已具备**：fred_cpi 与 nbs_cpi 两个 DatasetSpec（unit/frequency 声明）；采集→注册→Runtime→Evidence→带来源报告的全链路机制与离线测试。
**尚缺**：真实网络验证（凭据）；“识别指标与国家”的意图解析（属 Planner/Domain 扩展，toDo.md 未列入 B8 验收硬项，记录为后续）。

## 7. 后续建议

1. 凭据就绪后执行真实 smoke 并补一条真实 E2E（FRED）。
2. BLS normalize 丰富化（series 元数据、period 解析）。
3. NBS 接口稳定性验证（easyquery TLS 已知不稳定，必要时降级 WorldBank）。
4. 刷新调度（定时任务）与 Catalog 持久化（B8.2 余项）。
5. B8 冻结后：采集产出的 DataAsset 纳入 B6 Golden 全链路基线。

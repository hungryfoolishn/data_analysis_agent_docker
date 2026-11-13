# B0 差距分析：阻塞项、可复用模块与推进建议

Status: B0 audit。P0/P1/P2 判定遵循 toDo.md 第十节停止规则，不扩大化。

## 1. P0（核心链路不可运行 / 数据正确性 / 安全可靠性重大问题）

**当前无 P0。**
依据：核心链路可运行且有测试证据——Runtime V2 全链路 e2e（`tests/runtime_v2/test_runtime_e2e.py`）、SQLite 快照恢复与显式失败（`tests/test_runtime_sqlite_recovery_e2e.py`）、Golden 组件级 harness（`tests/test_golden_phase1.py`）、输出绑定 78 用例（`bf82650`/`d11ded6`）；本轮实测 84 passed（代表性组合）+ 全量回归失败集合与基线一致（仅 2 个既有 dashboard 失败）。

## 2. P1（进入相关批次前必须处理）

| 编号 | 问题 | 代码依据 | 阻塞的批次 | 处理建议 |
| --- | --- | --- | --- | --- |
| P1-1 | **Golden harness 未走完整 Runtime V2 链路**：`evaluation/golden_phase1.py::_run_file_case/_run_sqlite_case` 直接编排 `prepare_*_analysis` + `AssetResolver` + 确定性操作/SQL 工具，合成 execution_id，不经 Scheduler/Runner/Executor/record_execution_result；Binding、canonical execution_id、失败级联等 B11.2.1 契约均未被 Golden 覆盖 | `evaluation/golden_phase1.py:297/346/387` | **B6**（Golden 验收不成立则冻结无意义）；影响 B4/B5 的"完整闭环"口径 | B6 前将 `_run_*_case` 改为经 `initialize_plan → RuntimeV2Controller/execute_next_task` 的真实链路，问题集重跑并冻结新基线 |
| P1-2 | **Runtime V2 默认路由未切换**：`RUNTIME_V2_ENABLED` 默认 False（`config.py:86`），文件分析默认仍走 Legacy ReAct；toDo.md P0-2 明确"切换必须由门禁决定" | `config.py`、`runtime_route.py`、`langgraph_agent.py:1681/1838` | **B4/B5 验收口径**（"当前文件分析默认走哪条路径"） | 补齐切换门禁清单（e2e + Golden 全链路 + 回归零新增失败）后改默认值并冻结 Legacy 开关语义 |
| P1-3 | **通用 Planner 能力缺口**：`DeterministicAnalysisPlanner` 仅覆盖窄集操作（`PLAN_OPERATIONS`），Phase 1A 要求的"基于 Schema/数据摘要生成计划"只有确定性实现，无 NL→计划的通用规划层 | `runtime/general_planner.py`、`tests/test_general_planner.py` | **B4**（通用分析闭环的"通用"二字） | B4 内定义 Planner 扩展边界（LLM 规划 + 结构化校验 + 确定性兜底），先固定可规划问题集再扩 |
| P1-4 | **SQLite Schema-aware Planner 为关键词模板**：`sqlite_workflow.py::build_sqlite_plan` 用正则关键词选 SQL，复杂问题无法生成可复现 SQL | `runtime/sqlite_workflow.py` | **B5**（"可复现的 SQL"验收） | B5 内实现 schema-aware SQL 规划（schema 上下文 → SQL 生成 → 只读校验），复用现有 bounded read 与快照链路 |

## 3. P2（不阻断当前批次，记录为技术债）

| 编号 | 问题 | 位置 |
| --- | --- | --- |
| P2-1 | SQLite provenance 豁免偏宽（dedup 复用只验 provenance+hash，未验 fingerprint 匹配本次查询语义）；已按 B11.2.1.1 复审意见补边界测试，收窄实现留待 B11.2.2 Evidence Lineage | `runtime/context.py::_adjudicate_output_bindings` |
| P2-2 | Adapter 层无平台接入点：AdapterRegistry/DatasetCatalog 仅供测试，无 fetch→标准化→DataAsset 注册的运营链路（缓存刷新/限流/版本记录均缺）——这本身是 B7 的交付内容，列为边界提示而非缺陷 | `data/adapters/` |
| P2-3 | 财务能力内嵌 `runtime/financial*`（未按 4A 解耦 Domain Pack 接口），且本轮全量回归排除了 `tests/financial*`（财务测试证据待确认） | `runtime/financial/`、`runtime/financial_agent/` |
| P2-4 | `docs/newMain/toDo.md` 未纳入版本控制（仅工作区副本）；toDo.md 内容基于 `f695bf0`，与 HEAD 已有约 20 个提交的差距 | `docs/newMain/toDo.md` |
| P2-5 | `tests/test_dashboard.py` 2 个既有失败（503/断言），与 Runtime 无关，属 WebUI 技术债 | `tests/test_dashboard.py` |
| P2-6 | Parquet 输入与多轮追问/会话续跑在文件闭环中缺少专门 e2e（能力组件存在：`FileDataSource` 支持 parquet、`session_persistence`/resume API 存在） | `data/sources.py`、`api_server_langgraph.py` |

## 4. 可直接复用模块（无需重写）

| 模块 | 复用价值 | 服务于 |
| --- | --- | --- |
| `data/snapshots.py` + `runtime/asset_resolver.py` | 任何数据源产出 DataFrame 资产的持久化/恢复通用机制 | B7/B8（Adapter 产出注册为 DataAsset） |
| `OutputBindingRegistry` + `record_execution_result` 契约 | 命名输出/多下游/attempt 隔离 | B4/B5 多步骤分析、B9/B10 领域分析步骤 |
| `runtime/analysis_operations.py` + `analysis_method_tools.py` | 允许列表确定性操作与正式分析工具 | B4 扩展、B9/B10 模板实现 |
| `evidence/`（canonical evidence + registry） | 证据可信与报告校验 | 所有批次 |
| `runtime/evaluation/` + `evaluation/golden_phase1.py` | 数据集/问题集/门禁框架 | B6 全链路化改造（只换执行底座） |
| `data/adapters/base.py + registry.py + public.py` | Adapter 契约与 4 个真实源实现 | B7/B8 直接接入运营链路 |
| `runtime/financial/`（models/workflow/verification/golden） | 财务领域知识与验证规则 | B10 按 4A 提取为 Domain Pack |
| `api_server_langgraph.py` 的 plan/runs/metrics 端点 | 计划确认、运行观测、指标面板 | B4/B5/B6 验收观测 |

## 5. B1–B11 推进建议（进入条件，目标不变）

1. **B1–B3**：已完成（含 B11.2.1 增强），无重启项；后续批次不得破坏其契约（快照校验、Resolver 错误语义、Binding 单点写入）。
2. **B4（Phase 1A）**：进入条件 = P1-2 路由门禁清单确定 + P1-3 Planner 扩展方案评审；交付含 Parquet e2e 与多轮追问验证；验收仍为 toDo.md 四、Phase 1A 场景与检查项。
3. **B5（Phase 1B）**：依赖 B4 稳定；核心是 P1-4 schema-aware SQL 规划；验收场景 toDo.md 四、Phase 1B。
4. **B6（Phase 1C）**：进入条件 = **P1-1 Golden 全链路化完成**；在此之后 10 datasets/30 questions 的门禁结果才可作为冻结依据；随后执行 toDo.md 的契约冻结。
5. **B7/B8（Phase 2）**：进入条件 = B6 冻结；将现有 `AdapterRegistry/DatasetCatalog/public.py` 接入"fetch→normalize→validate→register DataAsset→Runtime"运营链路，先 FRED/BLS 等已有 4 个源。
6. **B9/B10（Domain Pack）**：进入条件 = B8 数据源可用；B10 先按 4A 定义 Domain Pack 接口再迁移 `runtime/financial*`（保持兼容，不大规模目录搬迁）。
7. **B11（Phase 5）**：进入条件 = B6/B9/B10 就绪 + 单 Planner 基线可度量（当前无任何多 Agent 实现，无需前置修复）。

## 6. 与近期 B11.2.x 工作的衔接说明

仓库近期提交的 "B11.2.x"（Runtime 输出传播：Binding/Evidence 血缘基础）是通用 Runtime 横向基础设施，**不占用** toDo.md 的 B11 编号；其成果（canonical execution_id、ResolvedTaskInputs、命名输出、Binding 血缘）正是 B4/B5/B6 验收所需的输入输出契约，应作为已完成前提并入上述推进顺序。

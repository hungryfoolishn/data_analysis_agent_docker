# B0 验收报告

Status: B0 audit complete（documentation-only）

## 1. 审计范围

- 对象：`codex/phase1-m2.4-sqlite-schema @ d11ded6`（与 `origin` 同步，0/0 差异）。
- 输入：`docs/newMain/toDo.md`（用户工作区副本，612 行，未跟踪文件）；`docs/architecture/B11.2.1-output-asset-binding-design.md`、`docs/architecture/B11.2.1-output-asset-binding.md`、`docs/architecture/reviews/b11.2-runtime-output-propagation-inspection.md`；全部业务模块与 112 个测试文件（1057 个测试函数）。
- 边界：只读审计；未修改业务代码/测试；未实施 B1；未切换/合并分支；未清理用户工作区。

## 2. 方法

1. **Git 基线**：实测 `status/branch/rev-parse/log/remote/fetch`，确认 HEAD 与远端一致、toDo.md 为未跟踪工作区副本。
2. **总计划解析**：通读 toDo.md 全文，按批次提取实施内容/验收重点（B1=M2.7.2 … B11=Phase 5），不修改目标。
3. **调用链核实**：对 13 个审计点逐一用 `rg`/源码阅读验证真实调用关系（非文件名推断），重点：SnapshotStore 写入与失败清理、AssetResolver 恢复、Runtime request 构建、Binding 单点写入、Golden harness 真实形态、Adapter 接入点、财务 Pack 结构、路由默认值（`RUNTIME_V2_ENABLED` 默认 False）。
4. **测试核对**：优先复用既有验收证据（B11.2.1 回归），再运行代表性只读测试补证。

## 3. 检查命令与结果

| 命令（docker-host / conda analysis） | 结果 |
| --- | --- |
| `git status/branch/rev-parse/log/remote/fetch` | 分支 `codex/phase1-m2.4-sqlite-schema`，HEAD `d11ded6`，与 origin 同步（0/0） |
| `pytest tests/test_golden_phase1.py tests/test_runtime_golden_e2e.py tests/test_b11_1_runtime_closure.py tests/test_snapshot_store.py tests/test_asset_resolver.py tests/test_runtime_sqlite_query_asset.py tests/test_public_adapters.py tests/test_report_rebuild.py -q` | **84 passed**（本轮实测，覆盖 B1–B6/B7 代表面） |
| 前序证据（B11.2.1 批次，同 HEAD 家族）：`pytest tests/test_output_binding_*.py` | 78 passed |
| 前序证据：主回归 30 文件 | 219 passed, 4 subtests |
| 前序证据：全量 `pytest tests`（排除 financial）+ git-stash 基线对照 | 失败集合与基线一致（仅 2 个既有 dashboard 失败） |
| 未执行项 | `tests/financial*`、`tests/financial_agent/`（本轮按范围排除，B10 证据标注待确认）；LLM 相关长流程未跑（审计环境无 LLM 依赖要求） |

## 4. 核心结论（详见 capability-matrix / gap-analysis）

1. **B1–B3 已完成**（SnapshotStore / AssetResolver / Runtime 输入接入），且被 B11.2.1（`bf82650`+`d11ded6`）扩展为完整输入输出契约——toDo.md 对此的描述已过时。
2. **B4/B5 部分具备**：Runtime V2 真实 e2e 与 SQLite 闭环存在，缺通用/schema-aware Planner 与 Parquet/多轮追问验证。
3. **B6 部分具备**：Golden 数据集与门禁存在，但 harness 为组件级编排，未经完整 Runtime V2 链路（本轮最重要的结构性发现，P1-1）。
4. **B7/B8 接口实现**：Adapter ABC/Registry/4 个真实源 Adapter 存在，未接入平台主链路。
5. **B9/B11 未开始**；B10 财务能力丰富但未解耦为 Domain Pack。
6. **无 P0**；P1 共 4 项（Golden 全链路化、默认路由门禁、通用 Planner、schema-aware SQL Planner）。
7. `RUNTIME_V2_ENABLED` 默认 False → 文件分析默认仍为 Legacy ReAct（toDo.md P0-2 的答案）。

## 5. 验收结论

B0 十条验收标准逐项满足：基线明确（§1 of B0-codebase-baseline）；toDo.md 已完整阅读（612 行）；模块地图与调用入口成型；13 个审计点经真实调用链核实；接口/局部/端到端已区分；B1–B11 全部有状态+代码依据+测试证据；P0/P1 明确；B1–B3 已完成、B1（toDo.md 编号）无重启条件——**当前应进入的实际批次是 B4（Phase 1A 收尾）与 B6 前置（Golden 全链路化）**，而非 toDo.md 字面建议的 B1。业务代码零改动，用户工作区原样保留。

## 6. B1 启动条件（按 toDo.md 编号的更正说明）

toDo.md 字面的 B1（M2.7.2 SnapshotStore）已具备——无需启动。真正需要启动的是：

- **B4 前置（Phase 1A 收尾）**：路由门禁清单（P1-2）+ Planner 扩展方案（P1-3）+ Parquet/多轮追问 e2e；
- **B6 前置（Golden 全链路化，对应 P1-1）**：Golden harness 接入完整 Runtime V2 链路并重跑冻结基线。

若严格沿用 toDo.md 编号推进，B0 之后的第一批应定义为"B4/B6 前置修复"，而非重启 B1–B3。

## 7. Git 交付

- 分支：`codex/phase1-m2.4-sqlite-schema`（未切换分支——按执行边界第 9 条，未经确认不切分支；建议分支 `codex/phase1-b0-codebase-baseline` 可在评审后从本提交 cherry-pick/branch 创建）。
- 新增 5 份文档：本文件 + `B0-codebase-baseline.md` + `B0-capability-matrix.md` + `B0-runtime-data-flow.md` + `B0-gap-analysis.md`。
- 提交信息：`docs(architecture): add B0 codebase baseline audit`。

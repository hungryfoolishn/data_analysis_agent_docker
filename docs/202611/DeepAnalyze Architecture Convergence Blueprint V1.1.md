# DeepAnalyze Architecture Convergence Blueprint V1.1

> 文档性质：独立架构评审修订版（Implementation-Ready Blueprint）
> 评审对象：`DeepAnalyze Architecture Convergence Blueprint V1.0.md`
> 评审基线：`docker-host:/python/pragrams/data_analysis_agent_a133`
> 分支：`codex/a1.3.3-canary-readiness`
> HEAD：`4a9b25474ddf5a8163e8e8925d8e19d128848d87`（工作区干净，远端同步）
> 评审日期：2026-11-24
> 评审方式：只读代码审计 + 文档交叉验证；未修改任何生产代码。

---

## 0. V1.1 结论摘要

**V1.0 评级：MAJOR_REVISION_REQUIRED**

V1.0 的战略方向正确：

- 统一 Runtime、统一 Data、统一 Worker、统一 Evidence 是正确目标。
- Docker Worker 应成为生产默认执行路径，判断正确。
- Domain Pack 不拥有独立 Runtime，判断正确。
- Capability Migration（搬能力不搬模块）迁移原则，判断正确。
- Evidence/Verification 驱动报告信任模型，判断正确。

但 V1.0 存在四类需要修订的问题：

1. **安全紧迫度低估**：V1.0 将路径隔离、session 授权归入 R0 的笼统清单，但未指出已存在的具体 P0 级攻击面（session_id 路径穿越、API 进程内执行 LLM 代码、全局 token 无身份语义）。
2. **迁移矩阵缺乏代码级锚点**：V1.0 的处置矩阵是"初步判断"，未给出真实文件、符号与调用关系，实施时无法直接当作工作分解依据。
3. **契约设计偏理想化**：V1.0 提出大量新契约（Unified AnalysisRequest、Task Graph、ExecutionResult 等），但未标注哪些契约已有真实实现可直接复用（如 `runtime/models.py`、`runtime/plans.py`、`evidence/registry.py`），存在重复造契约的风险。
4. **R0 范围过大**：V1.0 的 R0 同时包含执行隔离、网络策略、SSRF、Artifact 安全等 9 个主题，实际工程上应拆分为可独立验收的安全里程碑。

V1.1 在保持 V1.0 方向不变的前提下，给出经过代码验证的修订架构、最小契约集、逐文件的迁移矩阵和分阶段路线。

---

## 1. 代码事实核查（V1.1 证据基础）

以下事实全部在审计基线 HEAD `4a9b254` 上直接验证。

### 1.1 Runtime 现状

| # | 事实 | 证据位置 | 结论 |
|---|------|----------|------|
| F1 | `RUNTIME_V2_ENABLED` 默认 `False`，生产默认仍为 Legacy ReAct | `langgraph_langchain/runtime/runtime_route.py:38`（`enabled=_env_flag(env, "RUNTIME_V2_ENABLED", False)`） | V2 仅为 opt-in，未进入主链路 |
| F2 | Runtime V2 具备结构化 Plan / Task / Evidence / Report 能力，且 B11 Multi-Agent 已基于 V2 构建 | `langgraph_langchain/runtime/multi_agent.py:1-60`（模块声明"operates through the existing Runtime V2, StructuredTaskExecutor and EvidenceRegistry"） | V2 是收敛的最佳基座，V1.0 判断正确 |
| F3 | Runtime Selection Policy 已有显式路由契约（enabled / fallback / strict_mode） | `langgraph_langchain/runtime/runtime_route.py:25-60` | V2→Unified 的切换机制已有雏形，可直接扩展 |

### 1.2 执行架构现状

| # | 事实 | 证据位置 | 结论 |
|---|------|----------|------|
| F4 | 生产主链路硬编码 `ProcessWorkerBackend`，Docker Worker 未接入 | `langgraph_langchain/langgraph_agent.py:1139,1157`（`WorkerDispatcher(ProcessWorkerBackend(), backend_name="process")`）；`langgraph_langchain/execution/python_task_executor.py:43`（默认 `ProcessWorkerBackend`） | P0：不可信代码默认在 API 进程/子进程中执行，无容器隔离 |
| F5 | `worker_backend_from_env()` 已实现且不降级（unknown/docker 名称不回退），但生产调用方未使用 | `langgraph_langchain/execution/worker_dispatcher.py:54-70`（"unknown and Docker names do not fall back"） | 接线点已存在，只差主链路调用 |
| F6 | Docker Worker 具备文件系统/Artifact 隔离能力（A1.4.2.2 系列交付） | `docker/worker/Dockerfile.worker`、`docker/worker/worker_entrypoint.py`、近期提交 `570f348 feat(worker): enforce container filesystem artifact isolation` | Docker Worker 是 R0 唯一可信执行后端 |
| F7 | `delegate_analysis` 在 API 进程内以 `sub_ns` 命名空间直接执行 LLM 生成代码 | `langgraph_langchain/tools/tool_delegate.py:48-150`（构建 `sub_ns`、复制 `df`、注入 workspace helpers，无 Worker 调度） | P0：与 ADR-006（Isolated Worker, Fail Closed）直接冲突，必须迁移或停用 |
| F8 | Python Task Executor 同样默认内联/进程执行 | `langgraph_langchain/execution/python_task_executor.py:43` | 与 F4 同属执行边界缺陷 |

### 1.3 Session / 授权现状

| # | 事实 | 证据位置 | 结论 |
|---|------|----------|------|
| F9 | `get_session_workspace()` 直接 `WORKSPACE_DIR / session_id`，无任何格式校验即 `mkdir(parents=True)` | `langgraph_langchain/api_server_langgraph.py:464-469` | P0：恶意 session_id（如 `../../<target>`）可穿越 workspace 根目录，读写宿主文件系统 |
| F10 | 全仓库 API 层未发现 session_id 格式校验逻辑（无 `SESSION_ID_PATTERN` / `validate_session_id` / 白名单正则） | 全文搜索 `api_server_langgraph.py` 结果为空 | P0：path traversal 无第二道防线 |
| F11 | 全局 bearer token 无用户身份/会话归属语义（多用户共享同一 token 即可互访 session 资源） | A1.3.3 审计遗留结论 + `api_server_langgraph.py` 无 per-user 鉴权中间件 | P1（单用户部署）/ P0（多用户部署）：Session 归属授权缺失 |
| F12 | 单文件响应有 `relative_to` 防穿越，但根 workspace 本身可被 F9 污染 | `api_server_langgraph.py:1107-1114`（`requested.relative_to(session_workspace)`） | F9 修复后此处才真正安全 |

### 1.4 Evidence / Verification 现状

| # | 事实 | 证据位置 | 结论 |
|---|------|----------|------|
| F13 | `EvidenceRegistry.verify()` 强制要求 `content_validator`，缺省即 `failed`；引用验证失败即拒绝 | `langgraph_langchain/evidence/registry.py:160-183` | 核心信任原语设计正确，V1.0 的 Evidence 方向与实现一致 |
| F14 | `restore_record()` 恢复记录强制重置为 `unverified` | `evidence/registry.py:131-146` | 持久化恢复不会继承信任态，正确 |
| F15 | 但存在默认 `validation_status="verified"` 的旁路入口（legacy operations / sqlite 快路径） | `evidence/operations.py:27`、`evidence/sqlite.py:34` | P1：Legacy 兼容层存在"自我声明 verified"通道，V1.0 第 15 章禁止事项正对应此问题 |
| F16 | Evidence collector 仅从成功且经过验证的执行结果构建证据 | `evidence/collector.py:1-5,200` | V2 路径信任链完整，是 Unified Evidence 的参照实现 |

### 1.5 数据与领域现状

| # | 事实 | 证据位置 | 结论 |
|---|------|----------|------|
| F17 | DataAsset / Snapshot / Source / Acquisition / Sampling / Database 模块均已存在且结构完整 | `langgraph_langchain/data/`（`assets.py`、`snapshots.py`、`sources.py`、`acquisition.py`、`sampling.py`、`database.py`、`adapters/`） | V1.0 的统一 Data 架构大部分已有实现基础，无需新造 |
| F18 | Domain Pack 目录已存在（`runtime/domain_packs.py`、`runtime/financial/`、`runtime/macro/`、`runtime/financial_agent/`） | 目录清单 | B10 金融 / Macro 能力按 Domain Pack 迁入是现实可行路径 |
| F19 | Legacy 与 V2 双 Runtime 并存、Dashboard 认证缺失等既有问题延续 | A1.3.3 冻结基线结论（2 Dashboard 失败、1 reliability 失败为已知既有问题） | 收敛期间不得修复范围漂移，须按 R0/R1 计划处置 |

### 1.6 测试基线

审计基线全量收集 2001 tests；实际执行 1985 passed / 3 failed / 13 skipped。3 个失败为已知既有问题（2 Dashboard 认证、1 reliability），与本次架构评审无关。此为上一轮 A1.3.3 审计执行结果，非本轮重复执行（本轮为只读架构审计，未重跑全量测试）。

---

## 2. V1.0 → V1.1 修订决策记录

### 2.1 采纳的修订

| # | V1.0 原表述 | V1.1 修订 | 理由（代码证据） |
|---|-------------|-----------|------------------|
| M1 | R0 为单一大阶段 | R0 拆分为 R0a（不可信执行拦截）与 R0b（身份与路径边界） | F4/F7 与 F9/F11 是两类独立风险，可独立验收 |
| M2 | Worker Contract 全新设计 | 以现有 `WorkerBackend` / `WorkerDispatcher` 契约为基座扩展 | F5：`worker_backend_from_env()` 语义已符合 Fail-Closed，直接接线即可 |
| M3 | Unified Evidence 全新设计 | 以 `EvidenceRegistry` + `collector.py` 为唯一权威，删除 legacy 默认 verified 旁路 | F13/F15/F16 |
| M4 | 统一 Data 全新 DataSource 契约 | 复用 `data/` 现有模块，仅补齐 Identity/授权/生命周期缺口 | F17 |
| M5 | "Session ID 不再作为授权依据"作为 R0 目标 | 细化为三步：格式校验（立即）→ 归属校验（R0b）→ 用户级授权模型（R1 后） | F9 是可直接被利用的 P0，必须先行止血 |
| M6 | Roadmap R3 "通用分析智能" 单阶段 | 拆出 R2.5（Legacy 能力迁移到 Unified Runtime 的行为对齐） | Legacy 拥有 V2 缺失的动态探索/多轮 Tool 能力（V1.0 §10.2 自述），直接做 Planner 智能化会失去基线能力 |

### 2.2 拒绝的 V1.0 设计

| # | V1.0 提议 | 拒绝理由 |
|---|-----------|----------|
| R1 | 全新 Unified AnalysisRequest 大一统契约 | 当前 API 层已有稳定的请求/响应模型与 session 生命周期（`api_server_langgraph.py`），新契约应通过 Adapter 逐步吸收，而非一次性替换 |
| R2 | V1.0 §12 "不建议拆成几十个小阶段" 的粗粒度路线 | 与其自身 R0–R7 的 Gate 要求矛盾：8 个阶段 × 多主题 × 单一 Gate 无法做增量验收；V1.1 改为阶段内子里程碑 |

### 2.3 延迟决策（DEFERRED）

| # | 议题 | 延迟原因 | 决策时点 |
|---|------|----------|----------|
| D1 | Runtime V2 是否保留独立入口还是直接改名 Unified Runtime | 收敛路径取决于 R0 后 V2 稳定性数据 | R1 启动前 ADR 复审 |
| D2 | B8 Acquisition 独立平台化 vs 并入 `data/` 模块扩展 | 需先完成 R0/R1，避免并发大迁移 | R4 启动前 |
| D3 | Dashboard 定位（产品 vs 观测工具） | 与 B11 冻结契约相关，单独立项处理 | R7 前 |

---

## 3. 目标架构（V1.1 定稿）

### 3.1 分层职责（修订版）

```
┌─────────────────────────────────────────────────────┐
│ Application Layer: API / Dashboard / Session        │
│  - 身份与授权（R0b 起）                              │
│  - Session = (owner_user, session_id) 二元组        │
├─────────────────────────────────────────────────────┤
│ Unified Analysis Runtime（以 Runtime V2 为基座）     │
│  - Plan / TaskGraph / StateMachine / Scheduler      │
│  - Legacy 能力经 Capability Adapter 接入            │
├──────────────────────┬──────────────────────────────┤
│ Execution Layer      │ Trust Layer                  │
│  WorkerDispatcher    │  EvidenceRegistry（唯一权威） │
│  ├ Docker Backend    │  Verifier（content_validator │
│  │  （生产唯一）      │   强制）                     │
│  └ Process Backend   │  Report Gate                 │
│     （dev/test 显式） │                             │
├──────────────────────┴──────────────────────────────┤
│ Data Platform: DataAsset / Snapshot / Source        │
│  （复用 data/ 现有实现 + 补授权/生命周期）            │
├─────────────────────────────────────────────────────┤
│ Domain Packs: General / Macro / Finance             │
│  （Manifest + Metric/Method/Rule，无自有 Runtime）   │
├─────────────────────────────────────────────────────┤
│ Acquisition Platform（R4 起独立，经统一 Task 协作）   │
└─────────────────────────────────────────────────────┘
```

### 3.2 最小契约集（V1.1 裁定）

V1.0 提出约 20 个新契约。V1.1 裁定其中 **9 个为必须冻结的核心契约**，其余通过复用/延迟合并解决：

| 契约 | 基座 | 冻结时点 |
|------|------|----------|
| C1 Worker Job Contract | `execution/worker_backend.py` 现有接口 + `docker/worker/worker_entrypoint.py` | R0a |
| C2 Execution Backend Policy | `worker_dispatcher.py`（env 显式选择、无降级） | R0a |
| C3 Session Identity | `(owner_user, session_id, created_at)`，session_id 格式白名单 | R0b |
| C4 DataReference | 复用 `data/assets.py` 的 Asset 身份，新增引用解析层 | R1 |
| C5 Task State Machine | Runtime V2 `runtime/models.py` 状态机 + 补 Retry/Cancel 语义 | R1 |
| C6 Evidence Contract | `evidence/models.py` 现状（4 级状态）+ 禁止默认 verified | R0a（仅旁路封堵） |
| C7 Report Gate | `verification/verifier.py` + `tools/tool_finish_report.py` 收口 | R2 |
| C8 Domain Pack Manifest | `runtime/domain_packs.py` 扩展 | R5 |
| C9 Acquisition Task Boundary | B8 AcquisitionService 契约 + 统一 Task 包装 | R4 |

明确不新造的契约：Unified AnalysisRequest（用 Adapter）、ExecutionResult（用 Task State 承载）、DataSource 统一抽象（用 `data/sources.py` 现状）。

---

## 4. 模块处置矩阵（代码级，取代 V1.0 §10.1 初步矩阵）

| 现有代码 | 实际位置 | 处置 | 目标归属 | 阶段 |
|----------|----------|------|----------|------|
| Legacy ReAct Runtime | `langgraph_agent.py` | **保留**，能力盘点后经 Adapter 接入 | Unified Runtime / Agent Executor | R1 |
| Runtime V2 核心 | `runtime/context.py`, `runtime/graph.py`, `runtime/models.py`, `runtime/plans.py`, `runtime/executor.py` | **吸收为 Unified Runtime 基座** | Unified Runtime | R1 |
| Runtime 路由 | `runtime/runtime_route.py` | **保留并扩展**为 Unified 路由策略 | Unified Runtime | R1 |
| ProcessWorkerBackend | `execution/worker_backend.py:171` | **限制为 dev/test 显式选择**，生产禁用 | Execution Layer | R0a |
| DockerWorkerBackend | `execution/worker_backend.py:342` + `docker/worker/` | **生产唯一后端**，完成主链路接线 | Execution Layer | R0a |
| WorkerDispatcher | `execution/worker_dispatcher.py` | **统一入口**（`create_worker_backend` 已符合 Fail-Closed） | Execution Layer | R0a |
| PythonTaskExecutor | `execution/python_task_executor.py` | **改造**为经 Dispatcher 调 Worker | Execution Layer | R0a |
| delegate_analysis | `tools/tool_delegate.py` | **迁移**到 Docker Worker 执行或停用 | Execution Layer | R0a（P0） |
| Session 管理 | `api_server_langgraph.py:130-553` | **加固**：ID 校验 → 归属 → 授权 | Application | R0b |
| WorkspaceManager/路径 | `api_server_langgraph.py:464`（get_session_workspace） | **加固**：canonical path + 白名单 | Application | R0b |
| Evidence Registry | `evidence/registry.py` | **唯一权威**，封堵默认 verified 旁路 | Trust | R0a/R2 |
| Evidence Collector | `evidence/collector.py` | **参照实现**推广到全部执行路径 | Trust | R2 |
| Legacy Evidence 适配 | `evidence/adapters.py`, `operations.py`, `sqlite.py` | **收紧**：恢复即 unverified，禁止默认 verified | Trust | R0a |
| DataAsset/Snapshot | `data/assets.py`, `data/snapshots.py` | **保留**，补授权与生命周期 | Data Platform | R1/R2 |
| AssetResolver | `runtime/asset_resolver.py` | **保留**，接 DataReference | Data Platform | R1 |
| B8 Acquisition | `data/acquisition.py` + B8 冻结模块 | **能力迁移**，接入统一 Task | Acquisition Platform | R4 |
| Macro Pack | `runtime/macro/` | **迁移**到 Domain Pack Manifest | Domain | R5 |
| Financial Pack | `runtime/financial/`, `runtime/financial_agent/` | **迁移**（B10 冻结契约优先） | Domain | R5 |
| B11 Multi-Agent | `runtime/multi_agent.py`, `runtime/evaluation/` | **吸收**为 Orchestration 能力，不建第二 Runtime | Unified Runtime | R6 |
| Verifier | `verification/verifier.py` | **强化**为独立 Verification 层 | Trust | R2 |
| Report 工具 | `tools/tool_finish_report.py`, `runtime/analysis_report.py` | **收口**到 Report Composer + Gate | Trust | R2 |
| Dashboard | （B11 冻结范围） | **保留**，修复认证失败（既有失败项） | Application | R7 |

---

## 5. 四种产品场景的统一执行流程（V1.1 确认版）

V1.0 §11 的四场景统一流程在 V1.1 中确认，但增加以下硬约束：

1. **所有场景的代码执行一律经 C1 Worker Contract → Docker Backend**（ADR-006，Fail Closed）。
2. **所有场景的结论性输出必须经 C6 Evidence + C7 Report Gate**。
3. **所有场景的 session 资源访问必须满足 C3 归属校验**。
4. **所有场景共享同一 Plan/Task/Evidence/Data 契约**，差异仅允许出现在 V1.0 §11 已列的六个维度（Acquisition Strategy、Domain Pack、Methods、Task Graph、Verification Rules、Report Template）。

场景与现有实现的映射：

| 场景 | 数据入口 | 现有基础 | 缺口 |
|------|----------|----------|------|
| A 上传数据分析 | 文件 | Legacy Runtime 全链路 + Docker Worker（未接线） | R0a 接线、R0b 隔离 |
| B 公开宏观数据 | API/Connector | `runtime/macro/` + B8 Acquisition | R4 平台化、R5 Pack |
| C 企业财务分析 | 财报 | `runtime/financial/`（B10 冻结） | R5 Pack 化接入 |
| D 多源综合研究 | 多数据源 | B11 Multi-Agent（基于 V2） | R1 统一、R6 综合 |

---

## 6. Roadmap（V1.1 修订版）

### R0a — Untrusted Execution Interception（安全止血，最高优先）

**目标：任何 LLM 生成代码不再进入 API 进程或无隔离子进程。**

范围：
1. 主链路（`langgraph_agent.py:1157`、`python_task_executor.py:43`）切换到经 `worker_backend_from_env()` 的显式后端选择。
2. 生产配置强制 `WORKER_BACKEND=docker`；`process` 仅允许显式 dev/test 环境变量声明（禁止静默默认）。
3. `delegate_analysis`（`tool_delegate.py`）迁移到 Worker 执行路径或临时停用并给出替代说明。
4. 封堵 Evidence 默认 verified 旁路（`operations.py:27`、`sqlite.py:34` 恢复为 `unverified` 起步）。
5. 保留 `RUNTIME_V2_ENABLED` 默认关闭（不在 R0 改变 Runtime 路由）。

Gate：
- [ ] 生产路径不存在任何 API 进程内代码执行（含 delegate）。
- [ ] Docker 失败时不自动降级到 Process（Fail Closed 验证）。
- [ ] Evidence 恢复/注册无默认 verified 通道。
- [ ] 全量测试无新增失败。

### R0b — Identity & Path Boundary

**目标：session 与文件边界可被独立验证。**

范围：
1. session_id 格式白名单（如 `^[a-zA-Z0-9_-]{8,64}$`），在 `get_session_workspace()` 与所有 API 入口统一校验。
2. session 归属校验：Session 元数据记录 owner，跨 owner 访问返回 403/404。
3. workspace canonical path 防穿越复核（含 F12 已有的 `relative_to` 防线联动）。
4. 全局 token → 最小用户身份（不要求完整 IAM，单用户部署可先绑定单 owner）。

Gate：
- [ ] 恶意 session_id 全部被拒（穿越、绝对路径、编码绕过）。
- [ ] 跨 owner session 访问全部被拒。
- [ ] 单文件响应防线在污染 workspace 场景下仍有效。

### R1 — Unified Runtime Convergence

以 V2 为基座，Legacy Capability Adapter 接入（详细按 V1.0 §10.2/§10.3，采纳 M6 拆分：先对齐 Legacy 行为基线，再做能力吸收）。

Gate：沿用 V1.0 R1 Gate，另加：
- [ ] C5 Task State Machine 冻结并通过契约测试。
- [ ] Legacy 能力清单中每项有迁移/保留/退役决定记录。

### R2 — Trustworthy Analysis Product

Evidence/Verification/Report Gate 推广到全部执行路径（以 `collector.py` 为参照实现）。

Gate：沿用 V1.0 R2 Gate，另加：
- [ ] C7 Report Gate 冻结，未验证关键结论禁止进入报告。

### R2.5 — Legacy Behavior Parity（采纳 M6）

在 R3 之前完成 Legacy 动态探索/多轮 Tool/复杂拆解能力在 Unified Runtime 下的行为对齐测试，防止智能化过程中丢失基线能力。

### R3–R7

沿用 V1.0 的 R3（General Intelligence）、R4（Acquisition）、R5（Domain Packs）、R6（Multi-source Research）、R7（Production Readiness）目标与 Gate，不再重复。启动条件：R2.5 完成。

---

## 7. ADR 增补（在 V1.0 14 条 ADR 基础上）

| ADR | 决策 | 状态 |
|-----|------|------|
| ADR-017 | Worker Backend 选择必须显式，环境变量驱动，无静默降级 | 冻结（R0a） |
| ADR-018 | Session 资源授权以 owner 身份为准，session_id 仅是标识符 | 冻结（R0b） |
| ADR-019 | EvidenceRegistry 是唯一 verified 状态权威；任何"自我声明 verified"入口视为缺陷 | 冻结（R0a） |
| ADR-020 | Runtime V2 为 Unified Runtime 唯一基座；Legacy 只能经 Capability Adapter 接入 | 冻结（R1 启动时复核 D1） |
| ADR-021 | Process Worker 仅限显式声明隔离需求的开发/测试环境 | 冻结（R0a） |

V1.0 ADR-001～016 全部确认采纳。

---

## 8. 风险登记册（V1.1 初始版）

| ID | 风险 | 等级 | 缓解 | 关联证据 |
|----|------|------|------|----------|
| P0-1 | session_id 路径穿越读写宿主文件 | P0 | R0b 第 1-3 项；修复前对外部部署禁用 | F9/F10 |
| P0-2 | LLM 代码在 API 进程执行（delegate_analysis + Process 默认） | P0 | R0a 全部；修复前单用户/可信环境运行 | F4/F7/F8 |
| P0-3 | Process→Docker 回退破坏隔离（若实现自动降级） | P0 | ADR-017 禁止；现有 `create_worker_backend` 已 Fail Closed，保持 | F5 |
| P1-1 | 全局 token 无 owner 语义，跨 session 访问 | P1（多用户为 P0） | R0b 第 2/4 项 | F11 |
| P1-2 | Legacy Evidence 默认 verified 旁路 | P1 | R0a 第 4 项 | F15 |
| P1-3 | 双 Runtime 并存导致能力漂移 | P1 | R1/R2.5 | F1/F19 |
| P2-1 | Dashboard 2 个认证相关既有失败 | P2 | R7 或独立修复包（不混入架构收敛提交） | F19 |
| P2-2 | 领域能力（B8/B10/B11）暂为孤岛 | P2 | R4/R5/R6 计划性迁移 | F17/F18 |

---

## 9. 第一实施任务（首个 Codex 工作包）

**任务：R0a-1 — Production Execution Path Hardening**

- 范围：`langgraph_agent.py:1139-1157`、`execution/python_task_executor.py`、`execution/worker_dispatcher.py`、`tools/tool_delegate.py`。
- 交付：
  1. 主链路经 `worker_backend_from_env()` 显式选择后端。
  2. 生产配置样例与文档明确 `WORKER_BACKEND=docker`。
  3. `delegate_analysis` 迁移到 Worker 或停用（含工具描述更新与回归测试）。
  4. Evidence 默认 verified 旁路封堵 + 回归测试。
- 明确排除：不改 Runtime 路由默认值、不做 R0b、不动 B11 冻结契约。
- 验收 Gate：R0a 全部 Gate 项。

---

## 10. 未能验证 / 超出本轮范围事项

| 事项 | 原因 |
|------|------|
| B8 AcquisitionService 内部契约细节 | B8 冻结模块未在本轮逐文件复核，R4 前需专项确认 |
| `verification/verifier.py` 完整语义 | 仅确认存在与角色，未审计其验证强度（R2 前需专项审计） |
| Docker Worker 全部网络/资源限制组合的真实生效证据 | A1.3.3.2/A1.4.2.x 已有阶段性验收记录，但本轮未重复执行容器级验证 |
| 多用户并发下的 Session 存储一致性 | `api_server_langgraph.py:130` 使用内存 dict，需 R0b 时评估并发安全 |
| 全量测试复跑 | 本轮为只读架构审计，测试结果引用 A1.3.3 审计执行记录，未亲自重跑 |

---

## 11. 最终判定

| 维度 | 判定 |
|------|------|
| V1.0 蓝图 | MAJOR_REVISION_REQUIRED → 本文档完成修订 |
| V1.1 蓝图 | **ADOPTED — READY FOR PHASED IMPLEMENTATION**（以 R0a 为第一工作包） |
| 生产实施 | 禁止在 R0a/R0b 完成前对外部/多用户环境开放 |
| 冻结契约影响 | 不修改 B11 冻结契约；Runtime 默认路由保持不变 |

我先按仓库当前已提交内容校准路线，而不是只沿用上一轮的规划。

当前分支 `codex/phase1-m2.4-sqlite-schema` 指向提交 `f695bf0`（`docs(runtime): inspect sqlite asset consumption`）。仓库里其实已经提交了 Phase 0 的产品文档和 Phase 1 规划，因此 Phase 0 不应再从零开始；更准确的状态是：Phase 0 文档基线已形成，但需要确认其与当前 M2.7.1 代码现状一致，并将路线切换到 Phase 1 的实际实施。

我检查到的关键文档包括：

- `docs/product/product-vision.md`
- `docs/product/roadmap.md`
- `docs/product/phase0-scope-freeze.md`
- `docs/product/phase0-summary.md`
- `docs/architecture/module-classification.md`
- `docs/architecture/target-architecture-phase1.md`
- `docs/roadmap/phase1-general-analysis-mvp.md`
- `docs/architecture/M2.7.1-runtime-sqlite-asset-consumption-inspection.md`

其中 Phase 0 文档记录的基线提交是 `5314055`，而当前 SQLite 实施分支已经推进到 `f695bf0`。因此，后续要做的是以当前代码更新/补充架构现状，而不是重复编写一套产品愿景文档。

另外，M2.7.2 SnapshotStore 在目前可确认的提交中仍是实施方案阶段；下面会将它列为待完成任务，而不是已完成能力。

# DeepAnalyze 后续阶段总路线

## 一、阶段顺序总览

| 阶段           | 目标                        | 进入条件                                 | 主要交付                                 | 完成标准                                 |
| ------------ | ------------------------- | ------------------------------------ | ------------------------------------ | ------------------------------------ |
| Phase 0 收敛复核 | 确认产品方向、架构文档与当前代码一致        | 已有 Phase 0 文档；当前代码已推进至 M2.7.1        | 当前代码能力地图、差异清单、更新后的 Phase 1 范围        | 不再存在影响 Phase 1 的架构认知差异；P0/P1 阻塞项明确   |
| M2.7.2       | 持久化 SQLite 查询结果 DataFrame | M2.6 资产注册已完成；M2.7.1 已确认消费缺口          | SnapshotStore、快照引用、完整性校验             | 快照可安全保存、恢复、校验；失败不遗留错误状态              |
| M2.7.3       | 根据 DataAsset 恢复真实数据       | M2.7.2 完成                            | AssetResolver、ResolvedAsset 契约       | 通过 asset_id 可恢复数据；损坏/缺失时明确失败，不重跑 SQL |
| M2.7.4       | 将解析后的资产接入 Runtime 执行路径    | M2.7.3 完成                            | Runtime 输入解析与 E2E                    | SQLite 结果能进入现有分析执行路径，固定 E2E 通过       |
| Phase 1A     | 通用文件分析闭环                  | Runtime 输入链路稳定；Phase 0 阻塞项已确认        | 文件理解、通用 Planner、分析执行、验证与报告           | CSV/Excel/Parquet 至少一条完整可靠链路         |
| Phase 1B     | 通用数据库分析闭环                 | Phase 1A 基础稳定；SQLite Runtime 链路可用    | SQL Tool、只读执行、数据库分析报告                | SQLite 数据可被规划、查询、分析、验证并生成报告          |
| Phase 1C     | MVP 固定验收与冻结               | 1A、1B 功能闭环完成                         | Golden Dataset、Golden Questions、验收报告 | 固定数据集/问题集通过；形成可复现质量基线                |
| Phase 2      | 公开数据获取平台                  | Phase 1 MVP 稳定；DataAsset/Source 契约清晰 | Adapter Registry、数据目录、采集/标准化/质量检查    | 至少 4 个可用数据源 Adapter，具备刷新、缓存、来源追踪     |
| Phase 3      | 宏观经济 Domain Pack          | Phase 2 有稳定宏观数据源和指标元数据               | 宏观指标目录、转换规则、分析模板                     | 中美宏观指标对比与联动分析可复现                     |
| Phase 4      | 财务分析 Domain Pack          | 通用 Runtime 与采集层稳定                    | 财务分析模块解耦、财报数据标准化、财务报告                | 中国上市公司与 SEC 财报基础分析可追溯                |
| Phase 5      | 研究型多 Agent                | 单 Planner + 确定性工具可靠；Phase 3/4 数据可用   | 宏观、财务、统计、风险/反例等协作流程                  | 多 Agent 提升复杂研究质量，不绕过证据与验证            |

总体依赖关系：

```
Phase 0 收敛复核
        ↓
M2.7.2 SnapshotStore
        ↓
M2.7.3 AssetResolver
        ↓
M2.7.4 Runtime 输入接入
        ↓
Phase 1A 文件分析闭环
        ↓
Phase 1B SQLite 数据库分析闭环
        ↓
Phase 1C Golden 验收与冻结
        ↓
Phase 2 数据获取平台
        ↓
Phase 3 宏观 Domain Pack
        ↓
Phase 4 财务 Domain Pack
        ↓
Phase 5 研究型多 Agent
```

其中 Phase 1A 和 1B 可以有少量并行工作，但验收必须先保证通用文件分析，再验证数据库分析。Phase 2 不应提前成为 Phase 1 的阻塞项。

# 二、Phase 0：产品收敛与架构复核

## 当前状态

仓库已存在 Phase 0 产品文档：

- 产品愿景
- Scope Freeze
- 模块分类
- Phase 1 目标架构
- Phase 1 Golden Contract / Dataset
- Phase 1 MVP 规划

因此，Phase 0 不需要重做。需要补的是：将文档基线从旧提交同步到当前代码现状，并确认 M2.7.x 工作如何纳入 Phase 1。

## 具体任务

### P0-1：更新当前代码能力地图

重点核对：

- `langgraph_langchain/data/sources.py`
- `langgraph_langchain/data/assets.py`
- `langgraph_langchain/runtime/context.py`
- `langgraph_langchain/runtime/graph.py`
- `langgraph_langchain/runtime/runner.py`
- `langgraph_langchain/runtime/scheduler.py`
- `langgraph_langchain/execution/`
- `langgraph_langchain/verification/`
- `langgraph_langchain/evidence/`
- `langgraph_langchain/tools/`
- `langgraph_langchain/runtime/financial/`
- `langgraph_langchain/runtime/financial_agent/`

将每个模块标记为：

- 通用平台核心
- 通用但需要拆分/解耦
- Financial Domain Pack
- Legacy/Experimental
- Phase 1 阻塞项

### P0-2：复核 Runtime 默认路径

仓库 Phase 0 Summary 已明确：

- Runtime V2 是 Phase 1 目标路径；
- Legacy ReAct 应作为显式兼容/应急路径；
- 不应静默回退到 Legacy；
- 但 Runtime V2 默认切换必须由门禁决定，而不是仅靠文档宣布。

所以需要确认：

1. 当前文件分析默认走哪条路径；
2. Runtime V2 是否已满足默认切换门槛；
3. 如果尚未满足，缺少哪些测试或能力；
4. Legacy 路径是否有明确开关和回退原因记录。

### P0-3：确认 Phase 1 阻塞项

重点是：

- 通用数据理解是否能进入 Planner；
- Planner 是否真正基于 Schema/数据摘要生成计划；
- Runtime 是否能恢复 DataAsset 的真实数据；
- SQL Tool 是否已经接入 Runtime；
- Execution、Verification、Evidence、Report 是否形成闭环；
- Golden E2E 是否覆盖文件与数据库两类输入。

## Phase 0 验收

- 更新后的代码地图与当前提交一致；
- Phase 1 阻塞项有明确 owner/任务顺序；
- 不新增财务语义规则；
- 不进行大规模 Runtime 重构；
- 文档更新后进入 M2.7.2/Phase 1 实施。

# 三、M2.7.2–M2.7.4：完成 SQLite 资产进入 Runtime

这三个子阶段是当前最明确的技术主线。

## M2.7.2：SnapshotStore

目标： SQLite 查询结果注册为 DataAsset 后，数据体可以持久化并安全恢复。

### 实施内容

1. 新增独立 SnapshotStore，不复用 DataCache 的 pickle。
2. 明确快照格式和依赖，例如 Parquet + 显式 `pyarrow` 依赖。
3. 将 SnapshotReference 写入 `DataAsset.source_metadata`。
4. SnapshotReference 至少包含：
   - snapshot_id
   - format
   - relative location
   - physical hash
   - logical DataFrame content hash
   - row/column count
   - created_at
5. 保存过程采用临时文件 + 原子替换。
6. 加载时校验：
   - 路径 containment
   - 文件存在
   - physical hash
   - DataFrame 行列数
   - logical content hash
7. 处理注册失败时快照清理、重复资产、已有快照复用。
8. 保留 truncated 标记；快照只代表已读取的 bounded result，不重新执行 SQL。

### 验收

- DataFrame 保存后可以恢复；
- NULL、日期、数值、空 DataFrame 等场景通过；
- 文件损坏、hash 不匹配、路径逃逸被拒绝；
- 注册持久化失败不会留下孤儿快照或错误资产；
- dedupe 不会删除其他资产正在使用的快照；
- SQL 不会为了恢复数据而重新执行。

## M2.7.3：AssetResolver

目标： 从 Runtime 的 asset_id 找到 DataAsset，并恢复真实数据。

### 实施内容

新增类似：

```
resolve(asset_id) -> ResolvedAsset
```

ResolvedAsset 应包含：

- asset_id
- DataFrame / 数据访问句柄
- Schema
- source metadata
- snapshot reference
- truncation 信息
- 数据版本/hash

Resolver 负责：

1. 从 Runtime Registry 查找 DataAsset；
2. 判断资产类型；
3. 对 SQLite query asset 读取 SnapshotReference；
4. 通过 SnapshotStore 恢复数据；
5. 验证快照与资产元数据一致；
6. 对文件类资产保持现有加载逻辑；
7. 对未知资产类型或缺失快照返回结构化错误。

### 验收

- 通过 asset_id 可恢复 SQLite 查询结果；
- 缺失、损坏、过期快照有明确错误；
- 不重新执行 SQL；
- 不把数据库连接凭据传入 Python executor；
- 多个输入资产能保持 ID 与顺序映射。

## M2.7.4：Runtime 输入接入

目标： 让 Resolver 输出真正进入分析任务。

### 实施内容

1. 在 Runtime request preparation 阶段解析输入资产；
2. 建立明确的多资产输入映射；
3. 先接入一个可控执行路径，例如 Python task executor 或专用 data-consuming tool；
4. 将输入资产版本、Schema、hash 写入 Execution；
5. 将输出注册为新的 DataAsset/Artifact；
6. 确保 Verification/Evidence 能关联输入资产与执行结果；
7. 不要求一次性改造所有旧工具。

### 验收

用户问题：

> “基于 SQLite 查询结果，按地区统计近三年销售额并找出异常变化。”

至少能完成：

```
SQLite Query
→ DataAsset
→ Snapshot
→ AssetResolver
→ Runtime Input
→ Analysis Execution
→ Verification
→ Evidence
→ Finding/Report
```

完成后，停止继续扩展 SQLite Snapshot 层，转入 Phase 1 通用分析能力。

# 四、Phase 1：通用数据分析 MVP

Phase 1 不应只理解为“多支持几个数据源”，而是交付一个完整用户场景。

## Phase 1A：文件分析闭环

### 输入

- CSV
- Excel
- Parquet

### 能力

1. 文件上传与 DataAsset 注册；
2. Schema 和数据质量理解；
3. 缺失、重复、异常值、字段类型和粒度检查；
4. 通用 Planner；
5. AnalysisPlan 校验；
6. 正式分析工具选择；
7. Python/Pandas 确定性执行；
8. Verification；
9. Evidence/Finding；
10. 图表、表格、报告；
11. 多轮追问和会话状态。

### 建议首批正式分析方法

- 描述统计
- 分组比较
- 时间趋势
- 异常检测
- 相关性分析
- Top-N / 排名
- 比率计算
- 数据质量分析

### 验收场景

上传销售 Excel：

> “按地区分析近三年销售趋势，找出异常变化，并说明数据支持哪些结论。”

验收不只是报告生成，还要检查：

- 是否识别正确时间字段；
- 是否使用正确聚合粒度；
- 是否处理缺失和重复；
- 数值是否正确；
- 图表是否与结果一致；
- 结论是否有 Evidence；
- 是否披露数据局限。

## Phase 1B：数据库分析闭环

先 SQLite，后 PostgreSQL/MySQL。

### 实施内容

1. Runtime-facing SQL Tool；
2. 只读 SQL 执行；
3. 查询超时、行数和结果大小限制；
4. Schema-aware Planner；
5. 查询结果资产化；
6. SQL Execution 记录；
7. 结果验证与证据绑定；
8. 数据库输入的报告生成。

### 验收场景

> “分析数据库中各地区近三年销售趋势，识别异常，并给出可复现的 SQL 和计算结果。”

## Phase 1C：Golden 验收与冻结

仓库已经有 Phase 1 Golden Contract 和 Dataset/Questions 文档，后续应以它们为基础扩展，而不是重新建立一套不兼容的测试集。

### 建议验收规模

- 至少 10 个固定数据集；
- 至少 30 个自然语言分析问题；
- 覆盖文件、SQLite、缺失值、重复值、异常值、多表/查询结果、数据不足等场景。

### 核心门禁

- 数值事实正确；
- 必需分析步骤完整；
- Finding 与 Execution/Evidence 绑定；
- 报告不编造；
- 数据不足时明确拒绝或限定结论；
- SQL/代码执行安全；
- 关键回归集通过。

Phase 1 通过后冻结通用分析契约。 后续数据源和领域包应复用它，不再反复改动核心 Runtime 的基本输入/输出语义。

# 五、Phase 2：公开数据获取平台

进入条件： Phase 1 的 DataAsset、Schema、Execution、Evidence、Report 契约稳定。

## Phase 2A：数据源基础设施

先做平台机制，不先做大量数据源。

### 实施内容

- DataSource Adapter 接口；
- DataSource Registry；
- Dataset/Series Catalog；
- fetch / normalize / validate；
- 缓存、重试、限流；
- 手动刷新与定时任务；
- 来源 URL、参数、采集时间、数据期间；
- 数据版本和更新记录；
- 采集失败与质量告警。

数据获取层输出标准化 DataAsset，不直接调用 Agent。

## Phase 2B：第一批数据源

建议按可用性和验收价值分批：

1. 美国宏观： FRED；
2. 美国官方指标： BLS、BEA；
3. 中国宏观： 国家统计局及可稳定使用的公开接口；
4. 中国金融统计： 人民银行公开数据；
5. 美国财报： SEC EDGAR；
6. 中国财报： 交易所公告/财报数据，AKShare/Tushare 作为可替换 Adapter。

不要一开始要求所有源都“生产稳定”。先实现 4 个稳定 Adapter，验证 Adapter 机制和标准化契约。

## Phase 2C：采集质量与运营

- 指标元数据目录；
- 单位、频率、地区、币种；
- 发布时间与观察期间；
- 修订历史；
- 数据缺口检测；
- 缓存刷新策略；
- Adapter 健康状态；
- 数据源变更告警。

### Phase 2 验收

> “对比中国和美国近十年 CPI 变化。”

系统能够自动：

```
识别指标与国家
→ 选择数据源
→ 拉取数据
→ 标准化单位/频率
→ 校验质量
→ 注册 DataAsset
→ 进入通用 Runtime
→ 输出带来源的报告
```

# 六、Phase 3：宏观经济 Domain Pack

进入条件： Phase 2 至少有可用宏观数据源，指标目录和时间序列元数据已经建立。

## 实施内容

### 3A：宏观指标模型

统一：

- 指标定义；
- 国家/地区；
- 单位；
- 频率；
- 观察期间；
- 发布时间；
- 修订版本；
- 季调/非季调；
- 名义/实际；
- 同比、环比、年化、累计值。

### 3B：宏观分析模板

- GDP 增长趋势；
- CPI/PPI 通胀分析；
- 就业与工资变化；
- 利率与通胀联动；
- 货币与信贷变化；
- 贸易与出口分析；
- 多指标对比；
- 历史区间比较；
- 领先/滞后相关分析。

### 3C：结论质量控制

- 事实、相关线索、假设分开；
- 不自动把相关性说成因果；
- 对发布时间滞后和数据修订进行披露；
- 解释需绑定数据证据；
- 支持反例和替代解释。

### 验收场景

> “分析美国近五年通胀与就业变化，比较加息前后差异，并说明哪些结论有数据支持。”

Phase 3 不需要新增独立 Runtime；它应当是宏观指标知识 + 模板 + Adapter 配置 + 领域验证规则。

# 七、Phase 4：财务分析 Domain Pack

进入条件： 通用 Runtime 稳定，Phase 2 数据采集机制可用，财报数据具备可追溯来源。

## 实施顺序

### 4A：财务代码边界整理

把现有财务能力逐步收敛为 Domain Pack：

- `runtime/financial/`
- `runtime/financial_agent/`
- 财务指标、期间语义、单位、财务风险规则
- 财务专用 Planner/Intent
- 财务专用数据服务与验证

不建议一开始做大规模目录迁移。先定义 Domain Pack 接口，再通过适配层逐步移动，保持兼容。

### 4B：财报数据标准化

- 公司与证券标识；
- 财报期间；
- 年报/季报；
- 累计值/单季值；
- 三大报表；
- 会计准则与币种；
- 报告版本与修订；
- 原文定位与 XBRL/公告来源。

### 4C：财务分析能力

- 收入、利润、现金流、负债趋势；
- 盈利质量；
- 偿债能力；
- 营运效率；
- 同业比较；
- 财报异常；
- 风险信号；
- 财务指标计算和口径验证。

### 验收场景

> “分析某上市公司近五年收入、利润、现金流和负债变化，识别盈利质量风险，并给出财报原文和计算证据。”

现有 V10.x 财务期间语义继续保留，但只针对真实验收失败、重大误导或 P0/P1 问题修复。其他边界表达进入 backlog。

# 八、Phase 5：研究型多 Agent

进入条件：

- 单 Planner + 确定性工具已可靠；
- Phase 2 数据源稳定；
- Phase 3/4 领域知识可用；
- Golden Evaluation 能衡量复杂研究质量；
- Evidence/Verification 能约束 Agent 输出。

## 实施内容

只对复杂研究任务启用角色协作：

```
Research Planner
 ├── Macro Analyst
 ├── Fundamental Analyst
 ├── Trend / Statistical Analyst
 ├── Risk & Counterargument Analyst
 └── Research Synthesizer
```

角色只消费已注册数据、正式工具结果和已有 Evidence；不允许独立编造数值。

### 重点能力

- 并行分析；
- 假设对比；
- 看多/看空或支持/反对证据；
- 风险和反例；
- 结论冲突检测；
- 统一报告合成；
- 引用完整性检查。

### 不做

- 自动交易；
- 仓位/订单执行；
- 以交易收益为核心的优化；
- 简单任务默认多 Agent；
- 让 Agent 直接绕过 SQL/Python 工具生成数值。

### 验收场景

> “分析某公司过去五年的财务变化，并结合宏观环境，给出经营风险、支持证据、反面解释和数据局限。”

Phase 5 的成功标准不是 Agent 数量，而是复杂研究报告质量显著优于单 Agent 基线，同时不降低数值正确性和证据完整性。

# 九、建议近期实施批次与验收顺序

不要把上面的阶段直接变成一个巨大的开发任务。建议拆成以下可提交、可验收的批次：

| 批次  | 实施内容                 | 主要产物                       | 验收重点                         |
| --- | -------------------- | -------------------------- | ---------------------------- |
| B0  | Phase 0 文档与当前代码差异复核  | 更新后的模块地图、阻塞项               | 文档与当前分支一致                    |
| B1  | M2.7.2 SnapshotStore | SnapshotStore + 测试         | 持久化、hash 校验、失败清理             |
| B2  | M2.7.3 AssetResolver | Resolver + ResolvedAsset   | asset_id 可恢复数据               |
| B3  | M2.7.4 Runtime 输入接入  | Runtime 输入适配 + E2E         | SQLite 结果进入分析执行              |
| B4  | Phase 1A 文件分析闭环      | Planner/分析/报告集成            | CSV/Excel/Parquet Golden E2E |
| B5  | Phase 1B SQLite 分析闭环 | SQL Tool + Runtime 集成      | SQLite Golden E2E            |
| B6  | Phase 1C 固定验收与冻结     | Golden 报告、质量基线             | 10 datasets / 30 questions   |
| B7  | Phase 2 Adapter 基础设施 | Adapter Registry / Catalog | 数据源契约和质量检查                   |
| B8  | 第一批公开数据源             | 至少 4 个 Adapter             | 可刷新、可追溯、可标准化                 |
| B9  | Phase 3 宏观 Pack      | 指标目录与分析模板                  | 中美宏观分析场景                     |
| B10 | Phase 4 财务 Pack      | 财务模块解耦与财报流程                | 财务分析来源、口径、证据完整               |
| B11 | Phase 5 研究多 Agent    | 研究编排与角色协作                  | 对比单 Agent 基线有质量提升            |

## 十、每个阶段统一采用的停止规则

为了避免再次陷入“发现边界—修复—新增测试—继续发现边界”的循环，建议所有批次遵循：

- P0： 安全、数据损坏、系统不可用，立即修复。
- P1： 固定验收场景失败、严重误导、阻塞下一阶段，当前批次修复。
- P2： 体验改善、低风险边界问题，进入 backlog。
- P3： 理论可能性或未来扩展，暂不处理。

每个批次必须有：

1. 明确用户场景；
2. 明确修改模块；
3. 明确不做事项；
4. 固定测试和验收标准；
5. 通过后的冻结点。

阶段通过后，只允许 P0/P1 回归问题重新打开该阶段。 新能力进入下一阶段，不因“还可以更完善”而反复返工。

## 十一、当前最具体的下一步

结合目前已提交的代码，我建议立即执行：

B0：Phase 0 文档与当前代码差异复核 然后进入 B1：M2.7.2 SnapshotStore。

B0 不是重新设计产品，而是检查 `5314055` 的 Phase 0 文档与当前 `f695bf0` 代码之间的差异，重点更新：

- 当前 Runtime 默认路径与切换门槛；
- SQLite M2.7.1 已确认的消费缺口；
- M2.7.2–2.7.4 的明确依赖；
- Phase 1 当前阻塞项；
- Phase 1 Golden Contract 与实际实施顺序。

之后不建议再插入新的金融语义开发。先把 SQLite 数据资产链路真正接入 Runtime，再用文件和数据库两个真实用户场景交付通用分析 MVP。

最终主线： 当前阶段不是“继续做金融 Agent”，而是完成通用数据资产链路 → 通用分析闭环 → 公开数据获取 → 宏观/财务领域能力 → 复杂研究协作。
---

## 十二、B8 冻结状态更新（B8.8–B8.11 验收完成，2026-11-15）

B8 数据获取平台已完成 B8.8–B8.11 连续验收并冻结：

- B8.8 契约与注册桥一致性：`5e218a7 fix(data): harden acquisition contract and asset consistency`
- B8.9 数据版本与历史追溯：`ad8d029 feat(data): strengthen acquisition version history and lineage`
- B8.10 质量、健康、限流与失败恢复：`2a568c3 feat(data): strengthen acquisition quality and operational governance`
- B8.11 最终验收与冻结：见 `docs/architecture/reviews/b8.11-data-acquisition-final-acceptance.md`

验收要点：

- 五个 Adapter（FRED/BLS/BEA/SEC/NBS）均有离线完整链路测试（Catalog → Fetch → Normalize → Metadata → Quality → Hash → History → Snapshot → Asset → Runtime → Evidence）。
- Runtime V2 可消费采集 DataAsset；Evidence 可追溯到原始来源；Verified Report 披露输入来源与输出资产。
- B6 Golden 基线未被破坏（9 passed, 4 skipped）。
- 全量测试（排除 financial）：995 passed / 5 skipped / 2 failed，失败为既有 dashboard 基线失败，与 B8 无关。
- 真实 API Smoke 因凭据缺失跳过，不作为通过依据（运行命令与环境变量见 B8.11 验收文档）。

按总路线：B8 标记为完成并冻结，通用采集层不再扩展；下一步进入 Phase 3（B9 宏观经济 Domain Pack）完整实施，B10 财务 Pack 保持独立边界，B11 多 Agent 研究编排暂不启动。

# Codex 自动实施任务书：B10.2 Financial Models & Normalization

> 项目：DeepAnalyze / data_analysis_agent_docker  
> 阶段：Phase 1 — B10.2  
> 任务性质：Financial Pack 数据模型重构与标准化底座建设  
> 实施模式：自动连续实施、阶段验收、独立提交、逐阶段推送  
> 权威计划：`docs/newMain/toDo.md`  
> 架构依据：B10.1 Financial Pack Architecture 与 Codebase Inspection  
> 前置状态：B9 已正式冻结；B10.2 已解锁  
> 本任务不包含：B10.3 及以后阶段，不实现完整 Financial Agent，不重新打开 B9

---

# 0. 你的角色与最高执行原则

你是一名资深 Python 架构师、金融数据工程师、Agent Runtime 工程师和测试工程师。

你需要直接在当前 Git 仓库中完成 B10.2。

不是只给建议、设计方案或伪代码，而是必须：

1. 阅读项目权威计划和 B10.1 架构文档。
2. 检查当前真实代码，确认设计与代码一致。
3. 完成 B10.2 开工门禁。
4. 按阶段实施生产代码。
5. 为每个阶段编写或更新测试。
6. 运行针对性测试及必要回归。
7. 通过阶段验收后，独立 commit。
8. 推送到当前远端分支。
9. 自动进入下一阶段，不需要用户逐阶段确认。
10. 最后生成完整验收文档。

**必须遵守：真实代码优先于文档推断，现有架构优先于重新设计，数据语义优先于实现便利，验证结果优先于口头声明。**

不要仅完成文档就宣称实施完成。

不要为了尽快通过测试而修改或删除原有有效测试断言。

不要通过大量兼容性分支掩盖模型设计错误。

不要将 B10.2 扩大成完整财务分析系统。

---

# 1. 项目背景与目标

DeepAnalyze 是一个通用数据分析 Agent Runtime。

总体数据链路：

User Goal
→ Task Planner
→ Data Understanding
→ Analysis Skill Retrieval
→ Tool Selection
→ Execution
→ Evidence
→ Verification
→ Insight
→ Report

Financial Pack 是领域 Pack，不是独立的数据分析平台。

B10.2 的核心目标：

建立可扩展、语义明确、可追溯、可兼容的 Financial Models 和 Financial Normalization 底座，为后续 Financial Adapter、Financial Metrics、Runtime Integration、Evidence Integration 和 Golden Evaluation 提供可靠基础。

本阶段只建设数据模型与标准化底座。

不提前实现 B10.3–B10.7 的功能。

## 1.1 必须遵守的领域隔离

Financial Pack 与 Macro Pack：

- 领域模型相互独立。
- 领域枚举相互独立。
- 领域转换规则相互独立。
- 不允许 Financial Pack 直接 import Macro Pack 的内部实现。
- 不允许为 Financial Pack 修改已冻结的 B9 生产代码。

允许：

- 参考 B9.8.3 的显式统计口径设计模式。
- 参考 B9 的资产身份键哲学。
- 参考 B9 的转换结果与血缘记录机制。
- 参考 B9 的测试组织方式。

不允许：

- 直接复用 MacroStatisticKind。
- 直接依赖 `runtime/macro/statistics.py`。
- 为适配 Financial Pack 而修改 Macro Catalog、Comparison 或 Macro Runtime。

---

# 2. 开始实施前必须读取的文件

首先读取并理解以下文件：

1. `docs/newMain/toDo.md`
2. `docs/architecture/b10.1-financial-pack-architecture.md`
3. `docs/architecture/b10.1-codebase-inspection.md`
4. `docs/architecture/reviews/b9.8.3-cpi-semantics-fix-acceptance.md`
5. `docs/architecture/reviews/b9.8.2-final-integration-acceptance.md`

文件实际路径可能与上面不同。

如果路径不存在：

- 使用 `find`、`grep`、Git tree 或文件搜索定位真实文件。
- 不要凭文件名自行新建重复文档。
- 在实施报告中记录实际采用的文件路径。

同时检查：

- `langgraph_langchain/runtime/financial/`
- `langgraph_langchain/runtime/financial_agent/`
- `tests/financial/`
- `tests/financial_agent/`
- `tests/`
- Platform 的 DataAsset、SnapshotStore、AssetResolver、register_dataframe 实现。
- AcquisitionService 和 B8 的数据接入边界。
- EvidenceRegistry、canonical evidence 和 Runtime execution 相关接口。

以真实代码为准，不得只依据 B10.1 文档推断现状。

---

# 3. Git 与工作区安全规则

这是强制要求，优先级高于所有实施任务。

## 3.1 开始时记录完整状态

执行并记录：

```bash
git status --short
git status
git branch --show-current
git rev-parse HEAD
git log -8 --oneline --decorate
git remote -v
git diff --stat
git diff --cached --stat
git ls-files --others --exclude-standard
```

检查：

- 当前分支。
- 当前 HEAD。
- 本地与远端关系。
- 用户已修改文件。
- 用户已暂存文件。
- 未跟踪文件。
- 是否存在冲突。
- 是否存在未推送提交。

## 3.2 绝对禁止

不得执行：

```bash
git reset --hard
git clean -fd
git clean -fdx
git checkout -- .
git restore .
git stash
git stash pop
git add -A
git add .
git push --force
git push --force-with-lease
```

除非用户明确授权，不得覆盖、删除或重写用户已有内容。

特别注意：

`docs/newMain/temp.md` 和 `B1-B8方案.md` 曾经属于用户工作区内容。

必须重新检查其实际状态，不得假设它们仍处于未跟踪状态。

不得将用户已有 staged changes 混入 B10.2 提交。

## 3.3 提交规则

每个阶段：

1. 只暂存本阶段明确修改的文件。
2. 提交前执行 `git diff --cached --check`。
3. 检查 staged diff。
4. 确认不包含用户文件。
5. 独立 commit。
6. push 当前分支。
7. 确认远端提交 SHA 与本地一致。

禁止使用：

```bash
git add -A
git add .
```

采用明确路径：

```bash
git add path/to/file1 path/to/file2
```

如果用户预先 staged 的文件与本阶段文件冲突：

- 立即停止提交。
- 不修改用户 staged 内容。
- 汇报冲突。
- 不得自行 reset 或 unstage 用户文件。

## 3.4 远端同步

开始实施前：

- 确认当前分支 upstream。
- 检查远端是否有领先提交。
- 如远端领先，先安全同步。
- 如存在分叉，不得强推或覆盖。

所有阶段提交必须推送到当前工作分支。

不允许自动切换分支。

---

# 4. B10.2.0：Pack Contract 与注册机制补交

这是正式代码重构前的第一个强制门禁。

## 4.1 任务

检查 B10.1 中标记为“解锁补交”的 Financial Pack Contract 和注册机制。

重点检查：

- Pack Contract 是否存在。
- Contract 是否定义 Financial Pack 的身份、能力和边界。
- Pack 注册机制是否真实存在。
- Runtime 是否可以发现和注册 Financial Pack。
- Financial Pack 是否与 Macro Pack 隔离。
- Contract 是否具有测试覆盖。
- 是否存在只在文档中声明、但没有实际实现的能力。

重点检查：

`tests/test_financial_pack_contract.py`

以及真实注册模块。

## 4.2 实施要求

如果缺少：

- 补充最小 Contract。
- 补充最小注册机制。
- 补充对应测试。
- 不提前引入复杂插件市场或动态代码加载系统。

Pack Contract 应至少描述：

- pack_id
- pack_version
- domain
- supported_capabilities
- model/schema contract
- normalization contract
- registration identity
- dependency boundary

具体字段应结合现有项目接口确定，不得为满足上述示例而破坏已有契约。

## 4.3 测试

至少验证：

1. Financial Pack 可以正常注册。
2. 重复注册具有明确行为。
3. 无效 Contract 被拒绝。
4. Financial Pack 不依赖 Macro Pack。
5. 不影响现有 Macro Pack 注册。
6. 不影响现有 Runtime Pack Discovery。

## 4.4 文档

新增或更新：

`docs/architecture/reviews/b10.2.0-pack-contract-gate.md`

记录：

- 原始缺口。
- 真实代码位置。
- Contract 设计。
- 注册行为。
- 测试结果。
- 门禁结论。

## 4.5 验收

所有必要测试通过后：

Commit：

```text
feat(financial): complete financial pack contract and registration
```

推送成功后进入 B10.2.1。

如果已有实现完整且测试充分，可以不制造空提交，但必须提供明确证据，并继续下一阶段。

---

# 5. B10.2.1：基线复测与 Financial Asset Identity 冻结

这是第二个强制门禁。

本阶段先做检查和文档，不进行 Financial Models 的生产代码重构。

## 5.1 建立测试基线

运行：

```bash
pytest tests/financial -q
pytest tests/financial_agent -q
pytest tests/test_financial_pack_contract.py -q
```

如果实际目录或测试文件不同，以仓库现状为准。

记录：

- Passed
- Failed
- Skipped
- XFailed
- 完整失败 nodeid
- 执行命令
- Python 版本
- 依赖环境
- HEAD SHA

对于已有失败：

- 保存基线。
- 判断是环境性、历史性还是当前代码缺陷。
- 不允许将已有失败直接认定为本轮引入。
- 不允许通过删除测试来消除失败。

## 5.2 B9 IndicatorAssetKey 对齐检查

检查真实 B9 实现。

B9 的身份键为：

```text
indicator_id
country
concept
frequency
source_id
dataset_id
data_version
```

实际实现中的 `IndicatorAssetKey` 具有六个构造字段，`data_version` 由资产版本体系承载。

必须检查真实定义，不得只依据本文档。

Financial Pack 不得直接使用 Macro 的 IndicatorAssetKey。

Financial 资产身份必须由 Financial 自己定义。

## 5.3 Financial Asset Identity 设计

结合 B10.1 §6 和实际 Platform DataAsset 实现，明确以下维度：

```text
market
entity_id
canonical_concept
statement_type
period
reporting_basis
currency
filing/accession version
```

重点回答：

1. 同一个 Entity 不同报表是否区分身份。
2. 同一报告期间不同 accession 是否区分版本。
3. 同一 accession 的修订数据如何识别。
4. 同一概念不同币种是否产生不同标准化资产。
5. 同一企业在不同市场如何区分。
6. 同一 Entity 的合并报表与母公司报表如何区分。
7. 标准化结果如何追溯原始 filing。
8. DataAsset 的 data_version 与 filing/accession 如何关联。

特别注意：

`canonical_concept` 不应成为唯一资产键。

资产身份应区分：

- 逻辑资产身份。
- 具体数据版本。
- 原始来源身份。
- 标准化转换版本。

不要把所有维度不加区分地塞进一个 hash。

应检查 B9 的 Asset Identity 和 DataAsset 实际实现，设计 Financial 自己的键与 Platform provenance 的映射。

## 5.4 资产键设计验收

新增：

`docs/architecture/reviews/b10.2.1-financial-identity-baseline.md`

至少包含：

- Financial Identity 定义。
- B9 Identity 对照。
- DataAsset 映射方案。
- Filing/Accession 版本规则。
- Reporting Basis 规则。
- Currency 规则。
- 资产注册规则。
- 测试基线结果。
- 风险与未决问题。

## 5.5 本阶段冻结门禁

以下全部满足才能进入 Models 重构：

- B10.2.0 已完成或已有实现经测试证明完整。
- Financial 基线已保存。
- Financial Identity 已明确。
- DataAsset 映射已明确。
- Filing/Accession 版本规则已明确。
- 未发现与 B10.1 契约冲突。

提交：

```text
docs(financial): freeze financial identity and baseline
```

推送成功后进入 B10.2.2。

如果 Identity 仍有未决 P0/P1，停止，不得开始 Models 重构。

---

# 6. B10.2.2：Financial Models 重构

本阶段正式进入生产代码。

先检查当前模型和所有调用方，再确定兼容迁移策略。

禁止直接删除旧字段或一次性修改所有下游模块。

## 6.1 模型目标

Financial Models 至少覆盖：

### Entity

代表真实企业或金融报告主体。

建议考虑：

- entity_id
- legal_name
- display_name
- market
- exchange
- ticker
- country
- identifiers
- currency_context
- metadata

具体字段根据当前项目及 B10.1 契约确定。

Entity Identity 不应仅依赖 ticker。

同一个 ticker 可能在不同市场对应不同企业。

必须明确：

- 稳定内部 entity_id。
- 外部证券代码。
- 市场。
- 其他权威企业标识。
- 标识来源与有效期。

不得凭空创建不存在的外部企业标识。

### FinancialStatement

至少支持：

- entity_id
- statement_type
- period_start
- period_end
- fiscal_year
- fiscal_period
- reporting_basis
- currency
- filing_date
- accession_number
- source_id
- version
- line_items
- metadata

必须明确区分：

- instant statement。
- duration statement。

例如：

资产负债表是某个时点的数据。

利润表、现金流量表是一个期间的数据。

不要将两者都简化成一个 `year` 字段。

### FinancialLineItem

至少支持：

- canonical_concept
- original_concept
- value
- statistic_kind
- unit
- currency
- period_start
- period_end
- source_metadata
- normalization_metadata

数值类型必须使用：

```python
Decimal
```

禁止在新的标准化核心模型中使用 float 作为财务金额的权威存储类型。

必须注意 Decimal 的精度和序列化问题。

## 6.2 金融语义字段

建议在 Financial Pack 内部定义：

```python
FinancialStatisticKind
```

候选类型：

```text
absolute_amount
per_share
percent_ratio
times_ratio
growth_rate
yoy_index
unknown
```

这是候选设计，实施前必须检查真实业务和 B10.1 契约，必要时调整。

不允许直接 import MacroStatisticKind。

必须区分：

| 类型 | 含义 | 示例 |
|---|---|---|
| absolute_amount | 绝对金额 | Revenue |
| per_share | 每股数值 | EPS |
| percent_ratio | 百分比比率 | Gross Margin |
| times_ratio | 倍数比率 | Current Ratio / PE |
| growth_rate | 增长率 | Revenue YoY |
| yoy_index | 同比指数 | 上年同期=100 |

`growth_rate` 还必须有独立的比较基期语义，例如：

```text
yoy
qoq
mom
unknown
```

不得将增长率和同期比率混为一谈。

例如：

```text
毛利率 = 毛利润 / 营业收入
```

不等于：

```text
营业收入同比增长率
```

## 6.3 Unit Semantics

不能继续只依赖裸字符串：

```python
unit = "%"
unit = "x"
currency = "CNY"
```

至少明确区分：

- statistic_kind
- numeric_unit
- currency
- unit_denominator
- output_unit
- scale

示例：

```text
Revenue:
  statistic_kind = absolute_amount
  currency = CNY
  numeric_unit = CNY

EPS:
  statistic_kind = per_share
  currency = USD
  unit_denominator = share

Gross Margin:
  statistic_kind = percent_ratio
  output_unit = percent

Current Ratio:
  statistic_kind = times_ratio
  output_unit = times
```

必须保证：

- 25% 不会被解释为 25 倍。
- 25 倍不会被解释为 25%。
- USD/share 不会被误认为 CNY/share。
- Decimal 不会在兼容转换中意外损失精度。

## 6.4 Filing / Accession

必须明确区分：

- filing_date
- report_period
- accession_number
- source_document_id
- amendment / restatement
- data_version

不能将 filing_date 当成报告期间。

不能将 accession_number 当成通用 Entity ID。

不能因为相同报告期间而覆盖不同版本的报告。

## 6.5 兼容策略

旧固定字段模型必须保留兼容层，以供 V9 测试过渡。

要求：

1. 明确 Legacy Model 与 Canonical Model 的边界。
2. 明确转换入口。
3. 明确兼容字段的优先级。
4. 避免两个模型都成为权威数据源。
5. 旧模型不得绕过 Canonical Models。
6. 对无法无损转换的旧数据给出 warning 或显式错误。
7. 不允许静默丢失 accession、period、currency 等元数据。

如果旧 API 必须暂时保留，使用明确的兼容适配器，不要无限制增加 property alias。

## 6.6 测试

新增或更新：

```text
tests/test_financial_models.py
```

至少覆盖：

- Entity Identity。
- 不同市场的 Entity。
- FinancialStatement。
- instant / duration period。
- FinancialLineItem。
- Decimal 精度。
- 货币语义。
- 每股指标。
- 百分比与倍数区分。
- Filing / Accession。
- 不同版本报告。
- Legacy → Canonical。
- Canonical → Legacy（仅支持无损场景）。
- 不支持转换的显式拒绝。
- JSON / 序列化兼容。

## 6.7 验收

必须满足：

- 新模型具备稳定结构。
- 旧调用方没有无解释破坏。
- Decimal 作为权威财务数值。
- 身份键语义明确。
- 所有新模型测试通过。
- Financial Pack 与 Macro Pack 领域隔离。

提交：

```text
refactor(financial): introduce canonical financial models
```

推送成功后进入 B10.2.3。

---

# 7. B10.2.3：Financial Normalization

本阶段建立统一标准化模块。

建议位置：

```text
langgraph_langchain/runtime/financial/normalization.py
```

实际位置应遵循仓库现有包结构。

## 7.1 职责边界

Normalization 负责：

- 数值单位规范化。
- 币种标识规范化。
- 统计口径规范化。
- 财务概念映射。
- 时间语义规范化。
- FinancialLineItem 标准化。
- 标准化血缘记录。

Normalization 不负责：

- 生成财务分析结论。
- 实现完整财务指标计算。
- 自动预测汇率。
- 自动获取外部汇率。
- 自动推断未知财务语义。
- 替代 AcquisitionService。
- 建立独立 DataAsset 存储。

财务指标计算应留给后续 Financial Metrics 阶段。

## 7.2 单一显式转换出口

必须设计一个统一入口，例如：

```python
normalize_financial_statement(...)
```

或：

```python
normalize_financial_line_item(...)
```

具体名称遵循当前项目接口。

不得让每个 Adapter、每个指标计算模块各自实现一套隐式转换。

统一转换必须返回结构化结果，而不是只返回一个 Decimal。

建议包含：

```text
input_value
output_value
input_statistic_kind
output_statistic_kind
input_unit
output_unit
input_currency
output_currency
conversion_method
normalization_version
warnings
source_metadata
```

如果有多个 LineItem，必须能够定位每一条的转换来源。

## 7.3 旧数据白名单推导

参考 B9.8.3 的模式：

- 显式 statistic_kind 优先。
- 旧模型允许通过白名单推导。
- 不允许根据模糊 unit 字符串自动猜测。
- 无法确定的语义为 unknown。
- unknown 在需要语义转换时必须拒绝。

兼容规则必须可测试、可审计。

不要把所有历史数据默认标记为 absolute_amount。

## 7.4 防止“值 − 100”语义错误

必须明确区分：

```text
yoy_index
growth_rate
percent_ratio
```

例如：

```text
100.2 的同比指数
```

只有明确声明 `yoy_index` 且基准为 100 时，才允许：

```python
growth_rate = value - 100
```

以下数据不得自动减 100：

- 100.2% 的毛利率。
- 100.2 倍的比率。
- 100.2 的普通金额。
- 已经是同比百分比的 100.2。

对 yoy_index 必须明确基准语义。

如果基准不是 100，必须根据明确的定义转换或拒绝。

## 7.5 财务比率与增长率

必须保持：

```text
percent_ratio != growth_rate
```

毛利率、ROE、资产负债率等属于比率类指标。

营业收入同比、净利润同比等属于增长率。

不能因两者单位都是 percent 就认为统计语义相同。

## 7.6 Currency

本阶段必须支持明确识别和保留币种。

如果项目当前已有 CNY 单位换算逻辑，应检查并整合到统一标准化入口。

币种兑换必须与数量级单位转换分开。

例如：

```text
10000 万 CNY
```

与：

```text
10000 USD
```

不能仅凭单位归一规则转换成相同货币金额。

如果本阶段没有可靠汇率来源：

- 不实现自动汇率转换。
- 不伪造汇率。
- 不默认采用当前汇率。
- 不将不同币种数据标记为同币种可比。
- 记录无法比较的原因。

如果后续确需支持汇率换算，必须显式提供：

- source_currency
- target_currency
- rate
- rate_date
- rate_source
- direction
- rate_version
- conversion_method

本阶段只建立必要的模型和接口能力，不擅自扩展成完整 FX 服务。

## 7.7 Period Semantics

必须区分：

- instant。
- duration。
- period_start。
- period_end。
- fiscal_year。
- fiscal_period。
- comparison_basis。

不能只使用自然年替代财务报告期间。

对于增长率计算，Normalization 只负责提供正确的标准化期间语义和可追溯输入。

不要在本阶段把所有同比、环比财务指标计算逻辑塞入 Normalization。

## 7.8 标准化血缘

每次转换必须记录：

- 输入原始值。
- 输入单位。
- 输入币种。
- 输入统计口径。
- 输出值。
- 输出单位。
- 输出币种。
- 输出统计口径。
- 转换方法。
- 转换版本。
- warning。
- 原始来源标识。
- filing/accession。
- 输入数据版本。

建议设置独立版本：

```text
financial_normalization_version
```

不能使用 B9 的转换版本作为 Financial 的版本。

## 7.9 不可变性

标准化不得原位修改原始输入。

必须验证：

- 输入对象不变。
- 原始 Decimal 精度保留。
- NaN/Infinity 等异常输入按明确规则拒绝。
- 缺失值不被伪造成 0。
- 无法转换的数据保留明确错误信息。
- 转换过程可以重复执行且结果确定。

## 7.10 测试

新增或更新：

```text
tests/test_financial_normalization.py
```

至少覆盖：

### 数值和单位

- CNY 元、千、万、百万、亿。
- Decimal 精度。
- 负数。
- 零。
- 缺失值。
- 非法数值。
- 不支持单位。

### 统计口径

- absolute_amount。
- per_share。
- percent_ratio。
- times_ratio。
- growth_rate。
- yoy_index。
- unknown。

### 语义防错

- yoy_index=100 → growth_rate=0。
- yoy_index=100.2 → growth_rate=0.2。
- yoy_index=99.8 → growth_rate=-0.2。
- growth_rate=2.5 → 保持 2.5，不再减 100。
- percent_ratio=25 → 保持 25，不减 100。
- times_ratio=25 → 保持 25，不转换成百分比。
- unknown → 显式拒绝需要语义转换的操作。

### 币种

- CNY 保留。
- USD 保留。
- 未提供汇率时不进行 CNY/USD 自动换算。
- 不同币种不能被误判为同币种。

### 时间语义

- instant。
- duration。
- 不同 fiscal period。
- 不同报告期间。
- 缺失期间。
- 不完整期间。

### 血缘

- conversion_method。
- normalization_version。
- source metadata。
- filing/accession。
- 输入输出语义。
- 原始输入不可变。

## 7.11 验收

必须满足：

- 只有一个明确的标准化入口。
- 未知语义不会被猜测。
- 单位与统计口径分离。
- 货币转换不被数量级转换替代。
- 转换结果包含血缘。
- 原始数据不可变。
- 测试覆盖关键语义边界。

提交：

```text
feat(financial): add explicit financial normalization
```

推送成功后进入 B10.2.4。

---

# 8. B10.2.4：Legacy Compatibility

本阶段目标是让现有 Financial V9 测试和旧调用方可以逐步迁移到新模型。

## 8.1 任务

全面搜索：

```text
FinancialStatement
FinancialIndicator
FinancialMetrics
Company
FinancialData
FinancialAgent
```

检查：

- 旧字段使用位置。
- 旧构造函数。
- 旧序列化结构。
- 旧指标接口。
- 旧测试夹具。
- 旧 Runtime 调用路径。

建立：

```text
Legacy Financial Model
    ↓
Compatibility Adapter
    ↓
Canonical Financial Model
    ↓
Normalization
```

禁止：

```text
Legacy Model
    ↓
绕过 Canonical Model
    ↓
直接进入新 Runtime
```

## 8.2 兼容原则

- 旧字段能无损映射时自动映射。
- 旧字段不能无损映射时显式 warning。
- 缺少关键身份信息时拒绝注册。
- 不能将缺失币种默认为真实已确认的 CNY。
- 不能将缺少期间的数据伪装成完整年度报表。
- 不允许兼容层自行创建另一套资产存储。
- 不允许兼容层自行注册第二套 Evidence。

兼容层必须明确标记 deprecated 的字段或接口。

不要在本阶段提前删除全部旧 API。

## 8.3 测试

验证：

1. V9 既有测试兼容。
2. 旧模型能映射到 Canonical Model。
3. Decimal 精度不丢失。
4. Filing/accession 信息不丢失。
5. 期间语义不丢失。
6. 货币语义不丢失。
7. 无法无损映射时显式失败。
8. 不绕过 Platform 注册路径。

提交：

```text
feat(financial): add legacy model compatibility adapters
```

推送成功后进入 B10.2.5。

---

# 9. B10.2.5：集成验收与阶段冻结

这是最终阶段。

## 9.1 架构边界审计

检查：

### Financial Pack 隔离

```bash
grep -R "runtime.macro" langgraph_langchain/runtime/financial
grep -R "MacroStatisticKind" langgraph_langchain/runtime/financial
```

根据真实包路径调整。

必须保证 Financial Pack 没有直接依赖 Macro Pack 内部实现。

### Platform 边界

检查 Financial Pack 是否：

- 使用既有 Platform DataAsset。
- 使用快照化注册路径。
- 使用既有 AssetResolver。
- 没有平行 DataAsset 存储。
- 没有绕过 B8 的新读取通道。

### Evidence 边界

检查：

- 没有建立第二套权威 EvidenceRegistry。
- FinancialEvidence 仅作为计算明细或辅助信息。
- 原始 filing/accession 元数据保留。
- 后续 B10.6 能够将 Evidence 关联到源资产。

注意：B10.2 不要求提前完成 B10.6 的完整 Evidence Integration。

## 9.2 测试回归

重新运行：

```bash
pytest tests/financial -q
pytest tests/financial_agent -q
pytest tests/test_financial_pack_contract.py -q
pytest tests/test_financial_models.py -q
pytest tests/test_financial_normalization.py -q
```

实际目录或命令有变化时使用真实路径。

另外运行：

- 与 Financial 相关的 Runtime 测试。
- Pack Registration 测试。
- DataAsset / SnapshotStore 注册相关测试。
- Macro Pack 关键回归测试。
- B8 相关回归测试（如共享代码被修改）。
- 全量测试或项目规定的完整回归。

不能只运行新增测试。

## 9.3 基线比较

将最终结果与 B10.2.1 基线比较。

要求输出：

| 指标 | 基线 | 最终 | 差异 |
|---|---:|---:|---:|
| Passed | | | |
| Failed | | | |
| Skipped | | | |
| XFailed | | | |

对失败 nodeid 做逐项 diff。

必须区分：

- 原有失败。
- 新增失败。
- 已修复失败。
- 环境性失败。
- Flaky 测试。

新增失败必须处理，不能仅通过文字解释。

## 9.4 静态检查

按项目现有配置执行：

- Ruff / Flake8（以项目实际工具为准）。
- Mypy / Pyright（如果项目已配置）。
- import 检查。
- circular dependency 检查。
- `git diff --check`。

不要为了通过静态检查而大规模修改无关模块。

## 9.5 最终验收文档

新增：

```text
docs/architecture/reviews/b10.2-final-acceptance.md
```

必须包含：

1. 总体结论。
2. 实际 Git 分支和 HEAD。
3. 各阶段 commit SHA。
4. B10.2.0 Contract 实施结果。
5. B10.2.1 测试基线。
6. Financial Identity 设计。
7. Financial Models 实施结果。
8. Decimal 精度策略。
9. StatisticKind 设计。
10. Unit Semantics 设计。
11. Normalization 统一出口。
12. Legacy Compatibility。
13. Platform DataAsset 边界。
14. Evidence 边界。
15. 测试执行命令与真实结果。
16. 基线逐项对比。
17. 未解决问题。
18. P0/P1/P2 分类。
19. B10.2 冻结判定。
20. B10.3 解锁条件（仅按 toDo.md 原始定义）。

同时更新 `docs/newMain/toDo.md`：

- 只更新 B10.2 对应章节。
- 记录实际完成的子阶段。
- 记录真实 commit SHA。
- 记录测试结果。
- 记录未解决问题。
- 只有全部门禁通过才能标记 B10.2 完成。
- 不改写其他阶段的历史内容。
- 不删除已有用户注释或记录。

## 9.6 最终提交

提交：

```text
docs(financial): finalize B10.2 acceptance
```

推送并验证远端同步。

---

# 10. 明确禁止的范围扩张

B10.2 不得实施以下内容：

1. 修改 B9 已冻结的 Macro 生产代码。
2. 修改 Macro Catalog。
3. 扩展 MacroStatisticKind。
4. 完整实现中国财报 Adapter。
5. 完整实现美国 SEC Adapter。
6. 实现完整 FX 汇率服务。
7. 实现完整 Financial Metrics 引擎。
8. 实现完整 Financial Agent。
9. 实现 B10.3–B10.7。
10. 建立第二套 DataAsset。
11. 建立第二套 EvidenceRegistry。
12. 建立独立于 Platform 的数据读取通道。
13. 提前实施 canonical evidence 持久化专项。
14. 将 B9 遗留 P2 混入 B10.2。
15. 修改与本任务无关的 Runtime、Dashboard、Tracing 或用户脚本。
16. 修改或删除用户工作区内容。

发现上述能力存在必要依赖时：

- 先记录依赖。
- 说明是否阻塞 B10.2。
- 如确实需要超出范围的改动，停止并报告，不得擅自扩大范围。

---

# 11. 自动执行与阶段门禁规则

必须按以下顺序自动推进：

```text
B10.2.0
    ↓
Contract Gate
    ↓
B10.2.1
    ↓
Identity + Baseline Gate
    ↓
B10.2.2
    ↓
Models Gate
    ↓
B10.2.3
    ↓
Normalization Gate
    ↓
B10.2.4
    ↓
Compatibility Gate
    ↓
B10.2.5
    ↓
Final Acceptance
```

每个阶段都必须执行：

```text
检查
  ↓
实施
  ↓
测试
  ↓
静态检查
  ↓
Review Diff
  ↓
独立 Commit
  ↓
Push
  ↓
验证远端 SHA
  ↓
进入下一阶段
```

## 11.1 自动继续

不需要用户逐阶段确认。

当阶段通过：

- 自动进入下一阶段。
- 不需要等待用户回复。
- 不要只生成下一阶段计划而不执行。

## 11.2 停止条件

出现以下任意情况，立即停止后续阶段：

- Git 分支或远端状态无法安全处理。
- 用户工作区文件可能被覆盖。
- B10.1 契约存在未解决的 P0/P1 冲突。
- Pack Contract 无法满足隔离约束。
- Financial Identity 无法与 Platform DataAsset 正确映射。
- 模型重构导致无法解释的历史兼容性破坏。
- 新增测试失败。
- 新增 Runtime 回归失败。
- Normalization 不能保证语义明确。
- Evidence/DataAsset 边界被绕过。
- 需要修改 B9 冻结代码才能继续。
- 需要超出 B10.2 的范围才能完成当前阶段。

停止时：

1. 保留已经完成的安全修改。
2. 不提交未通过门禁的阶段。
3. 不推送未通过门禁的代码。
4. 生成失败审计文档。
5. 报告阻塞原因和最小解决方案。

## 11.3 阶段提交策略

一个阶段可以有多个逻辑提交，但原则上每个阶段至少有一个清晰的独立提交。

纯审计阶段如果没有任何代码或文档变化，不制造空提交。

提交信息采用：

```text
feat(financial): ...
refactor(financial): ...
test(financial): ...
docs(financial): ...
```

提交内容必须与阶段目标一致。

---

# 12. 最终完成门禁

只有全部满足以下条件，才能宣布 B10.2 完成：

- [ ] B10.2.0 Pack Contract 与注册机制完成。
- [ ] B10.2.1 基线建立并保存。
- [ ] Financial Asset Identity 已冻结。
- [ ] Financial Models 已重构。
- [ ] Decimal 为权威财务数值类型。
- [ ] Entity Identity 明确。
- [ ] FinancialStatement 区分 instant / duration。
- [ ] Filing / Accession / Version 语义明确。
- [ ] FinancialStatisticKind 独立存在。
- [ ] Unit Semantics 独立存在。
- [ ] Normalization 有统一显式出口。
- [ ] unknown 语义不会被猜测。
- [ ] yoy_index 不会被错误地当成 yoy_rate。
- [ ] percent_ratio 与 growth_rate 明确区分。
- [ ] 不同币种不会被隐式合并。
- [ ] 转换结果具备完整血缘。
- [ ] 输入原始数据不可变。
- [ ] Legacy Compatibility 通过。
- [ ] 不依赖 Macro Pack 内部实现。
- [ ] 不建立平行 DataAsset。
- [ ] 不建立第二套 EvidenceRegistry。
- [ ] 不新增绕过 B8 的数据通道。
- [ ] 关键测试通过。
- [ ] 全量回归没有新增未解释失败。
- [ ] 每个阶段提交独立。
- [ ] 所有阶段提交已成功推送。
- [ ] 验收文档完成。
- [ ] toDo.md 状态更新正确。
- [ ] 用户工作区原样保留。

如果任何关键条件不满足：

```text
B10.2 = NOT FROZEN
B10.3 = NOT UNLOCKED
```

不得提前解锁 B10.3。

---

# 13. 最终汇报格式

完成或停止时，必须按以下格式汇报。

## 1. 总体结论

- B10.2 是否完成。
- 是否达到冻结门禁。
- B10.3 是否解锁。
- 本轮是否存在范围外改动。

## 2. Git 状态

- 分支。
- 初始 HEAD。
- 最终 HEAD。
- 本地与远端是否同步。
- 每个阶段 Commit SHA。
- 用户工作区是否原样保留。

## 3. 各阶段实施情况

| 阶段 | 实施结果 | 测试结果 | Commit |
|---|---|---|---|
| B10.2.0 | | | |
| B10.2.1 | | | |
| B10.2.2 | | | |
| B10.2.3 | | | |
| B10.2.4 | | | |
| B10.2.5 | | | |

## 4. Financial Models

说明：

- Entity。
- FinancialStatement。
- FinancialLineItem。
- Decimal。
- Filing/Accession。
- Identity。
- Legacy Compatibility。

## 5. Normalization

说明：

- FinancialStatisticKind。
- Unit Semantics。
- Currency。
- Period Semantics。
- 统一转换出口。
- 转换版本。
- 转换血缘。
- 未知语义处理。

## 6. Platform / Evidence 边界

说明：

- DataAsset 注册路径。
- SnapshotStore。
- AssetResolver。
- B8 边界。
- EvidenceRegistry。
- 是否存在平行实现。

## 7. 测试结果

分别列出：

- 新增测试。
- Financial 原有测试。
- Financial Agent 测试。
- Pack Contract 测试。
- Runtime 相关测试。
- Macro 回归。
- B8 回归。
- 全量回归。

必须提供真实测试结果，不得估算或伪造。

## 8. 基线比较

列出：

- Passed。
- Failed。
- Skipped。
- XFailed。
- 新增失败。
- 修复失败。
- 既有失败。

## 9. 遗留问题

按：

- P0
- P1
- P2
- P3

分类。

每项说明：

- 具体问题。
- 影响。
- 是否阻塞。
- 建议解决阶段。

## 10. 最终门禁

明确：

```text
B10.2: FROZEN / NOT FROZEN
B10.3: UNLOCKED / NOT UNLOCKED
```

---

# 14. 最终执行指令

现在开始执行。

第一步先检查 Git 状态、读取权威文档和实际代码。

然后按 B10.2.0 → B10.2.5 自动推进。

不要只输出分析报告。

不要等待用户逐阶段确认。

不要提前实施 B10.3。

不要修改 B9 冻结代码。

不要覆盖用户工作区。

**所有阶段必须以真实测试、明确门禁、独立提交和远端同步作为完成依据。**

如果发现原始任务定义与真实代码冲突，以 `toDo.md` 和 B10.1 冻结架构契约为准；如契约自身存在矛盾，先记录并停止相关阶段，不得擅自选择一种解释继续实现。

最终目标不是尽可能多地修改代码，而是交付一个可以被后续 Financial Adapter、Financial Metrics、Runtime、Evidence 和 Golden Evaluation 安全依赖的金融数据基础层。
# Runtime V9.3 Real Financial Data 阶段实施验收

## 1. 结论

- 版本：Runtime V9.3 Phase 1 / Period + Unit + Fact + Formula Golden
- 日期：2026-09-24
- 结论：**当前阶段通过**
- 分支：`master`
- 基线提交：`18a54e1 test(runtime-v9): preserve calculation input lineage`

本阶段按 V9.3 设计文档完成 Phase 1、Phase 2、Phase 3，以及 Formula Golden Runner / Mutation Verification。真实财报数据接入（Phase 4）尚未完成，不能宣称 V9.3 整体完成。

## 2. 已完成能力

### 2.1 Period Normalization

新增：

```text
langgraph_langchain/runtime/financial/period.py
```

支持：

```text
2025
2025年度
FY2025
2025Q1 / 2025-Q1
2025Q2 / 2025-Q2
2025Q3 / 2025-Q3
2025Q4 / 2025-Q4
2025H1 / 2025-H1
2025H2 / 2025-H2
```

能力：

- `FinancialPeriod`
- `PeriodType`
- `PeriodNormalizer.parse()`
- `normalize()`
- `order_key()`
- `previous()`
- FY / 季度 / 半年稳定排序
- 非法期间拒绝
- TTM / LTM 明确不支持

`FinancialDataService` 已接入 Period API：

- `periods_for()` 使用 `PeriodNormalizer.order_key()` 排序
- `previous_period()` 使用规范化前一期
- 缺失前一期返回 `None`
- 不再依赖原始字符串排序

### 2.2 Unit Normalization

新增：

```text
langgraph_langchain/runtime/financial/unit.py
```

支持：

```text
CNY / RMB / 元
CNY_THOUSAND / 千元
WAN_YUAN / 万元 / CNY_TEN_THOUSAND
MILLION / CNY_MILLION / 百万元
BILLION / CNY_BILLION / 亿元
```

输出：

```text
normalized_value
normalized_unit
conversion_rule
status
```

状态：

- `calculated`
- `unavailable`
- `invalid`

当前内部统一为 `CNY`。

### 2.3 Financial Fact

新增：

```text
langgraph_langchain/runtime/financial/fact.py
```

新增模型：

```text
FinancialFact
FinancialFactBuilder
```

Fact 保留：

- `company_id`
- `period`
- `metric_id`
- `value`
- `unit`
- `source_id`
- `source_label`
- `original_period`
- `original_metric_name`
- `original_value`
- `original_unit`
- `conversion_rule`
- `normalized_unit`
- `status`

形成：

```text
FinancialDataSource
  ↓
FinancialFact
  ↓
Normalized Statement
  ↓
MetricEngine
```

### 2.4 Formula Golden Runner

新增：

```text
langgraph_langchain/runtime/financial/golden.py
tests/financial/formula_golden/core_metrics.json
```

核心类型：

- `FormulaGoldenCase`
- `FormulaGoldenResult`
- `FormulaGoldenSummary`
- `FormulaGoldenLoader`
- `FormulaGoldenRunner`

当前 Formula Golden Case 数量：**23**。

覆盖：

- reported 基础指标
- growth 同比指标
- profitability 指标
- solvency 指标
- operating turnover 指标
- cashflow 指标

每个 Case 包含：

- `case_id`
- `metric_id`
- `company_id`
- `period`
- `inputs`
- `expected`
- `unit`
- `formula`
- `formula_version`
- `tolerance`

### 2.5 Formula Golden 校验语义

Runner 同时校验：

1. MetricEngine 状态必须为 `calculated`
2. Actual Value 必须存在
3. Expected Value 必须存在
4. `Calculation.inputs` 必须与 Formula Golden `inputs` 一致
5. Actual 与 Expected 差异必须小于绝对 tolerance
6. Formula Version 必须存在

容差采用绝对容差，不使用相对容差放大金额类误差。

### 2.6 Mutation Verification

专项测试验证四类错误均可捕获：

| Mutation | 结果 |
| --- | --- |
| ROE 改为 ending equity | ✅ FAIL |
| 指标结果人为加 1 | ✅ FAIL |
| Calculation inputs 被篡改 | ✅ FAIL |
| Expected / Actual 不一致 | ✅ FAIL |

## 3. 测试结果

### 3.1 V9 专项测试

```bash
python -X utf8 -m pytest tests/financial -q
```

结果：

```text
57 passed
```

### 3.2 本地全量回归

```bash
python -X utf8 -m pytest -q
```

结果：

```text
735 passed, 0 failed
```

## 4. 当前阶段验收检查表

| 项目 | 状态 |
| --- | --- |
| Period Normalization | ✅ |
| Period 稳定排序 | ✅ |
| Period previous 语义 | ✅ |
| 非法 Period 拒绝 | ✅ |
| Unit Normalization | ✅ |
| Unit conversion provenance | ✅ |
| FinancialFact 层 | ✅ |
| Fact 保留 original period / metric / value | ✅ |
| Formula Golden Loader | ✅ |
| Formula Golden Runner | ✅ |
| Formula Golden 数量 ≥ 15 | ✅ 23 |
| Formula / Formula Version | ✅ |
| Calculation inputs 校验 | ✅ |
| Mutation Verification | ✅ |
| 全量回归 | ✅ |
| 真实财报数据接入 | ⏳ Phase 4 未完成 |
| 真实 Formula Golden Expected Value | ⏳ Phase 4 后完成 |

## 5. 明确未完成项

以下内容不属于本阶段完成范围：

1. 3 家真实上市公司 2021–2025 年财报数据接入
2. 真实年报 / 巨潮 / 交易所来源 Raw File 与 SHA256
3. 真实公司 Formula Golden Expected Value
4. 真实 Business Golden Case
5. TTM / LTM
6. XBRL / PDF OCR
7. 自动联网抓取
8. Skill Evolution / GEPA

## 6. 下一步建议

进入 V9.3 Phase 4：

```text
选择 3 家真实公司
  ↓
收集 2021–2025 年报数据
  ↓
保存 Raw Source + SHA256
  ↓
建立 Parsed Fact
  ↓
生成 Normalized Statement
  ↓
补充真实 Formula Golden
  ↓
运行完整 Formula / Business Golden
```

建议首批公司：

```text
贵州茅台
五粮液
泸州老窖
```

建议首批指标：

```text
revenue
revenue_growth
net_profit
net_profit_growth
net_margin
roe
roa
debt_to_asset
operating_cash_flow
ocf_to_net_income
free_cash_flow
```

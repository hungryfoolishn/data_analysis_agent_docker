# Runtime V9.3 Phase 4 Real Data Loader 实施验收

## 结论

V9.3 Phase 4 已完成真实财务数据接入的最小可信闭环：

```text
Eastmoney F10 API Snapshot
    ↓
Raw Source JSON + Source SHA256
    ↓
Parsed Financial Facts + Facts SHA256
    ↓
Period Normalization
    ↓
Unit Normalization
    ↓
Normalized Statement
    ↓
Formula Golden
    ↓
Metric Engine Regression
```

本阶段只解决真实数据的可加载、可校验、可追溯与核心公式可验证问题。不进入估值、实时行情、Skill Evolution、GEPA 或 V10。

## 交付范围

### 1. 真实数据集

新增目录：

```text
tests/financial/real_data/
```

数据范围：

| 项目 | 值 |
| --- | --- |
| 公司 | 贵州茅台、五粮液、泸州老窖 |
| 公司代码 | 600519、000858、000568 |
| 期间 | 2021、2022、2023、2024、2025 年报 |
| Source 数量 | 15 |
| Parsed Fact 数量 | 273 |
| Formula Golden Case 数量 | 153 |

数据集版本：

```text
financial-real-v1
```

### 2. Raw Source 与 Parsed Fact 分离

每个 Source 均包含：

```text
raw_file
facts_file
source_hash
facts_file_hash
document_url
published_at
```

Raw Source 保存 Eastmoney F10 API 快照，Parsed Facts 保存从 Raw Source 中提取出的标准化字段。

哈希使用：

```text
sha256-canonical-text
```

避免 Windows / Linux 换行符差异导致跨平台校验失败。

### 3. 期间与单位标准化

Loader 使用既有 V9.3 能力完成：

```text
原始期间          标准期间
2021年报    →    2021
2022年报    →    2022
2023年报    →    2023
2024年报    →    2024
2025年报    →    2025
```

金额统一为：

```text
CNY
```

并保留：

```text
original_period
original_metric_name
original_value
original_unit
source_label
conversion_rule
```

### 4. 真实 Formula Golden

新增：

```text
scripts/generate_real_financial_golden.py
tests/financial/real_data/formula_golden/core_metrics.json
```

覆盖以下指标：

```text
revenue
net_profit
operating_cash_flow
free_cash_flow
net_margin
debt_to_asset
ocf_to_net_income
revenue_growth
net_profit_growth
roa
roe
```

Formula Golden expected value 由生成脚本基于已校验事实独立计算，不调用 `MetricEngine`。测试运行时再由 `FormulaGoldenRunner` 比较独立 expected value 与 MetricEngine 实际输出。

## 数据来源

数据来源为东方财富 F10 年报财务数据接口快照。

Manifest 中保留：

```text
provider = Eastmoney Data Center
retrieved_at = 2026-09-24T12:08:24+08:00
source_type = annual_report_api_snapshot
document_url
raw_file
source_hash
```

Raw Source JSON 中进一步保留三张报表的实际 API 请求 URL 与 response。

说明：`document_url` 指向公司 F10 财务页面，`raw_file.source_urls` 保存利润表、资产负债表、现金流量表 API 的精确请求 URL。

## 核心校验

Loader 执行以下校验：

1. Raw Source 文件存在。
2. Raw Source SHA256 匹配。
3. Facts 文件存在。
4. Facts SHA256 匹配。
5. 公司、期间、指标键不重复。
6. 期间可标准化。
7. 单位可标准化。
8. 必需字段缺失时不伪造为 0。
9. 3 家公司与 5 个期间覆盖完整。
10. 标准化 Statement 可构建。

## 测试验收

### Loader / Real Data 测试

命令：

```bash
PYTHONPATH=. pytest tests/financial/test_real_financial_data.py -q
```

结果：

```text
9 passed
```

### Financial 全量测试

命令：

```bash
PYTHONPATH=. pytest tests/financial -q
```

结果：

```text
66 passed
```

### 全量回归测试

命令：

```bash
PYTHONPATH=. pytest -q
```

结果：

```text
744 passed, 26 warnings, 4 subtests passed
```

### Formula Golden 验收

真实 Formula Golden：

```text
153 / 153 passed
pass_rate = 1.0
failed_cases = 0
```

并覆盖以下 mutation 检测：

1. 数值结果被篡改时失败。
2. ROE 使用期末权益替代平均权益时失败。

## 关键字段映射

### 利润表

| Runtime Metric | API Field |
| --- | --- |
| revenue | TOTAL_OPERATE_INCOME |
| cost_of_revenue | OPERATE_COST |
| operating_profit | OPERATE_PROFIT |
| net_profit | NETPROFIT |
| net_profit_attributable | PARENT_NETPROFIT |

### 资产负债表

| Runtime Metric | API Field |
| --- | --- |
| total_assets | TOTAL_ASSETS |
| total_liabilities | TOTAL_LIABILITIES |
| total_equity | TOTAL_EQUITY |
| cash | MONETARYFUNDS |
| accounts_receivable | ACCOUNTS_RECE |
| inventory | INVENTORY |
| fixed_assets | FIXED_ASSET |
| short_term_debt | SHORT_LOAN |
| long_term_debt | LONG_LOAN |
| current_assets | TOTAL_CURRENT_ASSETS |
| current_liabilities | TOTAL_CURRENT_LIAB |

### 现金流量表

| Runtime Metric | API Field |
| --- | --- |
| operating_cash_flow | NETCASH_OPERATE |
| investing_cash_flow | NETCASH_INVEST |
| financing_cash_flow | NETCASH_FINANCE |
| capital_expenditure | CONSTRUCT_LONG_ASSET |

## 抽样数据核对

贵州茅台 2025 年报：

| 字段 | 值 |
| --- | ---: |
| revenue | 172,054,171,890.91 CNY |
| net_profit | 85,310,324,833.67 CNY |
| total_assets | 303,834,844,021.44 CNY |
| total_equity | 253,959,253,909.07 CNY |
| operating_cash_flow | 61,522,204,989.35 CNY |

## 边界与不做事项

本阶段明确不做：

```text
❌ 估值 / DCF / PE / PB
❌ 实时行情
❌ 自动爬取增量数据
❌ PDF OCR
❌ XBRL 全量解析
❌ V10
❌ Skill Evolution
❌ GEPA
❌ 自动投资建议
```

## 验收结论

V9.3 Phase 4 Real Data Loader 满足以下验收条件：

1. 真实公司数据可加载。
2. 3 家公司 × 5 个年度完整覆盖。
3. Raw Source 与 Parsed Fact 分离。
4. Raw Source 与 Facts 均有跨平台 SHA256 校验。
5. 期间与单位标准化正确。
6. 真实 Formula Golden 独立于 MetricEngine。
7. 153 条 Formula Golden 全部通过。
8. 公式错误可被 mutation test 捕获。

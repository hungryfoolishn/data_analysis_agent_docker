# Runtime V9.3 Phase 4 Real Financial Data Loader 实施验收

## 1. 结论

- 版本：Runtime V9.3 Phase 4 / Real Financial Data Loader Framework
- 日期：2026-09-24
- 结论：**当前阶段通过**
- 分支：`master`
- 基线提交：`c3ec860 feat(runtime-v9): add financial period, fact and formula golden`

本阶段完成真实财报数据接入框架。它验证：

```text
Manifest
  ↓
Raw Source File
  ↓
SHA256
  ↓
Parsed Financial Fact
  ↓
Period Normalization
  ↓
Unit Normalization
  ↓
Normalized Statement
  ↓
FinancialDataService
```

需要注意：本阶段交付的是可校验加载框架和一致性测试；尚未提交真实的 3 家公司 × 5 年财报数据，因此不能宣称 V9.3 全部完成。

## 2. 新增能力

### 2.1 Manifest 与 Raw Source

新增：

```text
langgraph_langchain/runtime/financial/real_data.py
```

支持 Manifest 字段：

- `dataset_version`
- `companies`
- `periods`
- `sources`

每个 Source 支持：

- `source_id`
- `source_type`
- `company_id`
- `report_period`
- `document_name`
- `document_url`
- `raw_file`
- `source_hash`

加载器会校验：

- 原始文件存在
- 原始文件 SHA256 与 Manifest 一致
- Source company / period 合法
- Fact company 与 Source company 一致
- Fact source 与 Source ID 一致
- Metric 属于支持的报表字段
- 数值单位可标准化
- 无重复 company / period / metric
- 至少 3 家公司
- 至少 5 个期间
- 每个 company / period 都有数据源覆盖

### 2.2 Fact 生成

从 Raw Source 生成：

```text
FinancialFact
```

Fact 保留：

- `fact_id`
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

### 2.3 Normalized Statement

Loader 将 Fact 归组为：

- `IncomeStatement`
- `BalanceSheetStatement`
- `CashFlowStatement`

并构建：

```text
FinancialDataService
```

缺失字段保持 `None`，不会伪造为 `0`。

### 2.4 Period 语义

真实数据加载过程复用：

```text
PeriodNormalizer
```

例如：

```text
2025年度
  ↓
2025 / FY
```

数据服务的期间排序和 previous period 不再依赖原始字符串排序。

## 3. 测试结果

### 3.1 真实数据加载器专项测试

```bash
python -X utf8 -m pytest tests/financial/test_real_financial_data.py -q
```

覆盖：

- 3 家公司 × 5 个期间
- 15 个 Raw Source
- Raw Source SHA256 校验
- FinancialFact 生成
- original period 保留
- normalized period 标准化
- normalized statement 构建
- previous period 语义
- Source hash mismatch 拒绝
- 公司数 / 期间数不足校验

结果：

```text
3 passed
```

### 3.2 V9 专项测试

```bash
python -X utf8 -m pytest tests/financial -q
```

结果：

```text
60 passed
```

### 3.3 本地全量回归

```bash
python -X utf8 -m pytest -q
```

结果：

```text
738 passed, 0 failed
```

## 4. 验收检查表

| 项目 | 状态 |
| --- | --- |
| Real Financial Manifest 加载 | ✅ |
| Raw Source SHA256 校验 | ✅ |
| FinancialFact 生成 | ✅ |
| 期间标准化 | ✅ |
| 单位标准化 | ✅ |
| Normalized Statement 构建 | ✅ |
| 缺失字段不伪造为 0 | ✅ |
| 3 家公司 × 5 年结构化加载测试 | ✅ |
| Source hash mismatch 拒绝 | ✅ |
| 本地全量回归 | ✅ |
| 真实公司真实财报数据提交 | ⏳ 未完成 |
| 真实数据 Formula Golden Expected Value | ⏳ 未完成 |
| 真实 Business Golden Case | ⏳ 未完成 |

## 5. 下一阶段

下一步应收集并提交真实财报数据：

1. 贵州茅台
2. 五粮液
3. 泸州老窖

期间：

```text
2021
2022
2023
2024
2025
```

必须保留：

```text
Raw Source File
  +
SHA256
  +
document URL
  +
Published At
  +
Parsed Fact
```

然后再生成真实 Formula Golden Expected Value 与 Business Golden Case。

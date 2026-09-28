# Runtime V9.3 Period Hardening 实施验收

## 结论

V9.3 Period Hardening 已完成。本次修复解决了 `PeriodNormalizer` 定义能力与 `FinancialDataService`、`FinancialAnalysisWorkflow` 端到端执行不一致的问题，不新增估值、实时行情、Skill Evolution 或 V10 功能。

## 修复范围

### 1. FinancialDataService canonical period key

修复前，DataService 直接使用原始报表期间字符串建立索引：

```text
(company_id, "2025年度")
(company_id, "FY2025")
(company_id, "2025")
```

会导致外部调用标准化期间 `2025` 时查询失败。

修复后，Statement 索引统一使用 canonical period：

```text
2025年度    → 2025
FY2025      → 2025
2025Q1      → 2025Q1
2025H1      → 2025H1
```

`statements()`、`previous_period()`、`previous_balance()`、`periods_for()` 均使用同一套期间语义。

### 2. 修正季度上一期日期

修复：

```text
2025Q1.previous()
    → 2024Q4
    start = 2024-10-01
    end   = 2024-12-31

2025Q4.previous()
    → 2025Q3
    start = 2025-07-01
    end   = 2025-09-30
```

同时保持：

```text
2025Q2.previous() → 2025Q1
2025Q3.previous() → 2025Q2
2025H2.previous() → 2025H1
2025H1.previous() → 2024H2
FY2025.previous() → 2024
```

### 3. 修正混合期间排序

`H1` 与 `Q2` 的期末日期相同，但 `H1` 是覆盖 Q1+Q2 的更完整期间，因此排序修正为：

```text
Q1
Q2
H1
Q3
Q4
H2
FY
```

避免字符串排序导致类似：

```text
2025Q10 / 2025Q2 / 2025H1
```

的错误顺序。

### 4. Workflow 支持混合期间

修复前 Workflow 使用：

```python
int(period)
```

无法处理 `2025Q1`、`2025H1`、`FY2025`。

修复后使用：

```python
PeriodNormalizer.parse(period).year
```

因此 `start_year/end_year` 可以正确筛选年度、季度、半年度与 FY 混合数据。

`_summary()` 中最新期间也从字符串 `max()` 改为按 `PeriodNormalizer.order_key()` 取最新期间。

## 新增回归测试

新增：

```text
tests/financial/test_runtime_v9_period_hardening.py
```

覆盖：

1. Q1/Q4 previous period 的正确日期。
2. Q/H/FY 混合期间语义排序。
3. `2025年度` 输入可被 DataService 通过 `FY2025` / `2025` canonical key 查询。
4. Formula Golden Runner 可在 raw period statement 上运行。
5. 混合期间数据下 `previous_period()` 语义正确。
6. Workflow 按查询年度过滤季度、半年度、FY。
7. Workflow summary 使用语义顺序选取最新期间。
8. canonical 与 raw period 的 source period 表示一致。

## 验收结果

### Period Hardening 专项测试

```bash
PYTHONPATH=. pytest tests/financial/test_runtime_v9_period_hardening.py -q
```

结果：

```text
8 passed
```

### Financial 全量测试

```bash
PYTHONPATH=. pytest tests/financial -q
```

结果：

```text
73 passed
```

## V9.3 封版状态

| 能力 | 状态 |
| --- | --- |
| Real Data API Snapshot | ✅ |
| Raw Source Provenance | ✅ |
| Parsed Fact Provenance | ✅ |
| Unit Normalization | ✅ |
| Period Normalization | ✅ |
| FinancialDataService Period Semantics | ✅ |
| Mixed Period Workflow | ✅ |
| Formula Golden | ✅ |
| Formula Mutation | ✅ |
| Calculation Input Provenance | ✅ |

V9.3 可进入 Final / 封版状态。

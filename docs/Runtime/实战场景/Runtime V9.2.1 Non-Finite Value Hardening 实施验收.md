# Runtime V9.2.1 Non-Finite Value Hardening 实施验收

## 1. 结论

- 版本：Runtime V9.2.1 Non-Finite Value Hardening
- 日期：2026-09-24
- 结论：**通过**
- 分支：`master`
- 基线提交：`1185985 feat(runtime-v9): add independent verification and missing data semantics`

本版本只解决 V9.2 复核中的 P1 问题：`NaN / +Inf / -Inf` 之前可能穿透计算链路，未完整落入 `INVALID` 语义。

## 2. 修复内容

### 2.1 MetricEngine

`compute_metric()` 在所有核心计算路径加入有限值检查：

```python
math.isfinite(value)
```

覆盖路径：

- reported 报表字段
- growth 同比指标
- ratio 指标
- average balance 指标
- free cash flow 派生指标

统一语义：

```text
None
  ↓
UNAVAILABLE

NaN / +Inf / -Inf
  ↓
INVALID

分母为 0
  ↓
INVALID

正常有限值
  ↓
CALCULATED
```

对应失败原因包括：

- `reported_value_not_finite`
- `growth_value_not_finite`
- `ratio_value_not_finite`
- `average_balance_value_not_finite`
- `free_cash_flow_value_not_finite`

### 2.2 Independent VerificationEngine

独立验证器同步增强：

- 待验证的 `FinancialCalculation.result` 为 `NaN / ±Inf` 时，直接标记 `INVALID`
- 独立公式重算结果为 `NaN / ±Inf` 时，也标记 `INVALID`
- 不允许非有限值参与 Evidence 生成
- `actual_value` 不会暴露非有限值，统一置为 `None`
- 消息明确返回：
  ```text
  独立公式验证结果是非有限值，判定为 INVALID。
  ```

## 3. 新增测试

新增到：

```text
tests/financial/test_runtime_v9_semantics.py
```

覆盖：

| 测试 | 语义 |
| --- | --- |
| `test_nan_is_invalid` | `NaN` 标记为 `INVALID` |
| `test_positive_infinity_is_invalid` | `+Inf` 标记为 `INVALID` |
| `test_negative_infinity_is_invalid` | `-Inf` 标记为 `INVALID` |
| `test_non_finite_ratio_is_invalid` | 非有限值进入 ratio 指标时标记 `INVALID` |
| `test_non_finite_growth_is_invalid` | 非有限值进入 growth 指标时标记 `INVALID` |
| `test_verification_engine_marks_non_finite_actual_result_invalid` | 独立验证器拒绝非有限重算结果 |

## 4. 测试结果

### 4.1 V9 专项测试

```bash
python -X utf8 -m pytest tests/financial -q
```

结果：

```text
27 passed
```

### 4.2 本地全量回归

```bash
python -X utf8 -m pytest -q
```

结果：

```text
705 passed, 0 failed
```

## 5. 验收检查表

| 项目 | 状态 |
| --- | --- |
| `NaN` 不进入 Observation / Evidence | ✅ |
| `+Inf` 不进入 Observation / Evidence | ✅ |
| `-Inf` 不进入 Observation / Evidence | ✅ |
| reported 指标非有限值标记 `INVALID` | ✅ |
| growth 指标非有限值标记 `INVALID` | ✅ |
| ratio 指标非有限值标记 `INVALID` | ✅ |
| average balance 指标非有限值标记 `INVALID` | ✅ |
| free cash flow 非有限值标记 `INVALID` | ✅ |
| 独立 VerificationEngine 拒绝非有限重算结果 | ✅ |
| 本地全量测试无回归 | ✅ |

## 6. 边界说明

本版本仍不进入 V10，也不接入真实上市公司财报数据。

下一步建议：

1. 确认 GitHub Actions 对本提交全绿。
2. 再进入 V9.3 Real Financial Data。
3. 接入 3 家真实公司 2021–2025 年财报。
4. 建立 Metric Formula Golden Expected Value。

# Runtime V9.2 Financial Semantics Hardening 实施验收

## 1. 结论

- 版本：Runtime V9.2 Financial Semantics Hardening
- 日期：2026-09-23
- 结论：**通过**
- 分支：`master`
- 基线提交：`5b6e0dd fix(runtime-v9): harden financial evidence, comparison and metrics`

本版本不进入估值、实时行情、股票推荐或 Skill Evolution，只解决 V9.1 复核中提出的独立验证、缺失数据语义、数据源实体、异常基线和 Comparison 显式路由问题。

## 2. 已修复项

### 2.1 独立 Verification Engine

新增：

```text
langgraph_langchain/runtime/financial/verification_engine.py
```

Verification 不再通过重复调用 `calculate_metric()` 实现“自己算自己验”，而是使用独立编码的金融公式路径。

新的验证链路：

```text
Financial Statement
  ↓
MetricEngine / compute_metric()
  ↓
FinancialCalculation
  ↓
IndependentVerificationEngine
  ↓
FinancialVerification
  ↓
FinancialEvidence
  ↓
FinancialFinding
```

支持：

- reported 报表字段
- growth 同比指标
- ratio 指标
- average balance 指标
- free cash flow 派生指标

Verification 状态：

```text
passed
failed
unavailable
invalid
```

### 2.2 缺失数据语义

报表字段从默认 `0.0` 改为可表达缺失：

```python
Optional[float] = None
```

新增 `MetricComputation` 与三种计算状态：

```text
calculated
unavailable
invalid
```

核心规则：

- 缺失字段：`UNAVAILABLE`
- 缺失上一期资产负债表：`UNAVAILABLE`
- 分母为 0：`INVALID`
- 非有限值：`INVALID`
- 真实 0 值仍然可以被正常计算
- `UNAVAILABLE / INVALID` 不会生成 Observation、Evidence、Finding 或 Risk Signal

报告新增：

```text
## 数据可用性
```

报告输入与结果中缺失值显式展示为：

```text
NULL
```

### 2.3 平均余额不再自动 fallback

`ROE / ROA / 应收账款周转率 / 存货周转率 / 总资产周转率` 使用平均余额时：

- 存在上一期：使用 `(current + previous) / 2`
- 缺失上一期：标记 `UNAVAILABLE`
- 不再错误地把当前期余额当成上一期余额
- 不再隐式使用 ending balance fallback

### 2.4 FinancialDataSource 真正接入

`FinancialDataService` 现在会根据每条标准化报表构建 `FinancialDataSource`。

每个 DataSource 包含：

- `source_id`
- `source_type`
- `company_id`
- `report_period`
- `document_name`
- `source_hash`

Evidence 与 DataSource 的关系变为：

```text
FinancialDataSource
  ↓
Financial Statement
  ↓
Financial Calculation
  ↓
Independent Verification
  ↓
Financial Evidence
```

`FinancialAnalysisResult.data_sources` 会返回当前 Evidence 实际引用的 DataSource 实体。

### 2.5 Anomaly Engine：历史基线

新增：

```text
langgraph_langchain/runtime/financial/anomaly_engine.py
```

异常检测从固定 `±10%` 阈值升级为：

```text
historical mean
+ historical standard deviation
+ current value
```

计算：

```text
z_score = (current - mean) / std
```

默认：

- `threshold = 2.0`
- `minimum_history = 3`
- `std = 0` 时不触发

新增模型：

```text
FinancialAnomalySignal
```

Finding Engine 使用异常结果生成 `anomaly` 类型 Finding，不再只使用固定阈值。

### 2.6 Comparison 显式路由

`Comparison` 不再作为多公司 Observation 的隐式副作用。

只有显式任务才生成：

```text
PEER_COMPARISON
COMPREHENSIVE_ANALYSIS
```

普通单公司或多公司 Overview 不会自动生成 Peer Comparison。

### 2.7 Adapter 与元数据升级

`FinancialEvaluationAdapter` 版本更新为：

```text
9.2.0
```

新增候选结果元数据：

- `unavailable_calculation_count`
- `invalid_calculation_count`
- `data_source_count`
- 原有 evidence / verified evidence / comparison / finding / risk signal

## 3. 测试结果

### 3.1 V9 专项测试

```bash
python -X utf8 -m pytest tests/financial -q
```

结果：

```text
21 passed
```

新增覆盖：

- 缺失上一期余额返回 `UNAVAILABLE`
- 分母为 0 返回 `INVALID`
- Workflow 不为不可计算指标生成 Evidence / Finding
- 独立验证器拒绝不一致结果
- Evidence 链接到 `FinancialDataSource`
- Anomaly 使用历史均值 / 标准差
- Comparison 只在显式任务中生成
- Adapter 输出 V9.2 语义元数据

### 3.2 本地全量回归

```bash
python -X utf8 -m pytest -q
```

结果：

```text
699 passed, 0 failed
```

## 4. 验收检查表

| 项目 | 状态 |
| --- | --- |
| Verification 独立于 MetricEngine | ✅ |
| 不再“同一函数算两次即通过” | ✅ |
| 缺失字段不是 0 | ✅ |
| `UNAVAILABLE` 不生成 Evidence / Finding | ✅ |
| 分母为 0 标记 `INVALID` | ✅ |
| 平均余额缺失不 fallback | ✅ |
| `FinancialDataSource` 实体接入 | ✅ |
| Evidence 绑定 DataSource | ✅ |
| Anomaly 使用历史均值 / 标准差 | ✅ |
| Anomaly 不再只依赖固定 ±10% | ✅ |
| Comparison 显式路由 | ✅ |
| 全量测试无回归 | ✅ |

## 5. 仍未纳入本版本

- 真实上市公司财报数据
- 官方年报 / XBRL 管道
- 估值指标
- DCF / PE / PB
- 实时行情
- 股票推荐
- 自动交易
- Skill Evolution / GEPA

下一步建议接入 3 家真实公司 2021–2025 年财报数据，先用真实数据验证 V9.2 的完整链路。

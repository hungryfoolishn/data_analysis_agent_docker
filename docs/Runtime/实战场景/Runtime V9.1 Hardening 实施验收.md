# Runtime V9.1 Hardening 实施验收

## 1. 结论

- 版本：Runtime V9.1 Financial Analysis Hardening
- 日期：2026-09-23
- 结论：**V9.1 Hardening 通过**
- 分支：`master`
- 基线提交：`5f52f5b feat(runtime-v9): add financial analysis domain and golden evaluation`

本版本不新增估值、实时行情、股票推荐或 Skill Evolution，只对 V9 金融分析能力做正确性硬化。

## 2. 修复范围

### 2.1 真实 Financial Evidence

修复前问题：

```text
Financial Calculation 数量
    ↓
被直接当作 Evidence 数量
```

修复后链路：

```text
Financial Statement Source
    ↓
Financial Observation
    ↓
Financial Calculation
    ↓
Financial Verification
    ↓
Financial Evidence
    ↓
Financial Finding
    ↓
Financial Report
```

新增或强化模型：

- `FinancialVerification`
- `FinancialEvidence`
- `FinancialFinding`

`FinancialEvidence` 现在包含：

- `source_ids`
- `source_fields`
- `company_id`
- `company_name`
- `stock_code`
- `period`
- `metric_id`
- `formula`
- `calculation_id`
- `verification_result_id`
- `verification_status`

`FinancialEvaluationAdapter` 不再使用：

```python
evidence_count = calculation_count
```

改为：

```python
evidence_count = len(result.evidence)
verified_evidence_count = count(FinancialEvidence.verification_status == "verified")
finding_count = len(result.findings)
```

### 2.2 真正 Peer Comparison

新增：

- `FinancialComparison`
- `FinancialComparisonEntity`
- `FinancialFindingEngine.build()`

Peer Comparison 输出：

- 参与公司
- 指标值
- 排名
- Leader
- Laggard
- Difference
- Relative Difference
- 方向语义：`higher_is_better`
- 可读 statement

Workflow 中已接入：

```text
Observation
  ↓
Comparison
  ↓
Finding
  ↓
Evidence
  ↓
Report
```

Golden Case 中 Peer Comparison 额外校验：

- `peer_difference:<metric_id>`
- `peer_relative_difference:<metric_id>`
- leader / laggard 上下文
- 期间一致性
- 报告包含 `同业比较`、`差异`、`相对差异`

### 2.3 Financial Metric 口径硬化

以下指标明确改为平均余额口径：

- `roe`：净利润 / 平均股东权益
- `roa`：净利润 / 平均总资产
- `receivable_turnover`：营业收入 / 平均应收账款
- `inventory_turnover`：营业成本 / 平均存货
- `asset_turnover`：营业收入 / 平均总资产

`FinancialMetricDefinition` 新增：

```text
balance_policy = ending_balance | average_balance
```

`calculate_metric()` 新增：

```python
previous_balance: BalanceSheetStatement | None
```

平均余额指标的计算输入保留：

- `current_denominator`
- `previous_denominator`
- `average_denominator`

### 2.4 Risk Detector 单位修复

`ocf_to_net_income` 的机器值是倍数：

```text
1.2x 表示经营现金流是净利润的 120%
```

修复前规则错误使用：

```text
value < 50
```

修复后规则使用：

```text
value < 0.5
```

同时 `FinancialRiskSignal` 增加：

- `unit`
- `display_value`

避免机器值和展示值混用。

### 2.5 Financial Finding Engine

新增：

```text
langgraph_langchain/runtime/financial/finding_engine.py
```

支持 Finding 类型：

- `metric`
- `trend`
- `peer_comparison`
- `risk`
- `anomaly`

每个 Finding 关联：

- statement
- companies
- periods
- metric
- evidence_ids
- calculation_ids
- risk_signal_ids

报告新增：

- `## 核心发现`
- `## 证据`

## 3. Golden Dataset / Golden Case 更新

重新生成 55 条 Financial Golden Cases：

```text
tests/financial/golden_cases/financial_golden_cases.json
```

更新内容：

- ROE / ROA / 营运周转指标改用平均余额期望值
- Peer Comparison 增加 difference / relative difference 期望
- 报告关键词加入：
  - `核心发现`
  - `证据`
  - Peer Case 加入 `同业比较`
  - Peer Case 加入 `差异`
  - Peer Case 加入 `相对差异`

数据集本体仍为确定性合成数据：

```text
20 家公司
5 个行业
2021–2025
```

## 4. 测试结果

### 4.1 V9 专项测试

```bash
python -X utf8 -m pytest tests/financial -q
```

结果：

```text
13 passed
```

新增覆盖：

- 平均余额指标
- Verified Evidence
- Evidence-backed Finding
- Peer Comparison ranking / difference
- OCF / Net Profit 机器值阈值
- Evaluation Adapter 不再将 Calculation 当作 Evidence
- 55 条 Golden Cases 全部通过

### 4.2 全量回归

最终提交前执行：

```bash
python -X utf8 -m pytest -q
```

结果：

```text
691 passed, 0 failed
```


## 5. 验收检查表

| 项目 | 状态 |
| --- | --- |
| Evidence 不再是伪证据 | ✅ |
| Calculation 不直接映射 Evidence | ✅ |
| Verification 独立存在 | ✅ |
| Evidence 绑定 source_ids / source_fields | ✅ |
| Finding 引用真实 Evidence | ✅ |
| Peer Comparison 输出差异 | ✅ |
| Peer Comparison 输出相对差异 | ✅ |
| Peer Comparison 输出 ranking | ✅ |
| Golden Case 校验 Comparison | ✅ |
| ROE 使用平均股东权益 | ✅ |
| ROA 使用平均总资产 | ✅ |
| 应收账款周转使用平均余额 | ✅ |
| 存货周转使用平均余额 | ✅ |
| 总资产周转使用平均余额 | ✅ |
| OCF / Net Profit 使用 `x` 机器值 | ✅ |
| Risk Signal 区分机器值与展示值 | ✅ |
| V9 原 55 Golden Cases 继续通过 | ✅ |
| 本地全量回归 | ✅ |

## 6. 仍未纳入本版本

以下内容仍然不在 V9.1 范围内：

- 真实上市公司财报数据
- 估值指标
- DCF / PE / PB
- 实时行情
- 股票推荐
- 自动交易
- Skill Evolution / GEPA

下一步建议先接入 3 家真实公司 2021–2025 年财报数据，再扩展 V10 估值能力。

# Runtime V10.4 阶段 1：Financial Period Semantics & Auditability

## 结论

V10.4 阶段 1 已完成期间查询精确性、显式季度语义解析和季度平均余额黄金值验收。
本阶段不新增 Agent、工具或调度器功能，只收敛财务期间口径与证据可审计性。

## 背景问题

### 1. 同年期间混选（P0 级查询精确性风险）

`FinancialAnalysisWorkflow.run()` 原先仅按 `start_year/end_year` 过滤期间。
当任务请求 `2023Q1` 而数据服务同时存在 `2023Q2`、`2023H1`、`2023` 时，
所有 2023 年度期间都会被计算并进入分析结果，季度、半年度与年度数据互相污染。

### 2. 语义解析不支持显式季度问题

`FinancialTaskUnderstandingBuilder` 只扫描年份，`分析 2023Q1 ROA` 会被
降级为 2023 年度问题；`FinancialSemanticResolver` 也只匹配 FY 期间。

### 3. 季度平均余额夹具不足

`_balance()` 对季度期间回退到 2023 年度数值，导致季度 ROA/ROE/周转率
测试无法区分前期余额来源。

## 代码变更

### `langgraph_langchain/runtime/financial/models.py`

- `FinancialQuery` 新增 `periods: list[str]`（规范化期间显式列表，默认空）。

### `langgraph_langchain/runtime/financial/workflow.py`

- `query.periods` 非空时按规范化期间精确选择，缺失期间直接跳过，
  不再回退到最近 5 个期间；
- `query.periods` 为空时保留原年份范围行为（向后兼容）。

### `langgraph_langchain/runtime/financial_agent/models.py`

- `PeriodResolution` 新增 `canonical_periods: list[str]`。

### `langgraph_langchain/runtime/financial_agent/understanding.py`

- 新增 `_extract_explicit_periods()`：识别 `2023Q1`-`2023Q4`、`2023H1/H2`
  显式期间记号，优先于纯年份扫描，避免 `2023Q1` 降级为年度问题；
- 盈利能力关键词补充 `净资产收益率`、`总资产收益率`。

### `langgraph_langchain/runtime/financial_agent/semantic.py`

- `period_range.canonical_periods` 非空时按公司可用规范化期间精确匹配，
  缺失期间计入 `missing_periods`，不回退到年度选择。

### `langgraph_langchain/runtime/financial_agent/runtime_integration.py`

- 新增 `_periods_from_tasks()`：从计划任务提取精确规范化期间（TTM 等
  不可解析期间跳过）；
- DAG 与 workflow 两条路径构造 `FinancialQuery` 时都写入 `periods`。

### 测试夹具

- `tests/financial_agent/test_runtime_integration.py` 的 `_balance()` 显式补充
  `2022Q4`、`2023Q1`、`2023Q2` 资产负债表数值，消除季度回退污染。

## 新增测试

`tests/financial_agent/test_period_semantics_v10_4.py`：

1. `test_quarterly_average_balance_golden_values`
   - 2023Q1/2023Q2 的 ROA、ROE、总资产周转率、应收账款周转率、存货周转率
     十项黄金值，全部人工可复核，例如：
     - ROA 2023Q1 = 60 / ((500 + 400) / 2) = 13.333333%
     - ROE 2023Q2 = 130 / ((400 + 300) / 2) = 37.142857%
     - asset_turnover 2023Q2 = 650 / 575 = 1.130435
   - 同时断言 numerator/current/previous/average 四项输入、验证通过、
     observation 期间与 `average_balance` 策略。
2. `test_explicit_query_periods_do_not_mix_quarter_half_and_fy`
   - 同一数据服务混合 `2023Q1/2023Q2/2023H1/2023` 时，显式 `periods=["2023Q1"]`
     只产生 `2023Q1` 观察；`2023H1` 同理。
3. `test_query_without_explicit_periods_keeps_year_range_behavior`
   - 不传 `periods` 时保持旧年份范围行为（兼容性护栏）。
4. `test_quarter_question_resolves_to_exact_quarter_plan`
   - `2023Q1` 自然语言问题生成 `2023Q1` 任务，绝不回退年度期间。
5. `test_end_to_end_quarter_execution_keeps_period_and_evidence_consistent`
   - 从自然语言问题到 DAG 执行全链路：分析结果的 observations、calculations、
     verifications、evidence 期间全部等于 `2023Q1`；每条 Evidence 的
     calculation_id、verification_id 与任务结果一致；source_ids 全部可解析回
     具体数据源且公司一致。

同时更新 `tests/financial_agent/test_task_executor.py` 中期间聚合测试：
workflow 模式与 DAG 模式都必须精确匹配请求期间，`2023Q3` 不得泄漏
（该断言由并行修复引入，与 V10.4 行为一致）。

## 回归结果

```bash
PYTHONPATH=. pytest tests/financial_agent -q
# 158 passed

PYTHONPATH=. pytest tests/financial/test_runtime_v9_semantics.py \
  tests/financial/test_runtime_v9_period_hardening.py \
  tests/financial/test_runtime_v9.py \
  tests/financial/test_runtime_v9_hardening.py \
  tests/financial/test_runtime_v9_golden.py -q
# 60 passed

PYTHONPATH=. pytest -q
# 见全量回归记录
```

## 边界与后续（V10.4 阶段 2+）

- 季度利润表仍按累计值口径读取；`growth_period_basis=single_period` 时
  自动推导单期值（V10.3.3 行为保持不变）。
- 尚未覆盖：单位不一致检测、跨报表期间错配的批量检测、
  报告层的期间口径标注强化。
- `FinancialQuery.periods` 是计划层的显式契约；独立调用 workflow 的
  旧入口不传 `periods` 时行为不变。

## 并行修改说明

实施过程中发现另一会话同期修改了 `tests/financial_agent/test_task_executor.py`
（新增 `test_quarterly_average_balance_metrics_use_distinct_period_balances`
并修正期间聚合断言）。该修改已并入本次变更集，其黄金值依赖的季度夹具由
`test_runtime_integration.py` 的显式季度余额补充完成，全部测试通过。

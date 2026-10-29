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


## 阶段 2：增长率口径端到端闭环

### 目标

把自然语言中的增长率意图贯通到执行计划、任务输入、计算公式和 Evidence，避免“用户说环比，系统跑同比累计”。

### 自然语言口径解析

`FinancialTaskUnderstanding` 新增：

```text
growth_comparison_type
growth_period_basis
```

`FinancialTaskUnderstandingBuilder` 显式识别：

| 用户表达 | comparison_type | period_basis |
| --- | --- | --- |
| 同比 + 显式季度/半年度 | `year_over_year` | `single_period` |
| 同比 + 年度，或未说明基础 | `year_over_year` | `cumulative` |
| 环比 + 显式季度/半年度 | `sequential` | `single_period` |
| 累计同比 / YTD YoY | `year_over_year` | `cumulative` |
| 累计环比 / 相邻报告期 | `sequential` | `cumulative` |

### 执行计划契约

`ExecutionPlanBuilder` 优先使用理解结果中的口径；显式构造参数仍可覆盖自然语言意图。

最终返回的 `ExecutionPlan.metadata` 现在保留：

```text
growth_comparison_type
growth_period_basis
```

增长率任务的 `inputs` 同样保留这两个字段和：

```text
previous_period
```

### 端到端审计

新增测试覆盖三类问题：

1. `2023Q2 收入同比增长率`
   - `year_over_year + single_period`
   - 基期 `2022Q2`
   - 单期收入 `120` 对 `100`
   - 增长率 `20%`

2. `2023Q2 收入环比增长率`
   - `sequential + single_period`
   - 基期 `2023Q1`
   - 增长率 `20%`

3. `2023Q2 累计收入同比增长率`
   - `year_over_year + cumulative`
   - 基期 `2022Q2`
   - 累计收入 `220` 对 `180`
   - 增长率 `22.222222%`

测试同时验证：

- `ExecutionPlan.metadata`
- `ExecutionTask.inputs`
- `RuntimeTaskResult.metadata`
- `FinancialCalculation.formula`
- `FinancialEvidence.formula`
- `FinancialCalculation.source_ids`
- `FinancialEvidence.source_ids`


### 阶段 2 回归结果

```bash
PYTHONPATH=. pytest \
  tests/financial_agent/test_period_semantics_v10_4.py \
  tests/financial_agent/test_task_executor.py \
  tests/financial_agent/test_scheduler.py \
  tests/financial_agent/test_execution_plan.py \
  tests/financial_agent/test_runtime_integration.py \
  tests/financial_agent/test_tool_execution.py \
  -q
# 88 passed

PYTHONPATH=. pytest \
  tests/financial/test_runtime_v9_semantics.py \
  tests/financial/test_runtime_v9_period_hardening.py \
  tests/financial/test_runtime_v9.py \
  tests/financial/test_runtime_v9_hardening.py \
  tests/financial/test_runtime_v9_golden.py \
  -q
# 60 passed

PYTHONPATH=. pytest -q
# 912 passed, 11 warnings, 4 subtests passed
```


## 阶段 3：同比 / 环比多意图与歧义治理

### 多任务语义

`FinancialTaskUnderstanding` 新增：

```text
growth_intents
growth_intent_source
growth_intent_diagnostics
```

当用户同时要求同比和环比时，系统生成两个独立的增长率任务：

```text
year_over_year + single_period
sequential     + single_period
```

每个任务使用：

```text
growth_comparison_variant
```

作为唯一比较口径签名，避免同公司、同期间、同指标任务被误判为重复。

### 默认口径策略

- 显式季度 / 半年度 + 未说明同比 / 环比：
  - 默认 `year_over_year + single_period`
  - `growth_intent_source = default`
- 年度增长：
  - 默认 `year_over_year + cumulative`
- 实际采用的口径会写入计划、任务和结果元数据。

### 冲突与歧义

- `同比和环比`：
  - 不是冲突，生成两个任务。
- `同比和环比二选一` / `口径冲突`：
  - 写入 `GROWTH_COMPARISON_CONFLICT`。
- `同比还是环比`：
  - 写入 `GROWTH_COMPARISON_AMBIGUOUS`。
  - 不会静默选择其中一个口径。

### 旧 workflow 模式边界

多增长率意图必须走 DAG。旧批量工作流遇到多个增长率意图时返回结构化失败，避免把同一口径结果错误映射到多个任务。

### 新增验证

- 同时同比 / 环比生成两个独立增长率任务。
- 每个任务有独立 `growth_comparison_variant`、`previous_period`、结果和元数据。
- 默认口径显式记录为 `default`。
- 冲突和歧义写入诊断。
- Q1–Q4、H1/H2 的同比 / 环比基期边界测试。
- 依赖图按 `company + period + metric + comparison_variant` 建键。


### 阶段 3 回归结果

```bash
PYTHONPATH=. pytest \
  tests/financial_agent/test_period_semantics_v10_4.py \
  tests/financial_agent/test_task_executor.py \
  tests/financial_agent/test_scheduler.py \
  tests/financial_agent/test_execution_plan.py \
  tests/financial_agent/test_runtime_integration.py \
  tests/financial_agent/test_tool_execution.py \
  -q
# 93 passed

PYTHONPATH=. pytest \
  tests/financial/test_runtime_v9_semantics.py \
  tests/financial/test_runtime_v9_period_hardening.py \
  tests/financial/test_runtime_v9.py \
  tests/financial/test_runtime_v9_hardening.py \
  tests/financial/test_runtime_v9_golden.py \
  -q
# 60 passed

PYTHONPATH=. pytest -q
# 917 passed, 26 warnings, 4 subtests passed
```


## 阶段 4：Growth Intent Guardrails

### 目标

冲突和歧义不再只是诊断信息，而是计划级阻断条件。

### 阻断规则

- `GROWTH_COMPARISON_CONFLICT`
  - 不生成增长率任务；
  - 执行计划状态为 `BLOCKED`；
  - 不允许选择任意一个口径继续计算。
- `GROWTH_COMPARISON_AMBIGUOUS`
  - 不生成增长率任务；
  - 执行计划状态为 `BLOCKED`；
  - 不静默选择同比或环比。
- 两种情况都会写入：
  - `FinancialTaskUnderstanding.planning_diagnostics`
  - `ExecutionPlan.diagnostics`
  - `ExecutionPlan.metadata.blocking_diagnostics`
  - `planning_readiness = NEEDS_CLARIFICATION`

### 累计口径生成规则

当问题包含“累计”或“年初至今”时，所有被请求的比较都保持在累计基础：

```text
累计收入同比和环比
  -> year_over_year + cumulative
  -> sequential     + cumulative
```

不会额外生成：

```text
sequential + single_period
```

### 执行边界

被阻断的执行计划不会进入 DAG。`FinancialRuntimeExecutor.execute_plan()` 会因为计划状态不是 `PENDING` 而返回明确错误，避免生成看似成功的报告。


### 阶段 4 回归结果

```bash
PYTHONPATH=. pytest \
  tests/financial_agent/test_period_semantics_v10_4.py \
  tests/financial_agent/test_task_executor.py \
  tests/financial_agent/test_scheduler.py \
  tests/financial_agent/test_execution_plan.py \
  tests/financial_agent/test_runtime_integration.py \
  tests/financial_agent/test_tool_execution.py \
  -q
# 96 passed

PYTHONPATH=. pytest \
  tests/financial/test_runtime_v9_semantics.py \
  tests/financial/test_runtime_v9_period_hardening.py \
  tests/financial/test_runtime_v9.py \
  tests/financial/test_runtime_v9_hardening.py \
  tests/financial/test_runtime_v9_golden.py \
  -q
# 60 passed

PYTHONPATH=. pytest -q
# 920 passed, 11 warnings, 4 subtests passed
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

---

## Runtime V10.4 阶段 5：端到端阻断与增长率口径验收

对应提交 `21ea4d8 fix(runtime-v10.4): block ambiguous growth intents` 之后的验收收尾。

### 背景

`21ea4d8` 已在计划层阻断 conflict/ambiguous 增长意图，但阻断以
`RuntimeIntegrationError` 异常形式暴露，上层调用方一旦漏接异常就会变成
笼统执行失败；同时增长率比较基期与累计/单期口径的正确性缺少端到端黄金值证明。

### 变更 1：结构化 BLOCKED 结果贯穿执行链路

`FinancialRuntimeExecutor.execute_plan()` 对 `status=BLOCKED` 的计划：

- 不再抛出异常，返回结构化 `RuntimeExecutionResult`：
  - `status=BLOCKED`、`task_results=[]`、三项计数均为 0；
  - `analysis_result=None`，不产生任何 Observation/Calculation/Evidence；
  - `metadata.blocked_before_execution=True`，保留
    `blocking_diagnostics`、`growth_intent_source`、`growth_intents` 等契约；
- `report_markdown` 为确定性澄清报告：明确说明“未生成任何财务计算结果或财务结论”，
  列出阻断诊断，并提示用户澄清同比/环比与累计/单期口径；
- DAG 与 workflow 两种执行模式行为一致（模式无关短路）；
- 其他非 PENDING 状态仍保持原有 `RuntimeIntegrationError` 契约。

对应更新 `test_growth_intent_conflict_blocks_plan_execution` /
`test_growth_intent_ambiguity_blocks_plan_execution` 的契约断言，并新增
`test_blocked_intent_structured_result_in_both_execution_modes`。

### 变更 2：DAG 路径缺失基期的明确不可用原因

`TaskLevelFinancialExecutor._compute_growth()` 在比较期报表缺失时，从
`growth_value_missing` 改为显式 `growth_previous_period_missing`，
与 workflow 路径语义对齐：缺失基期是数据不可用，不是数值缺失。

### 阶段 5 验收测试

新增于 `tests/financial_agent/test_period_semantics_v10_4.py`：

1. **全链路阻断**
   - `test_blocked_intent_structured_result_in_both_execution_modes`：
     conflict 计划在 DAG/workflow 两种模式下都返回结构化 BLOCKED，
     报告不含任何财务数字，仅含澄清指引。
2. **四种增长率口径黄金值（人工可复核）**

   累计收入样例 `2022Q1=250, 2022Q2=600, 2023Q1=300, 2023Q2=800`，
   推导单季收入 `2022Q2=350, 2023Q2=500`：

   | 口径 | 比较基期 | 黄金值 |
   | --- | --- | --- |
   | 同比/累计 | 2022Q2 累计 (600) | (800-600)/600 = 33.333333% |
   | 环比/累计 | 2023Q1 累计 (300) | (800-300)/300 = 166.666667% |
   | 同比/单期 | 2022Q2 单季 (350) | (500-350)/350 = 42.857143% |
   | 环比/单期 | 2023Q1 单季 (300) | (500-300)/300 = 66.666667% |

   `test_growth_comparison_matrix_golden_values` 同时断言计算输入
   （current/previous）、独立验证通过，并验证：
   - workflow 模式对多意图计划显式拒绝
     （`Multiple growth comparison intents require DAG execution`），
     不静默选择口径；
   - 单意图计划 DAG 与 workflow 计算结果一致（引擎等价性）。

3. **比较基期与证据一致性**
   - `test_cumulative_and_single_period_use_correct_comparison_bases`：
     四个口径变体各自拥有独立 task_id、比较基期
     （2022Q2/2023Q1/2022Q2/2023Q1）、source_ids、calculation、verification
     与 Evidence；Evidence 的 formula/period/calculation_id 与 Calculation 一致。
4. **缺失基期矩阵**
   - `test_missing_growth_base_period_matrix_is_unavailable`：
     年度同比、2023Q1 同比/单期、2023Q1 环比、2023H1 同比在缺少 2022 年
     基期时全部返回 `UNAVAILABLE` 且 `status_reason=growth_previous_period_missing`；
     对照组（2023Q2 环比/累计）正常计算 166.666667%。

### 回归结果

```bash
PYTHONPATH=. pytest tests/financial_agent -q
# 172 passed

PYTHONPATH=. pytest -q
# 全量回归（见验收记录）
```

### CI 核实（P2）

- GitHub Actions `Runtime Tests`（run 40660387828）在 `21ea4d8` 上
  push 触发，9/9 步骤成功，结论 success。
  文档中的全量测试数字自此有可核验的 CI 运行记录支撑。

### 后续边界

- 多意图计划仅在 DAG 模式执行；workflow 模式的显式拒绝为设计行为，
  上层如需兼容需向用户说明改用 DAG 模式或拆分请求。
- 报告层（`_blocked_plan_report`）当前为确定性澄清文本；如需接入
  LLM 生成澄清话术，必须保留“无财务数字”的硬约束。

---

## Runtime V10.4 阶段 5 补充：口径否定表达解析

### 问题

累计上下文判断原先使用 `"累计" in text`，否定表达会被误读：

- “不要累计，按单季度收入同比分析” → 仍被识别为累计口径；
- “不是累计口径，计算单季度环比” → 同样误判。

### 修复（`understanding.py`）

新增 `_resolve_period_basis_context()`，以否定窗口（关键词前 4 个字符内的
否定词：不要/不是/非/别/无需/不用/排除）区分正向与否定用法，并按以下优先级：

1. **矛盾表述请求澄清**（返回 `ambiguous` +
   `GROWTH_PERIOD_BASIS_AMBIGUOUS`）：
   - 同一关键词既被正向又被否定（“不是累计口径，按累计同比”）；
   - 双向否定（“不要累计也不要单季”）。
2. **正向累计关键词优先**：“累计/年初至今” → 累计口径；
3. **否定累计或正向单季** → 单期口径；
4. **否定单季** → 累计口径；
5. 无显式关键词时回退到子年度期间启发式（原行为不变）。

`_extract_growth_intents` 使用该覆盖值生成意图；矛盾时返回
`ambiguous`，由计划层继续阻断。旧的单意图路径
`_extract_growth_semantics` 同步应用同一覆盖，保持两条路径语义一致。

### 新增测试

- `test_negated_cumulative_keyword_resolves_to_single_period`：
  “不要累计”/“不是累计口径”/“非累计” 三种否定表达均解析为单期口径，
  且同比/环比/多意图组合正确；
- `test_negated_single_period_keyword_resolves_to_cumulative`：
  “不要单季度口径，按累计计算” → 累计口径；
- `test_negated_cumulative_intent_executes_single_period_golden_value`：
  否定累计的端到端执行命中单期同比黄金值 42.857143%
  （current=500，previous=350），比较基期 2022Q2；
- `test_contradictory_period_basis_wording_requires_clarification`：
  “不要累计也不要单季” → `NEEDS_CLARIFICATION` → `BLOCKED` 计划
  （含 `GROWTH_PERIOD_BASIS_AMBIGUOUS`）→ 结构化 BLOCKED 执行结果。

### 回归结果

```bash
PYTHONPATH=. pytest tests/financial_agent -q
# 176 passed

PYTHONPATH=. pytest -q
# 全量回归见验收记录
```

---

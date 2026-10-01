# Data Analysis Agent Runtime V10

# Financial Analysis Agent Intelligence 设计与实施方案

**版本：V10**
**当前前置版本：V9.3 Final（tag: v9.3.0）**
**状态：设计与实施方案**
**核心主题：金融任务理解、分析计划、语义解析、工具选择与可信执行闭环**

---

## 一、V10 的定位

V9.3 已经完成金融计算 Runtime 的可信底座：

```text
Real Financial Data API Snapshot
  → Raw Source
  → Parsed Financial Fact
  → Period Normalization
  → Unit Normalization
  → Normalized Statement
  → Metric Calculation
  → Verification
  → Evidence
  → Finding
  → Report
```

V9.3 的能力重点是：给定公司、期间和任务类型后，系统能够稳定地完成可验证的金融计算。

V10 的目标不是继续增加指标，也不是继续扩大 Golden Case，而是把现有 Runtime 接入真正的 Agent 层：

```text
User Goal
  → Task Understanding
  → Financial Task Planner
  → Semantic Resolver
  → Tool Selection
  → Existing Financial Runtime
  → Plan Evaluation
  → Evidence-backed Financial Insight
  → Financial Research Report
```

核心原则：

> LLM 负责理解、规划、解释和有限重规划；  
> 确定性引擎负责金融计算、数据校验、验证、证据和风险规则。  
> Agent 不允许绕过验证，也不允许为了成功而伪造结论。

---

## 二、V10 版本阶段

| 版本 | 名称 | 主要范围 | 核心交付 |
| --- | --- | --- | --- |
| V10.0 | Task Understanding + Planner | 金融任务理解与计划 DAG | 结构化任务、计划依赖、工具映射、计划校验 |
| V10.1 | Metric / Data / Period Resolver | 金融语义解析与数据覆盖检查 | 公司、期间、指标、口径、缺失数据解析 |
| V10.2 | Tool Selection + Runtime Integration | Agent 调用已有 Runtime | Planner 驱动 V9.3 Workflow 执行 |
| V10.3 | Insight + Recovery + Evaluation | 结论生成与失败恢复 | Evidence-backed Insight、Plan Evaluation、闭环评估 |

V10.0 只解决“理解目标 + 生成可执行计划 + 校验计划”，不直接执行分析。V10.1 及之后才逐步接入执行。

---

## 三、V10.0 目标

V10.0 首个端到端示例：

```text
分析贵州茅台 2021—2025 年的收入、利润、盈利能力和现金流变化，并与五粮液比较。
```

系统应生成：

```text
Task 1: Resolve Companies
Task 2: Resolve Periods
Task 3: Validate Data Coverage
Task 4: Revenue Trend
Task 5: Net Profit Trend
Task 6: Profitability Analysis
Task 7: Cash Flow Analysis
Task 8: Peer Comparison
Task 9: Verify Calculations
Task 10: Build Evidence-backed Findings
Task 11: Generate Report
```

V10.0 不执行这些 Task，只生成和校验 Plan。

---

## 四、Task Understanding 模块

### 1. 输入

```text
自然语言金融分析问题
```

示例：

```text
分析贵州茅台过去五年的经营情况，并与五粮液比较。
```

### 2. 输出：FinancialTaskUnderstanding

```json
{
  "query_id": "query_001",
  "raw_question": "分析贵州茅台 2021—2025 年的收入、利润、盈利能力和现金流变化，并与五粮液比较。",
  "task_type": "COMPREHENSIVE_ANALYSIS",
  "companies": [
    {
      "company_name": "贵州茅台",
      "stock_code": "600519",
      "company_id": "600519",
      "resolution": "resolved"
    }
  ],
  "period_range": {
    "start_year": 2021,
    "end_year": 2025,
    "period_type": "ANNUAL"
  },
  "objectives": [
    "revenue_trend",
    "profit_trend",
    "profitability",
    "cashflow",
    "peer_comparison"
  ],
  "required_metrics": [
    "revenue",
    "revenue_growth",
    "net_profit",
    "net_profit_growth",
    "net_margin",
    "roe",
    "operating_cash_flow",
    "free_cash_flow",
    "ocf_to_net_income"
  ],
  "comparison_enabled": true,
  "ambiguities": [],
  "missing_information": [],
  "confidence": 0.95
}
```

### 3. V10.0 实现边界

V10.0 使用确定性规则解析，先不引入 LLM：

| 输入信号 | 解析结果 |
| --- | --- |
| 公司名 / 股票代码 / 证券简称 | companies |
| 2021、2021—2025、近五年 | period_range |
| 收入、利润、盈利能力、现金流、偿债、风险、比较 | objectives |
| 两个及以上公司 + “比较/对比” | comparison_enabled |
| 未知公司、无期间、无有效目标 | missing_information |

LLM 可以在后续版本作为增强解析器，但 V10.0 必须有确定性 fallback。

---

## 五、Financial Task Planner

### 1. 职责

Planner 只做目标拆解、依赖建模和工具映射，不负责：

```text
❌ 直接读取财务数据
❌ 计算指标
❌ 修改公式
❌ 生成数字结论
❌ 删除验证步骤
```

### 2. 输出：FinancialPlan

```json
{
  "plan_id": "plan_001",
  "query_id": "query_001",
  "version": "financial-plan-v1",
  "status": "validated",
  "tasks": [
    {
      "task_id": "task_001",
      "task_type": "RESOLVE_COMPANIES",
      "name": "Resolve Companies",
      "dependencies": [],
      "required_data": [],
      "required_metrics": [],
      "selected_tool": "company_resolver",
      "tool_version": "v10.0.0",
      "execution_status": "PENDING",
      "verification_status": "NOT_REQUIRED",
      "output_refs": [],
      "failure_policy": "FAIL_FAST"
    },
    {
      "task_id": "task_004",
      "task_type": "REVENUE_TREND",
      "name": "Revenue Trend",
      "dependencies": [
        "task_001",
        "task_002",
        "task_003"
      ],
      "required_data": [
        "income_statement.revenue"
      ],
      "required_metrics": [
        "revenue",
        "revenue_growth"
      ],
      "selected_tool": "financial_metric_engine",
      "tool_version": "v9.3.0",
      "execution_status": "PENDING",
      "verification_status": "REQUIRED",
      "output_refs": [],
      "failure_policy": "CONTINUE_WITH_WARNING"
    }
  ]
}
```

---

## 六、Task Type 体系

V10.0 首批 Task Type：

| Task Type | 目的 | selected_tool | 依赖 |
| --- | --- | --- | --- |
| RESOLVE_COMPANIES | 解析并校验公司 | company_resolver | 无 |
| RESOLVE_PERIODS | 解析期间范围 | period_resolver | RESOLVE_COMPANIES |
| VALIDATE_DATA_COVERAGE | 检查数据覆盖 | data_coverage_checker | RESOLVE_COMPANIES、RESOLVE_PERIODS |
| REVENUE_TREND | 收入趋势 | financial_metric_engine | 前置数据校验 |
| PROFIT_TREND | 利润趋势 | financial_metric_engine | 前置数据校验 |
| PROFITABILITY_ANALYSIS | 盈利能力分析 | financial_metric_engine | 前置数据校验 |
| CASHFLOW_ANALYSIS | 现金流分析 | financial_metric_engine | 前置数据校验 |
| SOLVENCY_ANALYSIS | 偿债能力分析 | financial_metric_engine | 前置数据校验 |
| RISK_DETECTION | 规则型风险检测 | risk_detector | 相关指标计算 |
| PEER_COMPARISON | 同业比较 | comparison_engine | 相关指标计算 |
| VERIFY_CALCULATIONS | 独立验证计算 | verification_engine | 相关指标计算 |
| BUILD_FINDINGS | 生成 findings | finding_engine | VERIFY_CALCULATIONS |
| GENERATE_REPORT | 生成报告 | report_builder | BUILD_FINDINGS |

不可执行任务必须保持：

```json
{
  "selected_tool": null,
  "execution_status": "NOT_EXECUTABLE",
  "failure_policy": "REPLAN_OR_FAIL"
}
```

不允许映射到不存在的工具。

---

## 七、Objectives → Task / Metric 映射

| Objective | Plan Task | required_metrics |
| --- | --- | --- |
| revenue_trend | REVENUE_TREND | revenue、revenue_growth |
| profit_trend | PROFIT_TREND | net_profit、net_profit_growth |
| profitability | PROFITABILITY_ANALYSIS | net_margin、gross_margin、operating_margin、roe、roa |
| cashflow | CASHFLOW_ANALYSIS | operating_cash_flow、free_cash_flow、ocf_to_net_income |
| solvency | SOLVENCY_ANALYSIS | debt_to_asset、current_ratio、quick_ratio |
| operating_efficiency | OPERATING_ANALYSIS | receivable_turnover、inventory_turnover、asset_turnover |
| risk | RISK_DETECTION | debt_to_asset、revenue_growth、net_profit_growth、ocf_to_net_income |
| peer_comparison | PEER_COMPARISON | 与前述 objectives 求交集 |

Planner 生成 Plan 后必须做静态校验：

1. task_id 唯一。
2. dependencies 必须存在。
3. 不允许循环依赖。
4. 所有 selected_tool 必须已注册。
5. 所有 required_metrics 必须存在于 `financial_metric_registry`。
6. `VERIFY_CALCULATIONS` 不得被跳过。
7. `GENERATE_REPORT` 必须依赖 `BUILD_FINDINGS`。
8. 数值型任务必须标记 `verification_status = REQUIRED`。
9. 未注册工具 / 未注册指标必须导致 Plan 校验失败。
10. 缺失数据不改变 Plan 语义，只能在执行期产生 `UNAVAILABLE`。

---

## 八、Semantic Resolver 边界

`Semantic Resolver` 属于 V10.1 详细实现，但 V10.0 的 Planner 需要预留接口：

```python
class FinancialSemanticResolver:
    def resolve_companies(self, names: list[str]) -> CompanyResolution
    def resolve_periods(self, period_range) -> PeriodResolution
    def resolve_metrics(self, objectives: list[str]) -> MetricResolution
    def resolve_data_requirements(self, metrics: list[str]) -> DataRequirementResolution
```

V10.0 可先由确定性 `FinancialTaskUnderstandingBuilder` 提供简化实现；V10.1 再拆成独立模块并补齐数据覆盖检查。

---

## 九、Tool Registry

### 1. 目标

Agent 不能自由生成任意 Python 代码作为金融计算方式。工具必须显式注册。

```python
FINANCIAL_TOOL_REGISTRY = {
    "company_resolver": FinancialToolSpec(...),
    "period_resolver": FinancialToolSpec(...),
    "data_coverage_checker": FinancialToolSpec(...),
    "financial_metric_engine": FinancialToolSpec(...),
    "comparison_engine": FinancialToolSpec(...),
    "risk_detector": FinancialToolSpec(...),
    "verification_engine": FinancialToolSpec(...),
    "finding_engine": FinancialToolSpec(...),
    "report_builder": FinancialToolSpec(...),
}
```

### 2. FinancialToolSpec

```json
{
  "tool_id": "financial_metric_engine",
  "name": "Financial Metric Engine",
  "version": "v9.3.0",
  "input_schema": "financial_metric_engine_input_v1",
  "output_schema": "financial_metric_engine_output_v1",
  "capabilities": [
    "metric_calculation",
    "trend_analysis"
  ],
  "supported_task_types": [
    "REVENUE_TREND",
    "PROFIT_TREND",
    "PROFITABILITY_ANALYSIS",
    "CASHFLOW_ANALYSIS",
    "SOLVENCY_ANALYSIS",
    "OPERATING_ANALYSIS"
  ],
  "deterministic": true,
  "requires_verification": true
}
```

### 3. 映射规则

| 既有能力 | V10 Tool ID | 来源 |
| --- | --- | --- |
| 公司解析 | company_resolver | FinancialTaskClassifier + FinancialDataService |
| 期间解析 | period_resolver | PeriodNormalizer |
| 数据覆盖检查 | data_coverage_checker | V10.1 |
| 指标计算 | financial_metric_engine | V9.3 MetricEngine |
| 同业比较 | comparison_engine | FinancialAnalysisWorkflow |
| 风险检测 | risk_detector | FinancialRiskDetector |
| 独立验证 | verification_engine | FinancialVerificationEngine |
| Finding 生成 | finding_engine | FinancialFindingEngine |
| 报告生成 | report_builder | FinancialAnalysisWorkflow |

V10.0 只注册工具；V10.2 再执行工具。

---

## 十、Plan Execution 状态模型

### Task Status

```text
PENDING
READY
RUNNING
SUCCEEDED
FAILED
BLOCKED
SKIPPED
NOT_EXECUTABLE
CANCELLED
```

### Verification Status

```text
NOT_REQUIRED
PENDING
PASSED
FAILED
UNAVAILABLE
```

### Plan Status

```text
DRAFT
VALIDATED
READY
RUNNING
PARTIALLY_COMPLETED
COMPLETED
FAILED
BLOCKED
```

V10.0 只允许：

```text
DRAFT → VALIDATED
```

或：

```text
DRAFT → INVALID
```

---

## 十一、Plan Evaluation & Recovery 边界

V10.3 之前只保留字段和规则，不实现自动重规划。

失败分类：

| 类型 | 示例 | 允许动作 |
| --- | --- | --- |
| DATA_MISSING | 缺少上期权益 | 标记 UNAVAILABLE，不得填 0 |
| TOOL_NOT_REGISTERED | selected_tool 不存在 | Plan 校验失败 |
| TOOL_FAILED | MetricEngine 异常 | 记录失败，禁止伪结论 |
| VERIFICATION_FAILED | 独立验证不一致 | 阻断 Finding |
| PLAN_INCOMPLETE | 缺少依赖或验证节点 | 重新规划 |
| COMPANY_NOT_RESOLVED | 公司名无法解析 | 要求澄清或失败 |

最大重规划次数默认：

```text
2
```

重规划只允许：

```text
补依赖
更换已注册工具
缩小数据范围
请求澄清
```

不允许：

```text
删除验证
伪造 UNAVAILABLE 数据
降低 Golden 标准
```

---

## 十二、与既有 Runtime 的关系

V10 不重写金融 Runtime，而是新增 Agent 层。

| 层 | 所属版本 | 职责 |
| --- | --- | --- |
| Agent Controller / Planner | V10 | 理解目标、拆任务、选工具、管理状态 |
| Semantic Resolver | V10.1 | 公司、期间、指标、口径解析 |
| Financial Workflow | V9.3 | 计算、验证、Evidence、Finding、Report |
| Financial Data Layer | V9.3 | 真实数据、来源、期间、单位 |
| Formula Golden | V9.3 | 指标正确性回归 |
| Evaluation Layer | V8.5 / V9 | Golden 回归、评估 |

禁止事项：

1. Planner 不得重复实现 MetricEngine。
2. Planner 不得绕过 Formula Golden。
3. Agent 不得直接把 LLM 输出作为财务数字。
4. Agent 不得为了通过评估删除 UNAVAILABLE 或 FAILED 数据。
5. V10 不得引入实时行情、估值、投资建议、多 Agent 自由协作。

---

## 十三、V10.0 实施清单

### 模块

```text
langgraph_langchain/runtime/financial_agent/
├── __init__.py
├── models.py
├── tools.py
├── understanding.py
├── planner.py
└── tests support fixtures
```

### 最小类

```python
FinancialToolSpec
FINANCIAL_TOOL_REGISTRY

FinancialTaskUnderstanding
FinancialPlanTask
FinancialPlan

FinancialTaskUnderstandingBuilder
FinancialTaskPlanner
```

### 测试

```text
tests/financial_agent/test_task_understanding.py
tests/financial_agent/test_financial_planner.py
tests/financial_agent/test_tool_registry.py
tests/financial_agent/test_plan_validation.py
```

---

## 十四、V10.0 验收标准

### 1. Task Understanding

至少 20 条中文 / 英文测试问题必须生成合法 `FinancialTaskUnderstanding`：

| 问题类型 | 最低数量 |
| --- | --- |
| 收入分析 | 3 |
| 利润分析 | 2 |
| 盈利能力 | 3 |
| 现金流 | 3 |
| 偿债能力 | 2 |
| 营运效率 | 2 |
| 风险分析 | 2 |
| 同业比较 | 2 |
| 综合分析 | 1 |

### 2. Plan 校验

100% 的合法 Task Understanding 必须生成满足依赖关系的 Plan。

校验必须拒绝：

```text
循环依赖
未知依赖
未注册工具
未注册指标
缺少验证节点
报告任务缺少 Finding 依赖
```

### 3. Tool Mapping

所有可执行任务必须映射到 `FINANCIAL_TOOL_REGISTRY`。

未注册工具必须导致：

```text
Plan status = INVALID
task execution_status = NOT_EXECUTABLE
```

### 4. V9.3 不退化

```bash
PYTHONPATH=. pytest tests/financial -q
PYTHONPATH=. pytest -q
```

要求：

```text
tests/financial：全部通过
全量测试：0 failed
Formula Golden：153 / 153 passed
```

### 5. Schema / Provenance

Plan JSON 必须包含：

```text
plan_id
query_id
version
status
tasks
task_id
task_type
dependencies
required_metrics
required_data
selected_tool
tool_version
execution_status
verification_status
output_refs
failure_policy
```

---

## 十五、V10.0 非目标

V10.0 明确不做：

```text
❌ LLM Planning
❌ 真实执行金融 Workflow
❌ 自动重规划
❌ Insight Engine
❌ 自然语言解释优化
❌ Vector DB
❌ 多 Agent 框架
❌ 实时行情
❌ 估值 / DCF / PE / PB
❌ 自动投资建议
❌ Skill Evolution / GEPA
```

---

## 十六、后续版本路线

| 版本 | 目标 | 关键验收 |
| --- | --- | --- |
| V10.0 | Task Understanding + Planner | 20 条问题生成合法 Plan |
| V10.1 | Semantic Resolver | 公司 / 期间 / 指标 / 数据覆盖完整解析 |
| V10.2 | Runtime Integration | Planner 驱动 V9.3 Workflow 执行 |
| V10.3 | Insight + Recovery | Evidence-backed Insight、失败分类、有限重规划 |

V11 再进入 Financial Research Agent：

```text
多来源披露
年报 / 公告文本
行业研究
文本事实与财务事实交叉验证
完整研究报告
```

V12 再抽象为 Generalized Data Analysis Agent。

---

## 十七、V10.0 验收结论模板

完成后必须记录：

```text
V10.0 状态：FINAL / NOT FINAL
Task Understanding 测试：20 / 20 passed
Plan 校验测试：N / N passed
Tool Registry 测试：N / N passed
Formula Golden：153 / 153 passed
Financial tests：全部通过
Full regression：全部通过
GitHub Actions：success
```

只有以上全部满足，才能进入 V10.1。

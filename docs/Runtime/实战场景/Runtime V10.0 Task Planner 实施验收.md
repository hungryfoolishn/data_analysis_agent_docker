# Runtime V10.0 Task Understanding + Planner 实施验收

## 结论

V10.0 第一阶段已完成：金融 Agent 现在可以把自然语言金融问题转换为结构化 Task Understanding，并生成通过静态校验的 Financial Task Planner DAG。本阶段不执行金融 Workflow，不重复实现指标公式，也不引入 LLM、估值、实时行情、多 Agent 或 Skill Evolution。

## 交付内容

### 1. 新增 Agent Planning Layer

新增包：

```text
langgraph_langchain/runtime/financial_agent/
```

核心模块：

| 模块 | 职责 |
| --- | --- |
| `models.py` | Task Understanding、Plan、Task、状态与失败策略模型 |
| `tools.py` | 显式 Tool Registry 与 Task → Tool 映射 |
| `understanding.py` | 确定性 Task Understanding Builder |
| `planner.py` | Financial Task Planner 与 Plan 静态校验 |

### 2. Financial Task Understanding

支持解析：

| 输入信号 | 输出 |
| --- | --- |
| 公司名 / 股票代码 / 常用英文别名 | `companies` |
| `2021-2025`、`2021至2025`、`2021—2025`、单年 | `period_range` |
| `过去五年` / `近五年` | 基于参考年份的年度区间 |
| 收入、利润、盈利能力、现金流、偿债、营运、风险、比较、综合分析 | `objectives` |
| objectives 与既有指标注册表 | `required_metrics` |
| 两个公司 + 比较语义 | `comparison_enabled` |

Task Understanding 包含：

```text
query_id
raw_question
task_type
companies
period_range
objectives
required_metrics
comparison_enabled
ambiguities
missing_information
confidence
```

### 3. Financial Task Planner

示例输入：

```text
分析贵州茅台 2021—2025 年的收入、利润、盈利能力和现金流变化，并与五粮液比较。
```

生成 11 个任务：

```text
1. Resolve Companies
2. Resolve Periods
3. Validate Data Coverage
4. Revenue Trend
5. Net Profit Trend
6. Profitability Analysis
7. Cash Flow Analysis
8. Peer Comparison
9. Verify Calculations
10. Build Evidence-backed Findings
11. Generate Report
```

每个 Task 均包含：

```text
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

### 4. Tool Registry

显式注册以下工具：

```text
company_resolver
period_resolver
data_coverage_checker
financial_metric_engine
risk_detector
comparison_engine
verification_engine
finding_engine
report_builder
```

其中：

- `financial_metric_engine` 映射到 V9.3 既有 MetricEngine。
- 数值分析任务均标记 `verification_status = REQUIRED`。
- 所有 Task 都必须映射到已注册工具。
- 不注册的能力不会进入 Plan。

### 5. Plan 静态校验

Planner 会拒绝：

```text
❌ 空 Plan
❌ 重复 task_id
❌ 未知 dependency
❌ 依赖循环
❌ 未注册 tool
❌ tool 与 task_type 不匹配
❌ 未注册 metric
❌ 数值任务缺少 verification
❌ 缺少 verification task
❌ 缺少 report task
❌ report 不依赖 findings
```

## 测试验收

### Task Understanding

新增测试：

```text
tests/financial_agent/test_task_understanding.py
```

覆盖：

```text
20 条参数化任务理解问题
综合比较示例
相对期间解析
公司对象与股票代码解析
缺失公司 / 期间记录
query_id 稳定性
```

### Tool Registry

新增测试：

```text
tests/financial_agent/test_tool_registry.py
```

覆盖：

```text
9 个工具注册
14 种 Plan Task Type 映射
工具 schema
确定性标记
MetricEngine verification 要求
verification / report tool 不重复验证
```

### Planner / Validation

新增测试：

```text
tests/financial_agent/test_financial_planner.py
tests/financial_agent/test_plan_validation.py
```

覆盖：

```text
11 任务 DAG
依赖关系
工具映射
数值任务 verification
report 前置 verification / findings
required_metrics / required_data
稳定 plan_id
Plan schema 字段
未知 dependency
重复 task_id
依赖循环
未注册 tool
tool-task type 不匹配
未注册 metric
report 契约
```

## 回归结果

### V10.0 专项测试

```bash
PYTHONPATH=. pytest tests/financial_agent -q
```

结果：

```text
47 passed
```

### V9.3 + V10.0 联合测试

```bash
PYTHONPATH=. pytest tests/financial tests/financial_agent -q
```

结果：

```text
121 passed
```

### 全量回归

```bash
PYTHONPATH=. pytest -q
```

结果：

```text
799 passed, 26 warnings, 4 subtests passed
```

## V9.3 兼容性

本阶段没有修改 V9.3 Financial Runtime 的：

```text
MetricEngine
Formula Golden
Verification
Evidence
Finding
Report
```

因此 Formula Golden 的既有回归继续有效。

## 边界与不做事项

V10.0 明确不做：

```text
❌ LLM Planning
❌ 执行 Financial Workflow
❌ 自动重规划
❌ Insight Engine
❌ Vector DB
❌ 多 Agent 框架
❌ 实时行情
❌ 估值 / DCF / PE / PB
❌ 自动投资建议
❌ Skill Evolution / GEPA
```

## 下一步

进入 V10.1：Metric / Data / Period Resolver。

V10.1 将补齐：

```text
公司解析与别名治理
期间解析与数据集可用期间对齐
指标口径解析
数据覆盖检查
缺失字段 / 上期数据 / 比较口径的结构化诊断
```

## 验收结论

```text
V10.0 状态：FINAL
Task Understanding 测试：20 / 20 passed
Tool Registry 测试：全部通过
Planner / Validation 测试：全部通过
V9.3 Financial Tests：全部通过
Full regression：799 passed
```

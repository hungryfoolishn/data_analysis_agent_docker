# Runtime V10.0.1 Task Understanding & Planner Hardening 实施验收

## 结论

V10.0.1 已完成。本次是小版本质量加固，不改变 V10.0 架构，不进入 V10.1 Semantic Resolver，不引入 LLM Planner、自动重规划或 Skill Evolution。

## 修复内容

### 1. Comprehensive Objective Merge

修复前，`comprehensive` 目标会覆盖用户显式提出的其他目标。

修复后采用去重并集：

```text
comprehensive 基础目标：
- revenue_trend
- profit_trend
- profitability
- cashflow

显式目标全部保留：
- solvency
- operating_efficiency
- risk
- peer_comparison
```

示例：

```text
全面分析贵州茅台 2021-2025 年的经营情况，重点关注偿债能力、营运效率和风险。
```

现在会保留：

```text
revenue_trend
profit_trend
profitability
cashflow
solvency
operating_efficiency
risk
```

### 2. Planning Readiness

`FinancialTaskUnderstanding` 与 `FinancialPlan` 新增：

```text
planning_readiness
planning_diagnostics
```

`PlanningReadiness`：

| 状态 | 含义 |
| --- | --- |
| `READY` | 关键信息完整，可进入执行规划 |
| `NEEDS_CLARIFICATION` | 缺少公司、期间或分析目标，需要澄清 |
| `NOT_EXECUTABLE` | 问题为空或静态校验失败，不可执行 |

新增诊断码：

```text
EMPTY_QUESTION
UNRESOLVED_COMPANY
UNRESOLVED_PERIOD
NO_ANALYSIS_OBJECTIVE
```

Planner 处理规则：

| Task Understanding 状态 | Plan 状态 |
| --- | --- |
| `READY` | 正常生成并通过/失败静态校验 |
| `NEEDS_CLARIFICATION` | `BLOCKED`，不生成可执行分析 Plan |
| `NOT_EXECUTABLE` | `FAILED`，不生成可执行分析 Plan |

因此，只有公司和年份、没有分析目标时，不会产生误标为 `VALIDATED` 的空分析计划。

### 3. Plan DAG Validation

`validate_plan()` 从节点存在性检查升级为 DAG 完整性检查。

新增校验：

```text
1. 拒绝自依赖。
2. 拒绝重复 dependency。
3. 必须至少有一个分析任务。
4. 所有数值分析任务必须被 Verify Calculations 直接覆盖。
5. Findings 必须且只能依赖 Verification。
6. Report 必须且只能依赖 Findings。
7. 所有任务必须沿依赖链可达 Report。
8. 孤立任务逐个输出诊断。
```

保留既有校验：

```text
- task_id 唯一
- dependency 必须存在
- 无循环依赖
- selected_tool 必须注册
- tool 必须支持 task_type
- metric 必须注册
- numeric task 必须要求 verification
- Verification / Findings / Report 必须存在且唯一
```

`validate_plan()` 保持纯校验接口：返回错误列表，不直接修改 Plan 状态。`build()` 负责状态转换。

## 测试结果

### V10.0.1 / V10.0 专项测试

```bash
PYTHONPATH=. pytest tests/financial_agent -q
```

```text
60 passed
```

新增覆盖：

1. Comprehensive + Solvency 不丢失偿债目标。
2. Comprehensive + Risk 不丢失风险目标。
3. Comprehensive + Operating Efficiency 不丢失营运目标。
4. Comprehensive + Peer Comparison 不丢失比较目标。
5. 只有公司和年份、没有分析目标时，Plan 为 `BLOCKED`。
6. 公司无法解析时输出 `UNRESOLVED_COMPANY`。
7. 期间无法解析时输出 `UNRESOLVED_PERIOD`。
8. 空问题时输出 `NOT_EXECUTABLE`。
9. Verification 缺少数值任务依赖时校验失败。
10. Findings 缺少 Verification 依赖时校验失败。
11. Report 缺少 Findings 依赖时校验失败。
12. 孤立分析任务被识别。
13. 重复 dependency 和 self-dependency 被识别。
14. 正常 Plan 的 `planning_readiness = READY`。

### V9.3 + V10.0 联合测试

```bash
PYTHONPATH=. pytest tests/financial tests/financial_agent -q
```

```text
134 passed
```

### 全量回归

```bash
PYTHONPATH=. pytest -q
```

```text
812 passed, 26 warnings, 4 subtests passed
```

## 兼容性

未修改：

```text
V9.3 Financial Runtime
Formula Golden
Financial Verification
Evidence / Finding / Report
```

V10.0 Plan Schema 新增字段均有默认值：

```text
planning_readiness
planning_diagnostics
```

因此不会破坏既有 V10.0 调用方。

## 剩余 P2 技术债

以下问题不在本次范围内，留到后续版本：

1. Company alias 表仍硬编码在 Understanding Builder，后续迁移到统一 Company Registry。
2. `reference_year` 当前由 Builder 显式注入；后续应从数据集可用期间自动推导并校验。
3. 股票代码匹配仍可进一步做边界匹配。
4. Plan ID 可引入 planner/schema/tool registry 版本指纹。
5. Tool Registry 目前是能力声明，不是真实执行器绑定；V10.2 必须验证真实调用绑定。

## 验收结论

```text
V10.0.1 状态：FINAL
新增回归测试：全部通过
既有 20 条 Task Understanding 测试：全部通过
Financial + Financial Agent：134 passed
Full regression：812 passed
```

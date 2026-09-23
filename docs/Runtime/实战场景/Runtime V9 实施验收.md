# Data Analysis Agent Runtime V9 实施验收

## 1. 版本与结论

- 版本：Runtime V9 Phase 1 / Financial Analysis Domainization
- 日期：2026-09-23
- 结论：**通过本地全量回归**
- 本阶段定位：金融数据语义层、金融指标层、金融分析 Workflow、可验证 Financial Observation / Calculation / Risk Signal、Financial Golden Evaluation。
- 明确暂缓：估值、实时行情、自动交易、股票推荐、Skill Evolution / GEPA。

## 2. 已交付范围

### 2.1 金融数据层

新增模块：

```text
langgraph_langchain/runtime/financial/
├── models.py
├── metrics.py
├── data_service.py
├── classifier.py
├── risk_detector.py
├── workflow.py
├── evaluation.py
└── __init__.py
```

数据模型覆盖：

- `Company`
- `IncomeStatement`
- `BalanceSheetStatement`
- `CashFlowStatement`
- `FinancialIndicator`
- `FinancialDataSource`
- `FinancialQuery`
- `FinancialObservation`
- `FinancialCalculation`
- `FinancialRiskSignal`
- `FinancialAnalysisResult`

`FinancialDataService` 支持：

- 标准 CSV 目录加载
- 公司解析：`company_id` / `company_name` / `stock_code`
- 公司可用报告期查询
- 三张报表读取
- 上一报告期解析

### 2.2 金融指标语义层

已实现 `FinancialMetricRegistry`，覆盖：

- 报表基础指标
- 成长能力：收入、净利润、营业利润、EPS 增长
- 盈利能力：毛利率、营业利润率、净利率、ROE、ROA
- 偿债能力：资产负债率、流动比率、速动比率
- 营运能力：应收账款周转、存货周转、总资产周转
- 现金流：经营现金流、自由现金流、经营现金流 / 净利润

每个指标保留：

- `metric_id`
- `name`
- `category`
- `formula`
- `unit`
- `source_fields`
- `calculation_type`

当前指标数量：**39 个**。

### 2.3 金融任务分类

`FinancialTaskClassifier` 支持 12 类 V9 金融任务：

```text
COMPANY_OVERVIEW
FINANCIAL_TREND
REVENUE_ANALYSIS
PROFIT_ANALYSIS
PROFITABILITY_ANALYSIS
CASHFLOW_ANALYSIS
SOLVENCY_ANALYSIS
OPERATING_ANALYSIS
PEER_COMPARISON
ANOMALY_ANALYSIS
RISK_ANALYSIS
COMPREHENSIVE_ANALYSIS
```

支持从问题中提取：

- 已知公司名
- 年份区间
- 金融任务类型

### 2.4 Financial Analysis Workflow

`FinancialAnalysisWorkflow` 提供确定性金融分析路径：

```text
Natural Language Question
  ↓
FinancialTaskClassifier
  ↓
FinancialQuery
  ↓
FinancialDataService
  ↓
FinancialMetricRegistry.calculate_metric()
  ↓
FinancialObservation
  ↓
FinancialCalculation
  ↓
FinancialRiskDetector
  ↓
FinancialAnalysisResult + Report Markdown
```

输出包含：

- Fact 型 Observation
- Formula / Inputs / Result 型 Calculation
- Rule-based Risk Signal
- Markdown 报告
- 明确“不构成投资建议”的边界说明

### 2.5 风险信号

`FinancialRiskDetector` 先实现规则型风险：

- 营业收入负增长
- 净利润负增长
- 资产负债率超过 70%
- 经营现金流 / 净利润低于 50%

风险输出保留：

- 公司
- 期间
- 指标
- 数值
- 严重程度
- 风险说明

### 2.6 Financial Golden Dataset

新增确定性金融回归数据：

```text
tests/financial/data/financial/
├── companies.csv
├── income_statement.csv
├── balance_sheet.csv
├── cash_flow.csv
└── financial_dataset.json
```

数据规模：

- 公司数：**20**
- 行业数：**5**
- 报告期：**2021–2025，共 5 年**
- 数据性质：**确定性合成回归数据，不代表真实上市公司财务数据**

数据集通过 `financial_dataset.json` 标注：

- dataset version
- dataset kind
- source note
- file list
- company count
- periods
- industry count

### 2.7 50+ Financial Golden Cases

新增文件：

```text
tests/financial/golden_cases/financial_golden_cases.json
```

Case 数量：**55**

分布：

```text
基础指标      5
趋势分析      8
收入分析      5
利润分析      5
盈利能力      5
现金流        5
偿债能力      4
营运能力      3
同业比较      7
异常分析      2
综合分析      1
风险分析      5
```

Golden Case 校验内容：

- Case ID 唯一
- 数据集文件存在
- 数据集 canonical SHA256 一致
- 期望指标、期间、公司上下文一致
- 必要 Evidence
- 必要 Finding
- 必要报告关键词
- 多个 critical case

### 2.8 Financial Evaluation Adapter

新增：

```python
FinancialEvaluationAdapter
```

职责：

- 复用 Runtime V8.5 `GoldenEvaluationRunner`
- 将 `FinancialAnalysisResult` 转换为 `GoldenCandidateResult`
- 将 `FinancialObservation` 转换为 `MetricAnswer`
- 保留公司、期间、指标上下文
- 传递 Calculation Lineage 数量
- 输出 stable skill identity：

```text
skill_id    = runtime_financial_workflow
skill_name  = financial-analysis-workflow
skill_version = 9.0.0
skill_hash  = 由金融指标公式集合派生
```

## 3. 测试结果

### 3.1 V9 专项测试

```bash
python -X utf8 -m pytest tests/financial -q
```

结果：

```text
8 passed
```

覆盖：

- 指标注册与公式
- 任务分类
- Workflow 指标计算
- Calculation Lineage
- 风险信号
- 20 家公司 / 5 年 / 5 行业
- 55 条 Golden Cases
- `GoldenCaseLoader.validate() == []`
- `FinancialEvaluationAdapter` 全量通过
- stable skill identity 聚合

### 3.2 本地全量回归

```bash
python -X utf8 -m pytest -q
```

结果：

```text
686 passed, 0 failed
```

该结果为包含 V9 Golden Cases 与 Financial Evaluation Adapter 的最终提交前全量结果。

## 4. 验收检查表

| 项目 | 状态 |
| --- | --- |
| 金融数据 Schema | ✅ |
| 三表数据模型 | ✅ |
| Financial Data Service | ✅ |
| 金融指标 Registry | ✅ 39 个指标 |
| 金融任务分类 | ✅ 12 类 |
| 确定性金融 Workflow | ✅ |
| Financial Observation | ✅ |
| Financial Calculation Lineage | ✅ |
| Rule-based Risk Detector | ✅ |
| Financial Report | ✅ Phase 1 |
| ≥20 家公司 | ✅ 20 |
| ≥5 年历史数据 | ✅ 5 年 |
| ≥3 个行业 | ✅ 5 |
| ≥30 个金融指标 | ✅ 39 |
| ≥50 个 Golden Cases | ✅ 55 |
| Golden Dataset hash 校验 | ✅ |
| Financial Evaluation Adapter | ✅ |
| V8.5 Golden Evaluation 复用 | ✅ |
| 全量回归 | ✅ |
| 不做估值 / 行情 / 推荐边界 | ✅ |

## 5. 边界与后续

### 5.1 本阶段不做

- 不做估值模型
- 不接入实时行情
- 不做自动交易
- 不做股票推荐
- 不做 Skill Evolution / GEPA
- 不用 LLM 自由计算金融指标

### 5.2 后续建议

1. 接入真实披露数据管道，替换合成数据。
2. 扩展 `FinancialDataSource` 到官方公告 / 年报 / XBRL 溯源。
3. 增加 Financial UI 和金融任务入口。
4. 将 Financial Workflow 接入主 Runtime 的 Evidence / Finding / Verification 事件流。
5. 在 V10 中增加估值指标与估值工作流。
6. 在 V11 中扩展为多步骤金融研究 Agent。

## 6. Git 提交建议

```bash
git add langgraph_langchain/runtime/financial tests/financial scripts/generate_financial_golden.py docs/Runtime/实战场景
git commit -m "feat(runtime-v9): add financial analysis domain and golden evaluation"
git push origin master
```

不要提交：

```text
.claude/settings.local.json
runtime_v5_remote_sync.sh
workspace/
temp_uploads/
```

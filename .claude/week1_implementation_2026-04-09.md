# Week 1 优化实施记录：分析可信度底座

**实施日期**：2026-04-09  
**优化目标**：建立结论-证据绑定机制，提升分析可信度

---

## 已完成的改进

### 1. 定义结构化 findings 数据结构 ✅

**位置**：`langgraph_langchain/schemas.py`

已有完善的数据结构定义：
- `Finding` - 结构化结论，包含 statement、evidence、confidence_level、evidence_level
- `EvidenceItem` - 证据项，包含 evidence_text、source_fields、source_artifacts、time_window、group_dimension、filters
- `MetricDefinition` - 指标定义，包含 metric_name、definition_text、time_window、dedup_rule、denominator、semantic_uncertainty
- `AnalysisAssumption` - 分析假设，包含 assumption_text、risk_level

---

### 2. 在 _Session 中添加 findings 收集 ✅

**位置**：`langgraph_langchain/langgraph_agent.py:901-908`

在 `_Session` 类中添加了三个列表：
```python
self.findings: List[Finding] = []
self.metric_definitions: List[MetricDefinition] = []
self.assumptions: List[AnalysisAssumption] = []
```

---

### 3. 添加三个新工具 ✅

**位置**：`langgraph_langchain/langgraph_agent.py:1560-1681`

#### 3.1 `record_finding` 工具

让 agent 记录结构化的 findings：
```python
@tool
def record_finding(
    statement: str,
    evidence_text: str,
    confidence_level: str = "medium",
    evidence_level: str = None,
    hypothesis_flag: bool = False,
    source_fields: List[str] = None,
    source_artifacts: List[str] = None,
    time_window: str = None,
    group_dimension: str = None,
    filters: List[str] = None,
) -> str:
```

**功能**：
- 记录核心结论陈述
- 绑定支撑证据
- 标注置信度和证据等级
- 记录数据来源（字段、图表、时间窗口、分组维度、过滤条件）
- 区分观察 vs 假设

#### 3.2 `declare_metric` 工具

让 agent 显式声明指标定义：
```python
@tool
def declare_metric(
    metric_name: str,
    definition_text: str,
    time_window: str = None,
    dedup_rule: str = None,
    denominator: str = None,
    semantic_uncertainty: str = None,
) -> str:
```

**功能**：
- 明确指标定义
- 声明时间窗口
- 说明去重规则
- 定义分母（对于比率类指标）
- 标注语义不确定性

#### 3.3 `declare_assumption` 工具

让 agent 显式声明分析假设：
```python
@tool
def declare_assumption(assumption_text: str, risk_level: str = "medium") -> str:
```

**功能**：
- 记录数据假设
- 记录业务逻辑假设
- 标注假设风险等级

---

### 4. 更新 _SYSTEM_PROMPT ✅

**位置**：`langgraph_langchain/langgraph_agent.py:181-202`

#### 4.1 更新工作流程

要求 agent：
1. 调用 `load_data`
2. 调用 `eda_profile`
3. **使用 `declare_metric` 显式定义关键指标**
4. **使用 `declare_assumption` 记录假设**
5. 制定分析计划
6. 使用 `python_repl` 分步分析
7. **每个重要发现后调用 `record_finding` 记录结构化结论**
8. 保存图表
9. 调用 `finish_report` 生成报告

#### 4.2 更新 finish_report 要求

**位置**：`langgraph_langchain/langgraph_agent.py:267-301`

新增要求：
- 调用 `finish_report` 前必须：
  - 使用 `declare_metric` 定义所有关键指标
  - 使用 `declare_assumption` 记录假设
  - 使用 `record_finding` 记录至少 2 个重要发现
- 报告必须包含 **Data Context / 数据说明** 章节（新增，必需）
- Data Context 必须包含：
  - 时间范围
  - 关键指标定义
  - 去重规则
  - 比率的分母定义
  - 关键假设
  - 语义不确定性或字段解释说明

---

### 5. 增强 finish_report validator ✅

**位置**：`langgraph_langchain/langgraph_agent.py:1720-1820`

#### 5.1 检查 findings 数量

```python
if len(session.findings) < 2:
    return "REPORT REJECTED. Only {len(session.findings)} findings recorded. Use record_finding to document at least 2 key insights with concrete evidence."
```

#### 5.2 检查 Data Context 章节

```python
if not present_sections["Data Context"]:
    issues.append("missing required 'Data Context' or '数据说明' section")
```

#### 5.3 验证 Data Context 内容

检查是否包含：
- 时间范围（time_range）
- 指标定义（metric_definition）
- 去重规则（dedup_rule）

#### 5.4 检查 metric 声明

```python
if len(session.metric_definitions) == 0:
    issues.append("no metrics were declared using declare_metric")
```

---

### 6. 保存结构化 findings 到 JSON ✅

**位置**：`langgraph_langchain/langgraph_agent.py:2020-2033`

在报告保存后，自动生成 `analysis_findings.json`：
```python
findings_data = {
    "findings": [f.model_dump() for f in session.findings],
    "metric_definitions": [m.model_dump() for m in session.metric_definitions],
    "assumptions": [a.model_dump() for a in session.assumptions],
}
findings_path = session.workspace_dir / "analysis_findings.json"
findings_path.write_text(json.dumps(findings_data, ensure_ascii=False, indent=2), encoding="utf-8")
```

**输出文件**：
- `data_analysis_report.md` - 人类可读的 markdown 报告
- `analysis_findings.json` - 机器可读的结构化 findings

---

### 7. 创建测试文件 ✅

**位置**：`langgraph_langchain/test_findings.py`

测试覆盖：
- Finding 结构正确性
- MetricDefinition 结构正确性
- AnalysisAssumption 结构正确性
- Finding 序列化

---

## 预期效果

### 1. 降低"伪洞察"概率

- Agent 必须显式记录 findings，不能只写流畅的文字
- 每个 finding 必须绑定证据
- 必须标注置信度和证据等级

### 2. 业务口径显式化

- 指标定义不再隐式存在于推理中
- 时间窗口、去重规则、分母定义都必须显式声明
- 语义不确定性必须标注

### 3. 假设与结论分离

- 假设必须用 `declare_assumption` 显式声明
- Finding 可以标记为 `hypothesis_flag=True`
- 证据等级分为 A/B/C 三级

### 4. 可追溯性

- 每个结论都能追溯到：
  - 使用了哪些字段（source_fields）
  - 哪些图表支撑（source_artifacts）
  - 什么时间窗口（time_window）
  - 什么分组维度（group_dimension）
  - 什么过滤条件（filters）

### 5. 机器可读

- `analysis_findings.json` 提供结构化输出
- 可用于后续分析、审计、可视化
- 可用于构建 findings 数据库

---

## 使用示例

### Agent 工作流程示例

```python
# 1. 加载数据
load_data()

# 2. EDA
eda_profile()

# 3. 声明指标
declare_metric(
    metric_name="revenue",
    definition_text="Sum of order_amount for status='completed' orders",
    time_window="2025-Q3 (2025-07-01 to 2025-09-30)",
    dedup_rule="Deduplicated by order_id",
    semantic_uncertainty="Assuming 'status=completed' means paid orders"
)

# 4. 声明假设
declare_assumption(
    assumption_text="Assuming 'region' field has no missing values",
    risk_level="low"
)

# 5. 分析
python_repl("...")

# 6. 记录发现
record_finding(
    statement="North region accounts for 42% of Q3 revenue",
    evidence_text="North region revenue is 420,000 out of total 1,000,000",
    confidence_level="high",
    evidence_level="A",
    hypothesis_flag=False,
    source_fields=["region", "revenue", "order_amount"],
    source_artifacts=["revenue_by_region.png"],
    time_window="2025-Q3",
    group_dimension="region",
    filters=["status='completed'", "revenue > 0"]
)

# 7. 生成报告
finish_report("""
## 摘要
...

## 数据说明
- **时间范围**：2025-Q3 (2025-07-01 to 2025-09-30)
- **指标定义**：revenue = 订单金额总和（status='completed'）
- **去重规则**：按 order_id 去重
- **关键假设**：假设 'region' 字段无缺失值

## 关键发现
- North 区域占 Q3 revenue 的 42%（420,000 / 1,000,000）
...
""")
```

---

## 后续优化方向

### 短期（Week 2）
- 添加 findings 之间的关联关系
- 增强证据等级的自动判断
- 添加 findings 的时间序列追踪

### 中期（Week 3-4）
- 构建 findings 数据库
- 实现 findings 的可视化展示
- 添加 findings 的版本控制

### 长期
- 基于 findings 构建知识图谱
- 实现跨 session 的 findings 复用
- 构建行业化的 findings 模板

---

## 测试建议

### 单元测试
```bash
cd /python/pragrams/data_analysis_agent
pytest langgraph_langchain/test_findings.py -v
```

### 集成测试
使用一个简单数据集测试完整流程：
1. 上传数据
2. 运行分析
3. 检查是否生成 `analysis_findings.json`
4. 验证 findings 结构完整性
5. 验证报告包含 Data Context 章节

---

## 注意事项

1. **向后兼容**：旧的分析流程仍然可以工作，但会被 validator 拒绝（因为缺少 findings）
2. **渐进式采用**：可以先在新任务中使用，逐步迁移旧任务
3. **文档更新**：需要更新 API 文档和用户指南
4. **前端适配**：前端需要适配新的 `analysis_findings.json` 文件展示

---

## 相关文件

- `langgraph_langchain/schemas.py` - 数据结构定义
- `langgraph_langchain/langgraph_agent.py` - Agent 实现
- `langgraph_langchain/test_findings.py` - 测试文件
- `.claude/optimization_summary_2026-04-09.md` - 总体优化路线图

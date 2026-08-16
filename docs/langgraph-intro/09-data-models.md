# 09 - 数据模型与 Schema 详解

> 本文深入解析项目中的 Pydantic 数据模型体系：Finding、Evidence、StageResult、FailureInfo 等核心模型的设计和使用。

---

## 9.1 为什么用 Pydantic？

### Pydantic 的优势

```python
from pydantic import BaseModel, Field

class Finding(BaseModel):
    finding_id: str = Field(..., description="结论唯一标识")
    statement: str = Field(..., description="核心结论陈述")
    confidence_level: Literal["low", "medium", "high"] = "medium"

# 自动验证
f = Finding(finding_id="F001", statement="华东区最高")  # ✅
f = Finding(statement="华东区最高")  # ❌ 缺少 finding_id
```

Pydantic 提供：
- **类型验证**：自动检查字段类型
- **默认值**：可选字段有默认值
- **JSON 序列化/反序列化**：`model_dump()` 和 `model_validate()`
- **文档生成**：Field 的 description 生成 JSON Schema
- **不可变性**：创建后不可修改（可选）

---

## 9.2 模型体系总览

```
Finding (分析发现)                      ← 核心模型
  ├── finding_id: "F001"
  ├── statement: "华东区 revenue 占总量 42%"
  ├── evidence: List[EvidenceItem]      ← 证据模型
  ├── confidence_level: "high"
  ├── evidence_level: "A" / "B" / "C"
  ├── hypothesis_flag: False
  ├── category: "comparison"
  ├── metric_definitions: List[MetricDefinition]  ← 指标模型
  ├── assumptions: List[AnalysisAssumption]       ← 假设模型
  └── trace_id / span_id / source_tool           ← 追踪字段

EvidenceItem (证据)                     ← 支撑发现
  ├── evidence_text: "华东区 total=420K..."
  ├── source_fields: ["region", "revenue"]
  ├── source_artifacts: ["chart1.png"]
  ├── time_window: "2025-Q3"
  ├── group_dimension: "region"
  └── stats: {"north_revenue": 420000}

MetricDefinition (指标定义)              ← 分析口径
  ├── metric_name: "revenue"
  ├── definition_text: "SUM(order_amount)"
  ├── denominator: "N/A"
  └── dedup_rule: "按 order_id 去重"

AnalysisAssumption (假设)               ← 分析前提
  ├── assumption_text: "假设无重复订单"
  └── risk_level: "medium"

StageResult (阶段结果)                  ← 阶段追踪
  ├── stage: "deep_dive"
  ├── status: "completed"
  └── failure: FailureInfo (如果失败)

FailureInfo (失败信息)                  ← 错误详情
  ├── code: "python_execution_error"
  ├── message: "具体错误信息"
  ├── retryable: True
  └── recovery_action: "retry_narrower_scope"
```

---

## 9.3 Finding 模型详解

```python
# schemas.py:143-164
class Finding(BaseModel):
    # ━━━ 核心字段 ━━━
    finding_id: str = Field(
        ...,                           # 必填
        description="结论唯一标识，如 F001"
    )
    statement: str = Field(
        ...,
        description="核心结论陈述"
    )

    # ━━━ 证据和支撑 ━━━
    evidence: List[EvidenceItem] = Field(
        default_factory=list,
        description="支撑结论的证据列表"
    )
    assumptions: List[AnalysisAssumption] = Field(
        default_factory=list,
        description="相关假设"
    )
    metric_definitions: List[MetricDefinition] = Field(
        default_factory=list,
        description="相关指标定义"
    )

    # ━━━ 置信度和证据等级 ━━━
    confidence_level: Literal["low", "medium", "high"] = Field(
        default="medium",
        description="对结论的置信程度"
    )
    evidence_level: Literal["A", "B", "C"] = Field(
        default="B",
        description="证据等级：A=事实描述，B=相关线索，C=因果判断"
    )
    hypothesis_flag: bool = Field(
        False,
        description="是否属于假设性结论"
    )

    # ━━━ 分类 ━━━
    category: Optional[str] = Field(
        default=None,
        description="结论类别：trend/anomaly/comparison/attribution..."
    )
    stats: Optional[Dict[str, Any]] = Field(
        default=None,
        description="关键统计数据"
    )
    calculation_method: Optional[str] = Field(
        default=None,
        description="计算方法"
    )

    # ━━━ 追踪字段 ━━━
    trace_id: Optional[str] = None
    span_id: Optional[str] = None
    source_tool: Optional[str] = None
    source_step: Optional[int] = None
    created_at: Optional[str] = None
```

### Finding 的创建过程

在 `record_finding` 工具中：

```python
# 1. 构建证据
evidence_item = EvidenceItem(
    evidence_text=evidence_text,
    source_fields=source_fields or [],
    source_artifacts=source_artifacts or [],
    time_window=time_window,
    group_dimension=group_dimension,
    filters=filters or [],
    stats=stats,
    calculation_method=calculation_method,
)

# 2. 构建 Finding
finding_id = f"F{len(session.findings) + 1:03d}"  # F001, F002, ...
finding = Finding(
    finding_id=finding_id,
    statement=statement,
    evidence=[evidence_item],
    confidence_level=confidence_level,
    evidence_level=evidence_level,
    hypothesis_flag=hypothesis_flag,
    category=category,
)

# 3. 验证
is_valid, errors = validate_rd_finding(finding)
is_valid, errors = validate_evidence_binding(finding, artifacts)

# 4. 存储
session.findings.append(finding)
```

---

## 9.4 EvidenceItem 模型详解

```python
# schemas.py:73-84
class EvidenceItem(BaseModel):
    evidence_text: str = Field(
        ...,
        description="支撑结论的证据文本"
    )
    source_fields: List[str] = Field(
        default_factory=list,
        description="证据涉及的字段"
        # 例: ["region", "revenue", "date"]
    )
    source_artifacts: List[str] = Field(
        default_factory=list,
        description="证据涉及的图表或文件"
        # 例: ["revenue_by_region.png", "time_trend.png"]
    )
    time_window: Optional[str] = Field(
        default=None,
        description="证据对应的时间窗口"
        # 例: "2025-Q3", "2025-07-01 to 2025-09-30"
    )
    group_dimension: Optional[str] = Field(
        default=None,
        description="证据涉及的分组维度"
        # 例: "region", "product_category"
    )
    filters: List[str] = Field(
        default_factory=list,
        description="证据使用的过滤条件"
        # 例: ["revenue > 0", "status = 'completed'"]
    )
    stats: Optional[dict] = Field(
        default=None,
        description="关键统计数据"
        # 例: {"north_revenue": 420000, "total_revenue": 1000000, "share": 0.42}
    )
    calculation_method: Optional[str] = Field(
        default=None,
        description="计算方法说明"
        # 例: "SUM(revenue) GROUP BY region"
    )
```

### 证据绑定验证

`evidence_binding.py` 检查证据是否正确引用了已有的图表和文件：

```python
# 证据绑定验证检查什么？
validate_evidence_binding(finding, available_artifacts)

# 1. source_artifacts 中引用的文件是否存在？
# 2. stats 中的数据是否合理？
# 3. evidence_text 是否包含具体数字？
```

---

## 9.5 FailureInfo 模型详解

```python
# schemas.py:167-174
class FailureInfo(BaseModel):
    code: FailureCode                    # 失败代码（枚举）
    message: str                         # 失败消息
    retryable: bool = False              # 是否可重试
    hint: Optional[str] = None           # 给用户的提示
    recovery_action: Optional[RecoveryAction] = None  # 恢复策略
    stage: Optional[AnalysisStage] = None  # 发生在哪个阶段
```

### FailureCode 枚举

```python
# schemas.py:10-26
FailureCode = Literal[
    "missing_data_file",             # 数据文件缺失
    "session_not_found",             # 会话不存在
    "session_workspace_missing",     # 工作目录缺失
    "session_expired",               # 会话过期
    "python_execution_error",        # Python 执行错误
    "max_steps_exceeded",            # 超过最大步数
    "report_rejected",               # 报告被拒绝
    "cancelled",                     # 用户取消
    "schema_understanding_failed",   # Schema 理解失败
    "field_semantic_unclear",        # 字段语义不明确
    "tool_execution_failed",         # 工具执行失败
    "reasoning_drift",               # 推理偏离
    "report_generation_failed",      # 报告生成失败
    "timeout",                       # 超时
    "session_interrupted",            # 会话中断
]
```

### RecoveryAction 枚举

```python
# schemas.py:33
RecoveryAction = Literal[
    "retry_same_scope",         # 同范围重试
    "retry_narrower_scope",     # 缩小范围重试
    "user_action_required",     # 需要用户操作
]
```

---

## 9.6 FinalReport 模型

```python
# schemas.py:184-189
class FinalReport(BaseModel):
    report_markdown: str                          # 报告 markdown 内容
    findings: List[Finding] = []                  # 引用的发现
    assumptions: List[AnalysisAssumption] = []    # 引用的假设
    metric_definitions: List[MetricDefinition] = []  # 引用的指标
```

---

## 9.7 数据血缘模型

```python
# schemas.py:191-230
class ArtifactRef(BaseModel):
    """文件/图表引用"""
    artifact_id: str
    artifact_type: Literal["chart", "table", "file", "data"]
    file_path: Optional[str] = None
    description: Optional[str] = None
    # 血缘字段
    created_by_tool: Optional[str] = None
    created_by_step: Optional[int] = None
    referenced_by_findings: List[str] = []

class ConclusionTrace(BaseModel):
    """从结论到证据到图表的追踪链路"""
    finding_id: str
    finding_statement: str
    evidence_items: List[EvidenceItem]
    artifacts: List[ArtifactRef]
    source_steps: List[int]
    trace_path: str    # 如 "Step 5 → F001 → chart.png"

class SessionLineage(BaseModel):
    """完整会话的数据血缘"""
    session_id: str
    artifacts: List[ArtifactRef]
    findings: List[Finding]
    conclusion_traces: List[ConclusionTrace]
    final_report: Optional[str] = None
```

### 血缘追踪示例

```
SessionLineage:
  session_id: "abc-123"

  artifacts:
    - artifact_id: "chart_1", type: "chart", file: "revenue_by_region.png"
      created_by_tool: "eda_profile", created_by_step: 2
    - artifact_id: "chart_2", type: "chart", file: "time_trend.png"
      created_by_tool: "python_repl", created_by_step: 5

  findings:
    - F001: "华东区 revenue 占 42%"
    - F002: "Q3 revenue 环比上升 15%"

  conclusion_traces:
    - finding_id: "F001"
      trace_path: "Step 3 python_repl → F001 → chart_1"
      artifacts: [chart_1]
    - finding_id: "F002"
      trace_path: "Step 5 python_repl → F002 → chart_2"
      artifacts: [chart_2]
```

---

## 9.8 结构化日志模型

```python
# schemas.py:94-112
class StructuredLogEntry(BaseModel):
    """结构化日志条目 — 机器可读"""
    timestamp: str              # ISO 时间戳
    session_id: str             # 会话 ID
    request_id: Optional[str]   # 请求 ID
    trace_id: Optional[str]     # 追踪 ID
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"]
    event_type: str             # 事件类型：tool_call, stage_change, error...
    stage: Optional[AnalysisStage]
    tool_name: Optional[str]
    message: str
    metadata: Dict[str, Any]    # 额外元数据
```

---

## 9.9 模型之间的关系图

```
                    FinalReport
                   /    |      \
            findings  assumptions  metric_definitions
              │
              ▼
           Finding
          /       \
    evidence   evidence_level
        │
        ▼
    EvidenceItem ──── source_artifacts ────→ ArtifactRef
        │                                        │
    source_fields                           created_by_tool
    stats                                   created_by_step
    time_window
    group_dimension

    SessionLineage
        │
        ├── artifacts: List[ArtifactRef]
        ├── findings: List[Finding]
        └── conclusion_traces: List[ConclusionTrace]
                │
                ├── finding_id → Finding.finding_id
                ├── evidence_items → Finding.evidence
                └── artifacts → ArtifactRef
```

---

> **下一步**：阅读 [10-error-recovery.md](10-error-recovery.md) 深入学习错误恢复机制。

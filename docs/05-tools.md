# 05 - 工具系统详解

> 本文深入讲解 LangGraph Agent 的工具系统：设计原则、实现细节、阶段权限控制、验证机制。包含 9 个核心工具（含 Skills 工具和委托分析工具）。

---

## 5.1 工具设计原则

### 原则 1：Docstring 是给 LLM 看的

```python
@tool
def load_data(file_path: str, sheet_name: str = "") -> str:
    """Load a CSV or Excel file and return its shape, column types, and a 5-row preview.

    Args:
        file_path: Absolute path to the data file.
        sheet_name: Sheet name for Excel files (ignored for CSV).
    """
```

LLM 通过阅读 docstring 来理解：
- 这个工具做什么（第一行）
- 需要什么参数（Args 部分）
- 参数的含义和类型

**如果 docstring 写得不好，LLM 可能传错参数或在不该调用时调用。**

### 原则 2：返回值是给 LLM 看的

工具的返回值会作为 `ToolMessage` 追加到对话历史中，LLM 据此决定下一步。

```python
# ✅ 好的返回值 — 结构化、信息丰富
return "File: sales.csv\nShape: 200×8\nColumns: date, region, revenue..."

# ❌ 差的返回值 — 信息不足
return "done"

# ❌ 差的返回值 — 太长（浪费 tokens）
return str(huge_dataframe)  # 10万行数据
```

本项目限制工具输出不超过 3000 字符：

```python
_MAX_OUTPUT_LEN = 3000
if len(output) > _MAX_OUTPUT_LEN:
    output = output[:_MAX_OUTPUT_LEN] + f"\n...[truncated at {_MAX_OUTPUT_LEN} chars]"
```

### 原则 3：错误信息要可操作

```python
# ✅ 可操作的错误 — 告诉 LLM 怎么修复
return "[ERROR] python_repl step is missing required printed markers: key results. ..."

# ❌ 不可操作的错误 — LLM 不知道怎么修
return "Error"
```

---

## 5.2 工具全景（9 个核心工具）

```
┌─────────────────────────────────────────────────────────┐
│                    Tool 工具体系                          │
│                                                         │
│  数据加载层                                              │
│    load_data ─── 加载 CSV/Excel 到会话                   │
│                                                         │
│  自动分析层                                              │
│    eda_profile ─ 自动 EDA（缺失值/相关性/分布/趋势）       │
│                                                         │
│  手动分析层（核心）                                       │
│    python_repl ─ 执行任意 Python 代码                    │
│    delegate_analysis ─ 委派子任务（并行化分析）            │
│                                                         │
│  治理层（确保分析质量）                                    │
│    declare_metric ───── 声明指标定义                     │
│    declare_assumption ── 声明分析假设                    │
│    record_finding ────── 记录分析发现（含证据）           │
│                                                         │
│  输出层                                                  │
│    finish_report ──── 生成最终报告（含多重验证）           │
│                                                         │
│  知识层                                                  │
│    skill_view ──────── 查看可用分析技能                   │
│    skill_reference ─── 引用具体技能指导                   │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

### 工具注册机制（Tools Registry）

工具采用自注册模式：每个工具文件在 import 时调用 `registry.register()` 声明自己的工厂函数和元数据。

```python
# tools/tool_delegate.py
def _factory(session):
    @tool
    def delegate_analysis(...): ...
    return delegate_analysis

registry.register(
    name="delegate_analysis",
    toolset="analysis",
    factory=_factory,
    description="...",
    emoji="🔀",
)

# tools/__init__.py — 自动发现所有工具模块
_TOOL_MODULES = [
    "langgraph_langchain.tools.tool_load_data",
    "langgraph_langchain.tools.tool_python_repl",
    # ... 其他工具
    "langgraph_langchain.tools.tool_delegate",
]
```

---

## 5.3 工具 ①：`load_data`

### 功能

加载 CSV 或 Excel 文件，返回 schema 信息。

### 源码解析

```python
# langgraph_agent.py:1215-1306（简化版）
@tool
def load_data(file_path: str, sheet_name: str = "") -> str:
    # ① 阶段验证 — 只能在 INIT/SCHEMA_UNDERSTANDING 阶段调用
    error_msg = _validate_tool_stage("load_data")
    if error_msg:
        return f"[ERROR] {error_msg}"

    # ② 追踪 — 开始一个 span
    trace_ctx.start_span("load_data", attributes={"file_path": file_path})

    # ③ 记录工具使用到状态机
    session.state_machine.record_tool_use("load_data")

    # ④ 自动阶段推进
    if session.state_machine.current_stage == AnalysisStage.INIT:
        session.state_machine.transition_to(AnalysisStage.SCHEMA_UNDERSTANDING)
        session.start_stage("schema_understanding")

    # ⑤ 加载文件（支持多种编码）
    path = Path(file_path)
    if path.suffix == ".csv":
        for enc in ["utf-8", "utf-8-sig", "gbk", "gb2312", "latin-1"]:
            try:
                df = pd.read_csv(file_path, encoding=enc)
                break
            except UnicodeDecodeError:
                continue
    elif path.suffix in (".xlsx", ".xls"):
        df = pd.read_excel(file_path, sheet_name=sheet_name)

    # ⑥ 注入 DataFrame 到持久命名空间
    session.ns["df"] = df

    # ⑦ 更新状态机条件
    session.state_machine.add_condition("schema_documented")
    session.complete_stage("schema_understanding")
    session.try_advance_stage()

    # ⑧ 返回结构化信息给 LLM
    return f"File: {path.name}\nShape: {df.shape}\nColumns: ..."
```

### 设计亮点

1. **自动编码检测**：依次尝试 utf-8 → gbk → latin-1，兼容中文 CSV
2. **命名空间注入**：`session.ns["df"] = df`，后续工具可直接使用 `df`
3. **状态机联动**：自动推进到下一阶段

---

## 5.4 工具 ②：`eda_profile`

### 功能

自动探索性数据分析，生成图表和分析信号。

### 产出

```
eda_profile 自动生成:
  ├── 缺失值统计 + 缺失值柱状图
  ├── 描述统计（均值、标准差、分位数）
  ├── 偏度分析
  ├── 异常值检测（IQR 法）+ 箱线图
  ├── 相关性热力图
  ├── 数值列分布直方图
  ├── 类别列值计数图
  ├── 时间序列趋势图
  ├── Analysis Signals（分析信号提示）
  └── Business Hints（业务分析建议）
```

### 分析信号示例

```
### Analysis Signals
- revenue 缺失率 1.5%，可在后续按分组检查缺失是否集中
- revenue 偏度 2.35，可能存在长尾分布
- revenue 有 3.2% 异常值，需结合业务含义区分数据问题或真实事件
- revenue 与 quantity 相关系数 0.85，可作为线索但需分组验证
```

### R&D 模板匹配

EDA 阶段还会自动检测数据是否匹配 R&D 效率分析模板：

```python
# 自动检测列名模式
template = suggest_template(user_question, cols)
if template:
    session.ns["suggested_template"] = template
    # 返回给 LLM：建议遵循的模板步骤
```

---

## 5.5 工具 ③：`python_repl`（核心工具）

### 功能

在持久命名空间中执行 Python 代码，是整个分析过程中使用最频繁的工具。

### 运行时防护栏

```python
# 代码提交前的验证（langgraph_agent.py:108-135）
def _validate_python_repl_step(code: str) -> Optional[str]:
    stripped = code.strip()

    # 检查 1：不能为空
    if not stripped:
        return "[ERROR] Empty python_repl step."

    # 检查 2：行数限制
    code_lines = [line for line in stripped.splitlines() if line.strip()]
    if len(code_lines) > 50:
        return f"[ERROR] Too large ({len(code_lines)} lines; limit 50)."

    # 检查 3：必须包含步骤标记
    missing_markers = []
    for canonical, aliases in _REQUIRED_STEP_MARKER_ALIASES.items():
        if not any(alias.lower() in stripped.lower() for alias in aliases):
            missing_markers.append(canonical)
    if missing_markers:
        return f"[ERROR] Missing markers: {missing_markers}."

    return None  # 验证通过
```

### 必需的步骤标记

每段代码必须打印四类标记（中英双语）：

```python
# python_repl 代码模板
code = """
# 步骤目标 / step objective: 按 region 分析 revenue 差异
# 方法 / method: groupby 聚合 + 排名

region_summary = df.groupby('region')['revenue'].agg(['sum','mean','count'])
print(region_summary.sort_values('sum', ascending=False))

# 关键结果 / key results: 华东区 revenue 最高 (42%)
# 建议下一步 / suggested next step: 画区域对比柱状图
"""
```

### 代码执行机制

```python
# langgraph_agent.py:1160-1192
def run_code(self, code: str) -> str:
    buf = io.StringIO()       # 捕获 stdout + stderr
    had_exception = []

    def _target():
        old_out, old_err = sys.stdout, sys.stderr
        sys.stdout = sys.stderr = buf
        try:
            # 在持久命名空间中执行
            exec(compile(code, "<agent>", "exec"), self.ns)
        except Exception:
            traceback.print_exc(file=buf)
            had_exception.append(True)
        finally:
            sys.stdout, sys.stderr = old_out, old_err

    # 在子线程中执行，主线程等待超时
    t = threading.Thread(target=_target, daemon=True)
    t.start()
    t.join(timeout=60)  # 60 秒超时

    if t.is_alive():
        return "[ERROR] Execution timed out after 60s."

    output = buf.getvalue()
    if had_exception:
        output = "[ERROR]\n" + output
    if len(output) > 3000:
        output = output[:3000] + "\n...[truncated]"
    return output
```

### 预置辅助函数

`python_repl` 的命名空间预置了多个分析辅助函数：

| 函数 | 作用 | 使用场景 |
|------|------|---------|
| `save_fig(filename)` | 保存 matplotlib 图表 | 每次画图后调用 |
| `fix_chinese()` | 修复中文字体 | 画中文标签图表前调用 |
| `profile_dimension(df, dim, metric)` | 按维度分析指标 | 分组统计 |
| `compare_segments(df, dim, metrics)` | 分组对比 | Top/Bottom 分析 |
| `time_trend(df, date_col, metric)` | 时间序列分析 | 趋势分析 |
| `detect_anomalies(series)` | 异常值检测 | 数据质量检查 |
| `decompose_metric_change(...)` | 指标变化分解 | 归因分析 |
| `assess_evidence_level(...)` | 评估证据等级 | 判断证据质量 |
| `rank_driver_candidates(...)` | 驱动因素排名 | 归因排名 |
| `check_metric_definition_risk(...)` | 指标定义风险检查 | 数据口径检查 |
| `run_counterfactual_checks(...)` | 反事实检验 | 稳健性验证 |
| `generate_recommendation_candidates(...)` | 生成建议候选 | 报告建议 |

---

## 5.6 工具 ④：`record_finding`

### 功能

结构化记录分析发现，包含证据、置信度、证据等级。

### 参数详解

```python
@tool
def record_finding(
    statement: str,          # 核心结论（如 "华东区 revenue 占总量 42%"）
    evidence_text: str,      # 支撑证据（如 "华东区 total=420K, 占比 42%"）
    confidence_level: str,   # 置信度："low" / "medium" / "high"
    evidence_level: str,     # 证据等级："A"(事实) / "B"(相关) / "C"(因果)
    hypothesis_flag: bool,   # 是否为假设（True 表示需要验证）
    category: str,           # 分类：trend/anomaly/comparison/attribution
    source_fields: List[str],      # 涉及的数据字段
    source_artifacts: List[str],   # 涉及的图表文件
    time_window: str,        # 时间窗口（如 "2025-Q3"）
    group_dimension: str,    # 分组维度（如 "region"）
    filters: List[str],      # 使用的过滤条件
    stats: dict,             # 关键统计数据
    calculation_method: str, # 计算方法
) -> str:
```

### 证据等级体系

```
Level A — 事实描述（最低风险）
  ✅ "华东区 Q3 revenue 为 420K，占总量的 42%"
  ✅ "共有 5 个区域，200 条记录"
  特点：只描述数据，不做关系断言

Level B — 相关线索（中等风险）
  ✅ "Revenue 下降与订单量减少同时出现"
  ✅ "华东区的 revenue 和 quantity 相关系数为 0.85"
  特点：描述观察到的模式或相关性，但不声称因果

Level C — 因果判断（最高风险，要求最严）
  ✅ "8 月促销活动导致 revenue 上升 15%"
  前提条件：
    1. 时序关系（促销在 revenue 上升之前）
    2. 控制变量（排除了其他因素）
    3. 机制解释（为什么促销会导致上升）
    4. 排除替代解释（不是季节性因素等）
```

### 验证流程

```python
# record_finding 内部验证链
# ① 阶段验证
error_msg = _validate_tool_stage("record_finding")

# ② R&D 领域验证
is_valid, rd_errors = validate_rd_finding(finding)
if not is_valid:
    return "[WARNING] Finding has R&D domain issues: ..."

# ③ 证据绑定验证
is_valid, binding_errors = validate_evidence_binding(finding, artifacts)
if not is_valid:
    return "[ERROR] Evidence binding validation failed."

# ④ 完整性检查（警告，不阻断）
is_complete, warnings = validate_evidence_completeness(finding, artifacts)
```

---

## 5.7 工具 ⑤⑥：`declare_metric` 和 `declare_assumption`

### `declare_metric` — 指标定义

```python
@tool
def declare_metric(
    metric_name: str,              # "conversion_rate"
    definition_text: str,          # "成交用户数 / 访问用户数"
    time_window: str = None,       # "2025-Q3"
    dedup_rule: str = None,        # "按 user_id 去重"
    denominator: str = None,       # "访问用户数"
    semantic_uncertainty: str = None,  # "假设 status=completed 表示已付款"
) -> str:
```

**为什么需要？** 明确指标口径，防止分析歧义。

### `declare_assumption` — 分析假设

```python
@tool
def declare_assumption(
    assumption_text: str,    # "假设数据中无重复订单"
    risk_level: str = "medium",  # "low" / "medium" / "high"
) -> str:
```

**为什么需要？** 让分析的前提假设显式化，提高可重复性。

---

## 5.8 工具 ⑦：`finish_report`

### 功能

提交最终分析报告。这是最复杂的工具，包含 **30+ 条验证规则**。

### 验证规则概览

```
finish_report 验证链:
│
├── 基础验证
│   ├── 阶段验证（必须在 synthesis 之后）
│   ├── 一次性保护（只能调用一次）
│   ├── 最少 2 条 record_finding
│   └── 报告长度 ≥ 400 字符
│
├── 结构验证
│   ├── 必须有 markdown 标题
│   ├── 必须包含 Data Context 章节
│   ├── 必须包含 Summary、Key Findings、Data Quality、Analysis
│   └── 至少 5 个实质性章节
│
├── 证据验证
│   ├── Key Findings 必须有具体证据（数字、百分比、分组）
│   ├── 趋势结论必须有时间窗口
│   ├── 分组结论必须指明维度
│   └── Data Quality 必须有具体字段和计数
│
├── 因果语言验证
│   ├── 因果语言需要 C 级证据
│   ├── 归因声明需要证据等级或贡献分解
│   ├── 强因果声明需要稳定性检验或不确定性标注
│   └── 解释性结论需要稳健性说明
│
├── 建议验证
│   ├── 强行动建议需要量化支撑 + 时间窗口 + 影响对象
│   ├── 弱证据时建议必须保持验证/观察语气
│   └── 建议必须与 explanation_bundle 中的证据等级匹配
│
├── 指标验证
│   ├── 必须使用 declare_metric 声明关键指标
│   ├── 比率/转化类声明需要分母和去重规则
│   └── 退款/重复类话题需要显式风险标注
│
├── explanation_bundle 一致性
│   ├── 报告必须引用 metric_decomposition 的比较窗口
│   ├── 报告必须引用 top driver 的组和维度
│   ├── 报告必须引用 driver 的 evidence level
│   └── definition_risk 时必须标注 exploratory caveat
│
└── R&D 领域验证
    ├── R&D 分析完整性检查
    └── 证据等级与因果语言一致性
```

### 报告通过后的产出

```python
# 报告通过验证后，finish_report 会生成多个文件：

# 1. 最终报告（markdown）
workspace/data_analysis_report.md

# 2. 结构化发现（JSON）
workspace/analysis_findings.json
{
    "findings": [...],
    "metric_definitions": [...],
    "assumptions": [...]
}

# 3. 数据血缘图（JSON）
workspace/lineage_graph.json
{
    "nodes": [...],      # 从数据源到结论的节点
    "edges": [...],      # 节点之间的血缘关系
}

# 4. 追踪分析报告（如果有追踪）
workspace/trace_analysis.json
```

---

## 5.9 工具 ⑧：`delegate_analysis`（子任务委派）

### 功能

将聚焦的分析子任务委派到独立的 Python 命名空间执行，实现分析并行化。

### 适用场景

```
delegate_analysis 适用于:
├── 分段分析："按 region 分别分析 sales trend"
├── 分组对比："Compare top 5 vs bottom 5 customers"
├── 异常检测："Run anomaly detection on each product category"
└── 趋势分析："分析各 product 的月度趋势"
```

### 参数

```python
@tool
def delegate_analysis(
    task_description: str,        # 分析目标（如 "Sales trend for region=East"）
    analysis_type: str = "general",  # "segment" / "trend" / "anomaly" / "comparison"
    dimensions: List[str] = None,    # 关注的列名（如 ["region", "product"]）
    code: str = "",                  # Python 代码（有 df 副本、save_fig 等）
) -> str:
```

### 工作流程

```
主 Agent 调用 delegate_analysis
    │
    ├── 1. 验证阶段权限（BASIC_EDA / DEEP_DIVE / CONCLUSION_SYNTHESIS）
    ├── 2. 检查嵌套深度（_delegation_depth < 1）
    ├── 3. 验证 df 存在（session.ns["df"]）
    ├── 4. 创建隔离命名空间 { df: df.copy(), save_fig, ... }
    ├── 5. 在子线程中执行代码（60s 超时）
    ├── 6. 捕获输出（≤3000 字符）
    ├── 7. 检测新生成的图表文件
    └── 8. 返回结构化 JSON 结果
        {
            "task": "Sales trend for region=East",
            "analysis_type": "segment",
            "dimensions": ["region"],
            "success": true,
            "output": "{East: {Q1: 120K, Q2: 150K, ...}}"
        }
```

### 设计安全措施

| 保护措施 | 实现 |
|---------|------|
| 无嵌套委派 | `_delegation_depth` 计数器，限制为 1 |
| DataFrame 隔离 | 子任务获得 `df.copy()`，主 df 不被修改 |
| 超时保护 | 与 `python_repl` 共享 60s 超时 |
| 输出截断 | ≤3000 字符，与 `_MAX_OUTPUT_LEN` 一致 |
| 结果需手动记录 | 主 Agent 须调用 `record_finding` 记录发现 |

---

## 5.10 阶段权限控制

### `ToolStageValidator` 机制

```python
# tool_validators.py
class ToolStageValidator:
    TOOL_STAGE_REQUIREMENTS = {
        "load_data": {
            "allowed_stages": [INIT, SCHEMA_UNDERSTANDING],
        },
        "eda_profile": {
            "allowed_stages": [SCHEMA_UNDERSTANDING, DATA_QUALITY_CHECK, BASIC_EDA],
        },
        "python_repl": {
            "allowed_stages": [SCHEMA_UNDERSTANDING, DATA_QUALITY_CHECK,
                             BASIC_EDA, DEEP_DIVE, CONCLUSION_SYNTHESIS],
        },
        "record_finding": {
            "allowed_stages": [BASIC_EDA, DEEP_DIVE, CONCLUSION_SYNTHESIS],
        },
        "delegate_analysis": {
            "allowed_stages": [BASIC_EDA, DEEP_DIVE, CONCLUSION_SYNTHESIS],
        },
        "finish_report": {
            "allowed_stages": [CONCLUSION_SYNTHESIS, REPORT_GENERATION],
        },
    }

    TOOL_PREREQUISITES = {
        "eda_profile": {
            "required_tools": ["load_data"],  # 必须先调用 load_data
        },
        "delegate_analysis": {
            "required_tools": ["load_data"],  # 必须先加载数据
        },
        "finish_report": {
            "required_tools": ["load_data", "eda_profile"],
            "min_findings": 3,  # 必须至少 3 条 finding
        },
    }
```

### 权限矩阵

| 阶段 | load_data | eda_profile | python_repl | record_finding | delegate_analysis | declare_metric | declare_assumption | finish_report |
|------|:---------:|:-----------:|:-----------:|:--------------:|:-----------------:|:--------------:|:------------------:|:-------------:|
| INIT | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| SCHEMA_UNDERSTANDING | ✅ | ✅ | ✅ | ❌ | ❌ | ✅ | ✅ | ❌ |
| DATA_QUALITY_CHECK | ❌ | ✅ | ✅ | ❌ | ❌ | ✅ | ✅ | ❌ |
| BASIC_EDA | ❌ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ |
| DEEP_DIVE | ❌ | ❌ | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ |
| CONCLUSION_SYNTHESIS | ❌ | ❌ | ✅ | ✅ | ✅ | ❌ | ❌ | ✅ |
| REPORT_GENERATION | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ |

> **如果 LLM 尝试在错误的阶段调用工具**，会收到错误消息并需要调整行为。

---

> **下一步**：阅读 [06-state-management.md](06-state-management.md) 深入学习状态管理机制。

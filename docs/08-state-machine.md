# 08 - 状态机与阶段控制详解

> 本文深入解析分析状态机（`AnalysisStateMachine`）的设计：阶段定义、流转规则、约束检查、自动推进。

---

## 8.1 为什么需要状态机？

### 没有状态机的问题

LLM 是不确定的。即使系统提示词说"先调用 load_data，再调用 eda_profile"，LLM 可能：

```
❌ 在没加载数据的情况下直接调用 eda_profile
❌ 跳过 EDA 直接深入分析
❌ 没有任何 finding 就调用 finish_report
❌ 反复调用 load_data 而不推进分析
```

### 状态机的解法

状态机 **硬性约束** Agent 的行为顺序：

```
✅ INIT → 必须先 load_data
✅ 加载数据后 → 才能调用 eda_profile
✅ EDA 之后 → 才能用 python_repl 做分析
✅ 有足够 finding → 才能提交报告
```

---

## 8.2 八个阶段定义

```python
# state_machine.py:13-24
class AnalysisStage(str, Enum):
    INIT = "init"                              # 0. 初始化
    SCHEMA_UNDERSTANDING = "schema_understanding"  # 1. 理解数据结构
    DATA_QUALITY_CHECK = "data_quality_check"      # 2. 数据质量检查
    BASIC_EDA = "basic_eda"                        # 3. 基础探索分析
    DEEP_DIVE = "deep_dive"                        # 4. 深入分析
    CONCLUSION_SYNTHESIS = "conclusion_synthesis"  # 5. 结论综合
    REPORT_GENERATION = "report_generation"        # 6. 报告生成
    COMPLETED = "completed"                        # 7. 完成（终态）
    FAILED = "failed"                              # 8. 失败（终态）
```

### 每个阶段的目的和产出

| 阶段 | 目的 | 主要工具 | 产出 |
|------|------|---------|------|
| INIT | 等待开始 | 无 | — |
| SCHEMA_UNDERSTANDING | 了解数据结构和字段 | load_data | DataFrame 加载到 ns |
| DATA_QUALITY_CHECK | 评估数据质量 | eda_profile | EDA 报告 + 图表 |
| BASIC_EDA | 探索性分析 | python_repl | 分布、相关性初步了解 |
| DEEP_DIVE | 针对性深入分析 | python_repl + record_finding | 分析发现 + 证据 |
| CONCLUSION_SYNTHESIS | 综合和组织发现 | record_finding | 有序的发现列表 |
| REPORT_GENERATION | 生成最终报告 | finish_report | 完整分析报告 |
| COMPLETED | 分析完成 | 无 | 所有产出物 |
| FAILED | 分析失败 | 无 | 失败信息 |

---

## 8.3 阶段流转规则

### 流转图

```
                    ┌─────────┐
                    │  INIT   │
                    └────┬────┘
                         │
                    ┌────▼─────────────────┐
                    │ SCHEMA_UNDERSTANDING │
                    └────┬─────────────────┘
                         │                ┌─────────┐
                    ┌────▼───────────┐    │  FAILED │
                    │DATA_QUALITY_CHK│───→│         │
                    └────┬───────────┘    └─────────┘
                         │
                    ┌────▼────┐
              ┌────→│BASIC_EDA│←──┐
              │     └────┬────┘   │
              │          │        │
              │    ┌─────▼──────┐ │
              │    │ DEEP_DIVE  │─┘
              │    └─────┬──────┘
              │          │
              │    ┌─────▼──────────────┐
              └────│CONCLUSION_SYNTHESIS│
                   └─────┬──────────────┘
                         │
                   ┌─────▼──────────────┐
                   │REPORT_GENERATION   │
                   └─────┬──────────────┘
                         │
                   ┌─────▼──────┐
                   │ COMPLETED  │
                   └────────────┘
```

### `VALID_TRANSITIONS` 代码

```python
# state_machine.py:110-138
VALID_TRANSITIONS = {
    # 只能从 INIT 进入 SCHEMA_UNDERSTANDING
    INIT: {SCHEMA_UNDERSTANDING},

    # 从 SCHEMA_UNDERSTANDING 可以进入下一阶段或失败
    SCHEMA_UNDERSTANDING: {
        DATA_QUALITY_CHECK,
        FAILED,  # 如果 schema 无法理解
    },

    # 从 DATA_QUALITY_CHECK 可以进入下一阶段或失败
    DATA_QUALITY_CHECK: {
        BASIC_EDA,
        FAILED,  # 如果数据质量太差
    },

    # 从 BASIC_EDA 可以进入 DEEP_DIVE 或直接跳到 CONCLUSION
    BASIC_EDA: {
        DEEP_DIVE,
        CONCLUSION_SYNTHESIS,  # 简单问题可跳过 DEEP_DIVE
    },

    # 从 DEEP_DIVE 可以前进或回退
    DEEP_DIVE: {
        CONCLUSION_SYNTHESIS,
        BASIC_EDA,  # 发现线索不足，回退补充分析
    },

    # 从 CONCLUSION_SYNTHESIS 可以前进或回退
    CONCLUSION_SYNTHESIS: {
        REPORT_GENERATION,
        DEEP_DIVE,  # 发现不够充分，回退继续分析
    },

    # 从 REPORT_GENERATION 可以完成或回退
    REPORT_GENERATION: {
        COMPLETED,
        CONCLUSION_SYNTHESIS,  # 报告被拒绝，回退修改
    },

    # 终态不可转换
    COMPLETED: set(),
    FAILED: set(),
}
```

### 允许回退的场景

| 回退 | 场景 |
|------|------|
| DEEP_DIVE → BASIC_EDA | 分析中发现需要更多基础探索 |
| CONCLUSION_SYNTHESIS → DEEP_DIVE | 发现不够充分，需要更多分析 |
| REPORT_GENERATION → CONCLUSION_SYNTHESIS | 报告被验证拒绝，需要补充 |

---

## 8.4 阶段约束详解

### `StageRequirements` 数据类

```python
@dataclass
class StageRequirements:
    entry_conditions: List[str]    # 进入条件
    exit_conditions: List[str]     # 退出条件
    required_tools: List[str]      # 必须使用的工具
    optional_tools: List[str]      # 可选工具
    min_steps: int                 # 最少步骤
    max_steps: int                 # 最多步骤
    can_skip: bool = False         # 是否可跳过
```

### 每个阶段的约束

```python
STAGE_REQUIREMENTS = {
    SCHEMA_UNDERSTANDING: StageRequirements(
        entry_conditions=[],                # 无前置条件
        exit_conditions=["schema_documented", "fields_understood"],
        required_tools=["load_data"],       # 必须调用 load_data
        optional_tools=["python_repl"],
        min_steps=1,                        # 最少 1 步
        max_steps=3,                        # 最多 3 步
    ),

    DATA_QUALITY_CHECK: StageRequirements(
        entry_conditions=["schema_documented"],  # 必须先完成 schema 理解
        exit_conditions=["quality_assessed", "issues_documented"],
        required_tools=["eda_profile"],
        optional_tools=["python_repl"],
        min_steps=1,
        max_steps=5,
    ),

    BASIC_EDA: StageRequirements(
        entry_conditions=["quality_assessed"],
        exit_conditions=["distributions_analyzed", "correlations_checked"],
        required_tools=["python_repl"],
        optional_tools=["declare_metric", "declare_assumption"],
        min_steps=2,
        max_steps=10,
    ),

    DEEP_DIVE: StageRequirements(
        entry_conditions=["distributions_analyzed"],
        exit_conditions=["question_addressed", "findings_recorded"],
        required_tools=["python_repl", "record_finding"],
        optional_tools=["declare_metric", "declare_assumption"],
        min_steps=3,                        # 最核心，至少 3 步
        max_steps=20,                       # 最多 20 步
    ),

    REPORT_GENERATION: StageRequirements(
        entry_conditions=["findings_organized", "min_findings_count"],
        exit_conditions=["report_generated"],
        required_tools=["finish_report"],
        min_steps=1,
        max_steps=2,                        # 报告最多提交 2 次
    ),
}
```

---

## 8.5 状态机核心方法

### `can_transition_to` — 检查转换是否合法

```python
# state_machine.py:147-168
def can_transition_to(self, target_stage):
    """检查能否转换到目标阶段"""

    # 检查 1：当前阶段允许转换到目标阶段吗？
    if target_stage not in self.VALID_TRANSITIONS.get(self.current_stage, set()):
        return False, f"Cannot go from {self.current_stage} to {target_stage}"

    # 检查 2：目标阶段的进入条件满足了吗？
    if target_stage in self.STAGE_REQUIREMENTS:
        requirements = self.STAGE_REQUIREMENTS[target_stage]
        missing = [
            cond for cond in requirements.entry_conditions
            if cond not in self.conditions_met
        ]
        if missing:
            return False, f"Missing conditions: {', '.join(missing)}"

    return True, None
```

### `record_tool_use` — 工具使用触发条件更新

```python
# state_machine.py:186-198
def record_tool_use(self, tool_name: str):
    """记录工具使用，并自动检测条件"""
    self.tools_used.append(tool_name)

    # 工具使用 → 自动满足对应条件
    if tool_name == "load_data":
        self.conditions_met.add("data_loaded")
    elif tool_name == "eda_profile":
        self.conditions_met.add("quality_assessed")
    elif tool_name == "record_finding":
        self.conditions_met.add("findings_recorded")
    elif tool_name == "finish_report":
        self.conditions_met.add("report_generated")
```

### `get_next_recommended_stage` — 推荐下一阶段

```python
# state_machine.py:231-251
def get_next_recommended_stage(self):
    """根据当前状态推荐下一个阶段"""
    valid_next = self.VALID_TRANSITIONS.get(self.current_stage, set())

    # 优先推荐非终态阶段
    non_terminal = [s for s in valid_next
                    if s not in {COMPLETED, FAILED}]

    for stage in non_terminal:
        can, _ = self.can_transition_to(stage)
        if can:
            return stage

    # 如果非终态都不行，检查终态
    for stage in valid_next:
        if stage in {COMPLETED, FAILED}:
            can, _ = self.can_transition_to(stage)
            if can:
                return stage

    return None
```

---

## 8.6 状态机在工具中的使用

### load_data 中的状态机联动

```python
@tool
def load_data(file_path, sheet_name=""):
    # 如果还在 INIT，推进到 SCHEMA_UNDERSTANDING
    if session.state_machine.current_stage == AnalysisStage.INIT:
        session.state_machine.transition_to(AnalysisStage.SCHEMA_UNDERSTANDING)
        session.start_stage("schema_understanding")

    # 加载数据...

    # 记录条件已满足
    session.state_machine.add_condition("schema_documented")
    session.state_machine.add_condition("fields_understood")

    # 完成当前阶段
    session.complete_stage("schema_understanding")

    # 尝试自动推进到下一阶段
    session.try_advance_stage()
```

### record_finding 中的条件触发

```python
@tool
def record_finding(...):
    # ... 记录发现

    session.state_machine.add_condition("findings_recorded")

    # 记录足够多 finding 后标记 min_findings_count
    if len(session.findings) >= 3:
        session.state_machine.add_condition("min_findings_count")
```

### finish_report 中的完成推进

```python
@tool
def finish_report(markdown):
    # ... 验证和生成报告

    session.state_machine.add_condition("report_generated")

    # 尝试推进到 COMPLETED
    can, _ = session.state_machine.can_transition_to(AnalysisStage.COMPLETED)
    if can:
        session.state_machine.transition_to(AnalysisStage.COMPLETED)
        session.start_stage("completed")
```

---

## 8.7 条件（Condition）机制

条件是状态机的"软开关"。它们不是硬编码在转换规则中，而是通过工具使用动态触发的：

```
条件                    触发方式
───────────────────    ─────────────────────────
schema_documented      load_data 调用后自动触发
fields_understood      load_data 调用后自动触发
quality_assessed       eda_profile 调用后自动触发
findings_recorded      record_finding 调用后自动触发
min_findings_count     finding 数量 >= 3 时自动触发
report_generated       finish_report 调用后自动触发

也可以手动添加：
session.state_machine.add_condition("custom_condition")
```

这种设计让阶段转换更加灵活——条件可以由任何工具触发，而不仅限于特定的工具调用顺序。

---

> **下一步**：阅读 [09-data-models.md](09-data-models.md) 深入学习 Pydantic 数据模型。

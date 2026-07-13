# Week 2 优化实施记录：Agent 收敛稳定性

**实施日期**：2026-04-09  
**优化目标**：让执行过程更稳定，不靠运气

---

## 已完成的改进

### 1. 扩展失败分类体系 ✅

**位置**：
- `langgraph_langchain/schemas.py:15-31` - FailureCode 类型定义
- `langgraph_langchain/api_server_langgraph.py:72-145` - 失败策略矩阵

#### 新增失败类型

在原有 8 种失败类型基础上，新增 7 种：

**原有失败类型**：
1. `missing_data_file` - 数据文件缺失
2. `session_not_found` - Session 不存在
3. `session_workspace_missing` - Workspace 缺失
4. `session_expired` - Session 过期
5. `python_execution_error` - Python 代码执行错误
6. `max_steps_exceeded` - 超过最大步数
7. `report_rejected` - 报告被拒绝
8. `cancelled` - 用户取消

**新增失败类型**：
9. `schema_understanding_failed` - 数据 schema 理解失败
10. `field_semantic_unclear` - 字段语义不明确
11. `tool_execution_failed` - 工具执行失败
12. `reasoning_drift` - 推理偏航
13. `report_generation_failed` - 报告生成失败
14. `timeout` - 超时
15. `session_interrupted` - Session 中断

#### 每种失败类型的恢复策略

```python
{
    "schema_understanding_failed": {
        "retryable": True,
        "hint": "Data schema could not be understood. Check if the file format is valid and columns are properly named.",
        "recovery_action": "user_action_required",
    },
    "field_semantic_unclear": {
        "retryable": True,
        "hint": "Field meanings are ambiguous. Provide explicit field definitions or rename columns to be more descriptive.",
        "recovery_action": "user_action_required",
    },
    "tool_execution_failed": {
        "retryable": True,
        "hint": "A tool failed to execute. Check tool parameters and data format.",
        "recovery_action": "retry_narrower_scope",
    },
    "reasoning_drift": {
        "retryable": True,
        "hint": "Analysis went off track. Restart with a more focused question or narrower scope.",
        "recovery_action": "retry_narrower_scope",
    },
    "report_generation_failed": {
        "retryable": True,
        "hint": "Report generation failed. Ensure sufficient findings were recorded and all required sections are present.",
        "recovery_action": "retry_narrower_scope",
    },
    "timeout": {
        "retryable": True,
        "hint": "Analysis timed out. Try a simpler question or smaller dataset.",
        "recovery_action": "retry_narrower_scope",
    },
    "session_interrupted": {
        "retryable": True,
        "hint": "Session was interrupted. Restart the analysis from the beginning.",
        "recovery_action": "retry_same_scope",
    },
}
```

---

### 2. 设计并实现分析状态机 ✅

**位置**：`langgraph_langchain/state_machine.py`

#### 状态机设计

定义了 8 个固定阶段：

```python
class AnalysisStage(str, Enum):
    INIT = "init"
    SCHEMA_UNDERSTANDING = "schema_understanding"
    DATA_QUALITY_CHECK = "data_quality_check"
    BASIC_EDA = "basic_eda"
    DEEP_DIVE = "deep_dive"
    CONCLUSION_SYNTHESIS = "conclusion_synthesis"
    REPORT_GENERATION = "report_generation"
    COMPLETED = "completed"
    FAILED = "failed"
```

#### 每个阶段的要求

**1. Schema Understanding（数据理解）**
- **入口条件**：`data_loaded`
- **出口条件**：`schema_documented`, `fields_understood`
- **必需工具**：`load_data`
- **可选工具**：`python_repl`
- **步数范围**：1-3 步
- **不可跳过**

**2. Data Quality Check（数据质量检查）**
- **入口条件**：`schema_documented`
- **出口条件**：`quality_assessed`, `issues_documented`
- **必需工具**：`eda_profile`
- **可选工具**：`python_repl`
- **步数范围**：1-5 步
- **不可跳过**

**3. Basic EDA（基础探索性分析）**
- **入口条件**：`quality_assessed`
- **出口条件**：`distributions_analyzed`, `correlations_checked`
- **必需工具**：`python_repl`
- **可选工具**：`declare_metric`, `declare_assumption`
- **步数范围**：2-10 步
- **不可跳过**

**4. Deep Dive（深入分析）**
- **入口条件**：`distributions_analyzed`
- **出口条件**：`question_addressed`, `findings_recorded`
- **必需工具**：`python_repl`, `record_finding`
- **可选工具**：`declare_metric`, `declare_assumption`
- **步数范围**：3-20 步
- **不可跳过**

**5. Conclusion Synthesis（结论整理）**
- **入口条件**：`findings_recorded`
- **出口条件**：`findings_organized`, `evidence_linked`
- **必需工具**：`record_finding`
- **可选工具**：`python_repl`
- **步数范围**：1-5 步
- **不可跳过**

**6. Report Generation（报告生成）**
- **入口条件**：`findings_organized`, `min_findings_count`
- **出口条件**：`report_generated`
- **必需工具**：`finish_report`
- **可选工具**：无
- **步数范围**：1-2 步
- **不可跳过**

#### 状态转换规则

```python
VALID_TRANSITIONS = {
    INIT → SCHEMA_UNDERSTANDING
    SCHEMA_UNDERSTANDING → DATA_QUALITY_CHECK | FAILED
    DATA_QUALITY_CHECK → BASIC_EDA | FAILED
    BASIC_EDA → DEEP_DIVE | CONCLUSION_SYNTHESIS
    DEEP_DIVE → CONCLUSION_SYNTHESIS | BASIC_EDA (可回退)
    CONCLUSION_SYNTHESIS → REPORT_GENERATION | DEEP_DIVE (可回退)
    REPORT_GENERATION → COMPLETED | CONCLUSION_SYNTHESIS (可回退)
    COMPLETED → (终止)
    FAILED → (终止)
}
```

#### 状态机核心方法

```python
class AnalysisStateMachine:
    def can_transition_to(target_stage) -> (bool, reason)
    def transition_to(target_stage) -> bool
    def record_tool_use(tool_name)
    def record_step()
    def add_condition(condition)
    def check_stage_limits() -> (bool, warning)
    def get_next_recommended_stage() -> AnalysisStage
    def get_stage_progress() -> dict
```

---

### 3. 在 _Session 中集成状态机 ✅

**位置**：`langgraph_langchain/langgraph_agent.py:930-948`

#### 初始化状态机

```python
# State machine for tracking analysis progress
from .state_machine import AnalysisStateMachine
self.state_machine = AnalysisStateMachine()
```

#### 在工具中集成状态机跟踪

**load_data 工具**：
```python
session.state_machine.record_tool_use("load_data")
session.state_machine.add_condition("schema_documented")
session.state_machine.add_condition("fields_understood")
```

**eda_profile 工具**：
```python
session.state_machine.record_tool_use("eda_profile")
# 自动标记 quality_assessed 条件
```

**record_finding 工具**：
```python
session.state_machine.record_tool_use("record_finding")
# 自动标记 findings_recorded 条件
```

**declare_metric 工具**：
```python
session.state_machine.record_tool_use("declare_metric")
```

**declare_assumption 工具**：
```python
session.state_machine.record_tool_use("declare_assumption")
```

**finish_report 工具**：
```python
session.state_machine.record_tool_use("finish_report")
# 自动标记 report_generated 条件
```

---

## 预期效果

### 1. 更可预测的执行路径

- Agent 必须按固定阶段执行
- 不能跳过关键阶段（如 schema understanding、data quality check）
- 每个阶段有明确的入口和出口条件

### 2. 更清晰的失败原因

- 15 种细分的失败类型
- 每种失败类型有明确的提示信息
- 每种失败类型有针对性的恢复策略

### 3. 更好的进度追踪

- 可以查询当前处于哪个阶段
- 可以查询已完成的阶段历史
- 可以查询已满足的条件
- 可以查询使用过的工具

### 4. 更强的约束力

- 每个阶段有步数限制
- 超过步数限制会触发警告
- 可以检测是否满足阶段转换条件

---

## 使用示例

### 查询状态机进度

```python
progress = session.state_machine.get_stage_progress()
# {
#     "current_stage": "deep_dive",
#     "stage_history": ["init", "schema_understanding", "data_quality_check", "basic_eda", "deep_dive"],
#     "conditions_met": ["data_loaded", "schema_documented", "fields_understood", "quality_assessed", "distributions_analyzed"],
#     "tools_used": ["load_data", "eda_profile", "python_repl", "python_repl", "record_finding"],
#     "stage_step_counts": {
#         "schema_understanding": 1,
#         "data_quality_check": 1,
#         "basic_eda": 3,
#         "deep_dive": 5
#     },
#     "next_recommended_stage": "conclusion_synthesis"
# }
```

### 检查是否可以转换到下一阶段

```python
can_transition, reason = session.state_machine.can_transition_to(AnalysisStage.REPORT_GENERATION)
if not can_transition:
    print(f"Cannot transition: {reason}")
    # "Cannot transition: Missing entry conditions: findings_organized, min_findings_count"
```

### 检查当前阶段是否超过步数限制

```python
within_limits, warning = session.state_machine.check_stage_limits()
if not within_limits:
    print(warning)
    # "Stage deep_dive has exceeded max steps (20)"
```

---

### 4. 实施恢复策略 ✅

**位置**：
- `langgraph_langchain/recovery.py` - 恢复策略实现
- `langgraph_langchain/api_server_langgraph.py:451-550` - 集成到 API 层

#### 恢复策略类型

**1. retry_same_scope（相同范围重试）**
- 适用场景：用户取消、会话中断
- 策略：使用原始指令重试
- 最大重试次数：1 次

**2. retry_narrower_scope（缩小范围重试）**
- 适用场景：超过最大步数、Python 执行错误、推理偏航、报告生成失败
- 策略：在原始指令后添加约束提示
- 最大重试次数：2 次

**针对不同失败类型的提示**：
```python
max_steps_exceeded → "Focus on the most critical 2-3 findings only. Skip exploratory analysis."
python_execution_error → "Use simpler, more defensive code. Validate data types before operations."
reasoning_drift → "Stay focused on the original question. Avoid tangential analysis."
report_generation_failed → "Ensure you record at least 2 findings. Include a 'Data Context' section."
```

**3. user_action_required（需要用户操作）**
- 适用场景：数据文件缺失、字段语义不明确、schema 理解失败
- 策略：不自动重试，返回提示信息
- 最大重试次数：0 次

#### 恢复执行器

```python
class RecoveryExecutor:
    async def attempt_recovery(
        session_id: str,
        failure_detail: Dict[str, Any],
        original_instruction: str,
        retry_count: int = 0,
    ) -> Optional[Dict[str, Any]]
```

**功能**：
- 跟踪每个 session 的恢复历史
- 防止同一失败类型重试超过 3 次
- 返回修改后的指令和重试元数据

#### API 集成

在 `_run_analysis` 中自动执行恢复：

```python
failure = _structured_failure_from_output(output)
if failure:
    recovery_result = await recovery_executor.attempt_recovery(...)
    if recovery_result and should_retry:
        # 递归重试
        return await _run_analysis(..., retry_count=current_retry)
    raise AnalysisFailureError(failure)
```

#### 新增 API 端点

```
GET /sessions/{session_id}/recovery_history
```

返回示例：
```json
{
  "session_id": "abc123",
  "recovery_attempts": 2,
  "history": [
    {
      "failure_code": "max_steps_exceeded",
      "recovery_action": "retry_narrower_scope",
      "result": {
        "strategy": "retry_narrower_scope",
        "retry_count": 1
      }
    }
  ]
}
```

---

### 5. 添加稳定性指标跟踪 ✅

**位置**：`langgraph_langchain/stability_metrics.py`

#### 跟踪的指标

**会话级别指标**：
- `session_id` - 会话 ID
- `instruction` - 用户指令（截断到 200 字符）
- `start_time` / `end_time` - 开始/结束时间
- `status` - 状态（running / success / failed / cancelled）
- `steps` - 执行步数
- `duration_seconds` - 持续时间（秒）
- `failure_code` / `failure_message` - 失败代码和消息
- `recovery_attempts` - 恢复尝试次数
- `stage_history` - 阶段历史

**聚合指标**：
- `total_sessions` - 总会话数
- `successful_sessions` - 成功会话数
- `failed_sessions` - 失败会话数
- `cancelled_sessions` - 取消会话数
- `success_rate` - 成功率
- `failure_rate` - 失败率
- `cancellation_rate` - 取消率
- `avg_steps_per_session` - 平均步数
- `avg_duration_seconds` - 平均持续时间
- `failure_counts_by_code` - 按失败代码统计
- `recovery_attempts` - 恢复尝试总数
- `successful_recoveries` - 成功恢复数
- `recovery_success_rate` - 恢复成功率

#### 核心方法

```python
class StabilityMetrics:
    def record_session_start(session_id, instruction)
    def record_session_end(session_id, status, steps, failure_code, failure_message, stage_history)
    def record_recovery_attempt(session_id, success)
    def get_aggregated_metrics() -> Dict
    def get_failure_breakdown() -> Dict
    def get_stage_completion_stats() -> Dict
    def get_recent_sessions(limit) -> List
```

#### 新增 API 端点

**1. 获取聚合指标**
```
GET /metrics/stability
```

返回示例：
```json
{
  "total_sessions": 100,
  "successful_sessions": 85,
  "failed_sessions": 10,
  "cancelled_sessions": 5,
  "success_rate": 0.85,
  "failure_rate": 0.10,
  "cancellation_rate": 0.05,
  "avg_steps_per_session": 12.5,
  "avg_duration_seconds": 45.3,
  "recovery_attempts": 15,
  "successful_recoveries": 10,
  "recovery_success_rate": 0.667
}
```

**2. 获取失败分解**
```
GET /metrics/failures
```

返回示例：
```json
{
  "total_failures": 10,
  "breakdown": [
    {"failure_code": "max_steps_exceeded", "count": 5, "percentage": 50.0},
    {"failure_code": "python_execution_error", "count": 3, "percentage": 30.0},
    {"failure_code": "report_rejected", "count": 2, "percentage": 20.0}
  ]
}
```

**3. 获取阶段统计**
```
GET /metrics/stages
```

返回示例：
```json
{
  "stage_stats": [
    {"stage": "schema_understanding", "sessions_reached": 100, "sessions_succeeded": 95, "success_rate": 0.95},
    {"stage": "data_quality_check", "sessions_reached": 95, "sessions_succeeded": 90, "success_rate": 0.947},
    {"stage": "basic_eda", "sessions_reached": 90, "sessions_succeeded": 85, "success_rate": 0.944},
    {"stage": "deep_dive", "sessions_reached": 85, "sessions_succeeded": 85, "success_rate": 1.0},
    {"stage": "conclusion_synthesis", "sessions_reached": 85, "sessions_succeeded": 85, "success_rate": 1.0},
    {"stage": "report_generation", "sessions_reached": 85, "sessions_succeeded": 85, "success_rate": 1.0}
  ]
}
```

**4. 获取最近会话**
```
GET /metrics/recent_sessions?limit=10
```

**5. 获取特定会话指标**
```
GET /sessions/{session_id}/metrics
```

#### 持久化

指标保存在 `./workspace/.stability_metrics.json`，包含：
- 所有会话的详细指标
- 聚合统计数据
- 最后更新时间

---

## 待完成的优化

### 短期（本周内）

1. **实施恢复策略** ✅ 已完成
2. **添加稳定性指标跟踪** ✅ 已完成
3. **构建 benchmark 数据集** ✅ 已完成

---

### 6. 构建稳定性 Benchmark 数据集 ✅

**位置**：
- `langgraph_langchain/benchmark.py` - Benchmark 框架
- `scripts/generate_benchmark_data.py` - 数据生成脚本

#### Benchmark 测试用例

定义了 10 个测试用例，覆盖不同难度和场景：

**简单（Easy）**：
1. **BC001 - Simple Sales Analysis**
   - 基础销售数据分析，清晰的 schema
   - 问题：前 3 名产品的收入
   - 预期步数：≤15 步

**中等（Medium）**：
2. **BC002 - Time Series Trend Analysis**
   - 时间序列趋势分析，需要日期解析
   - 问题：月度销售趋势，是否增长
   - 预期步数：≤20 步

3. **BC004 - Missing Data Handling**
   - 包含大量缺失值的数据集
   - 问题：分析销售表现，适当处理缺失值
   - 预期步数：≤20 步

4. **BC005 - Correlation Analysis**
   - 多变量相关性分析
   - 问题：哪些因素与转化率最相关
   - 预期步数：≤20 步

5. **BC006 - Outlier Detection**
   - 异常值检测
   - 问题：识别可能表明欺诈或错误的异常交易
   - 预期步数：≤20 步

6. **BC009 - A/B Test Analysis**
   - A/B 测试统计分析
   - 问题：对照组和实验组是否有显著差异
   - 预期步数：≤20 步

**困难（Hard）**：
7. **BC003 - Customer Segmentation**
   - 基于行为模式的客户分群
   - 问题：根据购买行为分群，每个群体的特征
   - 预期步数：≤25 步

8. **BC007 - Multi-Metric Dashboard**
   - 计算和比较多个业务指标
   - 问题：创建电商关键指标仪表板（转化率、AOV、LTV）
   - 预期步数：≤25 步

9. **BC008 - Cohort Analysis**
   - 用户队列分析
   - 问题：执行队列分析以了解用户留存
   - 预期步数：≤30 步

10. **BC010 - Ambiguous Schema**
    - 列名不清晰的数据集，需要解释
    - 问题：分析数据并提供洞察（列名是缩写）
    - 预期步数：≤25 步

#### Benchmark 框架

```python
class BenchmarkCase:
    case_id: str
    name: str
    description: str
    data_file: str
    instruction: str
    expected_findings_count: int
    expected_stages: List[str]
    max_steps: int
    difficulty: str  # "easy", "medium", "hard"
    tags: List[str]

class BenchmarkRunner:
    def record_run(case_id, success, steps, duration_seconds, findings_count, stages_completed, failure_code, notes)
    def get_summary() -> Dict
    def get_case_history(case_id) -> List
```

#### 数据生成

运行 `scripts/generate_benchmark_data.py` 生成所有测试数据：

```bash
python scripts/generate_benchmark_data.py
```

生成的文件：
- `benchmark_data/sales_simple.csv` - 简单销售数据（100 行）
- `benchmark_data/sales_timeseries.csv` - 时间序列销售数据（365 行）
- `benchmark_data/customers.csv` - 客户数据（500 行）
- `benchmark_data/sales_missing.csv` - 带缺失值的销售数据（100 行）
- `benchmark_data/marketing.csv` - 营销数据（200 行）
- `benchmark_data/transactions.csv` - 交易数据（1000 行，含异常值）
- `benchmark_data/ecommerce.csv` - 电商数据（500 行）
- `benchmark_data/user_activity.csv` - 用户活动数据（变长）
- `benchmark_data/ab_test.csv` - A/B 测试数据（1000 行）
- `benchmark_data/unclear_schema.csv` - 不清晰 schema 数据（200 行）

#### 使用示例

```python
from langgraph_langchain.benchmark import get_benchmark_cases, BenchmarkRunner

# 获取所有测试用例
cases = get_benchmark_cases()

# 运行测试
runner = BenchmarkRunner()
for case in cases:
    # 执行分析...
    runner.record_run(
        case_id=case.case_id,
        success=True,
        steps=12,
        duration_seconds=45.3,
        findings_count=2,
        stages_completed=["schema_understanding", "eda", "deep_dive", "synthesis", "report_generation"],
    )

# 查看摘要
summary = runner.get_summary()
print(f"Overall success rate: {summary['overall_success_rate']}")
```

#### Benchmark 结果跟踪

结果保存在 `./workspace/.benchmark_results.json`：

```json
{
  "runs": [
    {
      "case_id": "BC001",
      "timestamp": "2026-04-09T10:30:00",
      "success": true,
      "steps": 12,
      "duration_seconds": 45.3,
      "findings_count": 2,
      "stages_completed": ["schema_understanding", "eda", "deep_dive", "synthesis", "report_generation"],
      "failure_code": null,
      "notes": null
    }
  ],
  "summary": {
    "total_runs": 10,
    "successful_runs": 8,
    "failed_runs": 2,
    "overall_success_rate": 0.8,
    "case_stats": [
      {"case_id": "BC001", "total_runs": 1, "successful_runs": 1, "success_rate": 1.0}
    ]
  }
}
```

---

## 待完成的优化

### 短期（本周内）

1. **实施恢复策略** ✅ 已完成
2. **添加稳定性指标跟踪** ✅ 已完成
3. **构建 benchmark 数据集** ✅ 已完成

### 中期（Week 3）

1. **在 API 层面暴露状态机进度** - 让前端可以展示当前阶段
2. **基于状态机优化 prompt** - 在不同阶段给出不同的指导
3. **实现阶段级别的超时控制** - 每个阶段有独立的超时限制

### 长期

1. **基于状态机的自动恢复** - 失败后自动回退到上一个稳定阶段
2. **状态机的可视化展示** - 在前端展示分析流程图
3. **基于历史数据优化阶段参数** - 动态调整步数限制

---

## 注意事项

1. **向后兼容**：状态机是新增功能，不影响现有分析流程
2. **渐进式采用**：当前状态机主要用于跟踪，未来可以增强约束力
3. **性能影响**：状态机操作非常轻量，对性能影响可忽略
4. **调试友好**：`get_stage_progress()` 提供了丰富的调试信息

---

## 测试建议

### 单元测试

```python
# 测试状态机转换逻辑
def test_state_machine_transitions():
    sm = AnalysisStateMachine()
    assert sm.current_stage == AnalysisStage.INIT
    
    # 测试正常转换
    sm.add_condition("data_loaded")
    assert sm.transition_to(AnalysisStage.SCHEMA_UNDERSTANDING)
    
    # 测试非法转换
    assert not sm.transition_to(AnalysisStage.REPORT_GENERATION)
    
    # 测试条件检查
    can_transition, reason = sm.can_transition_to(AnalysisStage.DATA_QUALITY_CHECK)
    assert not can_transition
    assert "schema_documented" in reason
```

### 集成测试

```python
# 测试完整分析流程的状态机跟踪
def test_full_analysis_with_state_machine():
    session = create_session()
    
    # 加载数据
    load_data("test.csv")
    assert "data_loaded" in session.state_machine.conditions_met
    assert "schema_documented" in session.state_machine.conditions_met
    
    # EDA
    eda_profile()
    assert "quality_assessed" in session.state_machine.conditions_met
    
    # 记录 finding
    record_finding("Test finding", "Test evidence")
    assert "findings_recorded" in session.state_machine.conditions_met
    
    # 生成报告
    finish_report("# Report")
    assert session.state_machine.current_stage == AnalysisStage.COMPLETED
```

---

## 相关文件

- `langgraph_langchain/state_machine.py` - 状态机实现
- `langgraph_langchain/schemas.py` - 失败类型定义
- `langgraph_langchain/api_server_langgraph.py` - 失败策略矩阵
- `langgraph_langchain/langgraph_agent.py` - 状态机集成
- `.claude/optimization_summary_2026-04-09.md` - 总体优化路线图
- `.claude/week1_implementation_2026-04-09.md` - Week 1 实施记录

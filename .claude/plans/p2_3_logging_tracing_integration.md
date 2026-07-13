# P2-3: 结构化日志与 Tracing 集成实施计划

## 目标
将现有的 `StructuredLogger` 与 `TraceContext` 集成，确保日志自动关联 trace_id/span_id，支持按 trace 聚合日志查询。

## 当前状态分析

### 已完成
1. ✅ `StructuredLogger` 模块已开发完成（structured_logging.py）
2. ✅ `TraceContext` 模块已开发完成（tracing.py）
3. ✅ 所有主要工具已添加 span 记录（P2-2 完成）
4. ✅ 测试覆盖：structured_logging 10/10 通过

### 待完成
1. ❌ `StructuredLogger` 尚未集成到 langgraph_agent.py
2. ❌ 日志条目缺少 trace_id/span_id 字段
3. ❌ 无法按 trace 聚合查询日志
4. ❌ 缺少日志查询 API 端点

## 实施方案

### 阶段 1: 扩展 Schema 支持 Tracing 字段

**文件**: `langgraph_langchain/schemas.py`

**修改点**:
- 在 `StructuredLogEntry` 中添加字段：
  - `trace_id: Optional[str] = None`
  - `span_id: Optional[str] = None`

**原因**: 日志条目需要关联到 trace 和 span，以便后续聚合查询。

---

### 阶段 2: 增强 StructuredLogger 支持 Tracing

**文件**: `langgraph_langchain/structured_logging.py`

**修改点**:
1. 在 `__init__` 中添加参数：
   - `trace_context: Optional[TraceContext] = None`
   
2. 在 `log()` 方法中：
   - 自动从 `trace_context` 获取 `trace_id`
   - 自动从 `trace_context.current_span` 获取 `span_id`
   - 将这些 ID 写入 `StructuredLogEntry`

3. 添加新方法：
   - `set_trace_context(trace_context: TraceContext)` - 动态设置 trace context
   - `log_span_start(span_name, **attributes)` - 记录 span 开始
   - `log_span_end(span_id, status, **attributes)` - 记录 span 结束

**原因**: 让 logger 自动关联 trace 信息，无需在每次调用时手动传递。

---

### 阶段 3: 集成到 langgraph_agent.py

**文件**: `langgraph_langchain/langgraph_agent.py`

**修改点**:

1. **替换 _make_session_logger**:
   - 移除旧的 `_make_session_logger()` 函数
   - 在 `_Session.__init__` 中创建 `StructuredLogger` 实例
   - 保存为 `self.structured_logger`

2. **在 run_analysis_stream 中**:
   - 创建 `TraceContext` 后，立即设置到 `StructuredLogger`
   - 在分析开始时调用 `logger.log_stage_start()`
   - 在分析结束时调用 `logger.save_run_metrics()`

3. **在工具函数中添加日志**:
   - `load_data`: 添加 `session.structured_logger.log_tool_call()`
   - `python_repl`: 添加工具调用日志
   - `eda_profile`: 添加工具调用日志
   - `record_finding`: 添加工具调用日志
   - `finish_report`: 添加工具调用日志

4. **在状态机转换时添加日志**:
   - `_Session.transition_to_stage()`: 调用 `log_stage_complete()` 和 `log_stage_start()`
   - 失败时调用 `log_stage_fail()`

**原因**: 将结构化日志集成到实际执行流程中，确保所有关键事件都被记录。

---

### 阶段 4: 添加日志查询 API 端点

**文件**: `langgraph_langchain/api_server_langgraph.py`

**新增端点**:

1. `GET /logs/{session_id}` - 获取会话的所有日志
   - 参数: `event_type`, `level`, `tool_name`, `stage` (可选过滤)
   - 返回: JSONL 日志条目列表

2. `GET /logs/trace/{trace_id}` - 按 trace_id 聚合日志
   - 返回: 该 trace 下的所有日志条目，按时间排序

3. `GET /logs/{session_id}/metrics` - 获取会话的运行指标
   - 返回: `run_metrics_{session_id}.json` 内容

**实现逻辑**:
- 读取 `structured_log_{session_id}.jsonl` 文件
- 解析 JSONL 并过滤
- 按 trace_id 或其他条件聚合

**原因**: 提供 API 接口，支持前端或监控系统查询日志。

---

### 阶段 5: 更新测试

**文件**: `tests/test_structured_logging.py`

**新增测试**:
1. `test_trace_context_integration` - 测试 trace_id/span_id 自动关联
2. `test_set_trace_context` - 测试动态设置 trace context
3. `test_span_logging` - 测试 span 开始/结束日志

**文件**: `tests/test_api_logging.py` (新建)

**新增测试**:
1. `test_get_session_logs` - 测试获取会话日志
2. `test_get_trace_logs` - 测试按 trace 聚合日志
3. `test_get_session_metrics` - 测试获取运行指标
4. `test_log_filtering` - 测试日志过滤功能

---

## 关键设计决策

### 1. 自动 vs 手动关联 Trace ID
**决策**: 自动关联
**原因**: 
- 减少代码重复
- 降低出错概率
- 提升开发体验

### 2. Logger 与 TraceContext 的耦合方式
**决策**: 松耦合，通过可选参数传递
**原因**:
- `StructuredLogger` 可以独立使用（不依赖 tracing）
- `TraceContext` 可以独立使用（不依赖 logging）
- 集成时通过 `set_trace_context()` 动态关联

### 3. 日志存储格式
**决策**: 保持 JSONL 格式不变
**原因**:
- 易于追加写入
- 易于解析和流式处理
- 与现有测试兼容

### 4. API 端点设计
**决策**: RESTful 风格，支持过滤参数
**原因**:
- 与现有 API 风格一致
- 支持灵活查询
- 易于前端集成

---

## 预期收益

### 1. 全链路可追踪
- 每条日志都关联 trace_id 和 span_id
- 可以按 trace 聚合所有相关日志
- 支持跨工具的调用链追踪

### 2. 问题定位效率提升
- 通过 trace_id 快速定位问题上下文
- 通过 span_id 精确定位到具体工具调用
- 结构化字段支持高效过滤和搜索

### 3. 性能分析能力
- 结合 span 的 duration_ms 和日志的 timestamp
- 可以分析每个工具的性能瓶颈
- 支持跨会话的性能趋势分析

### 4. 成本监控
- 自动跟踪 token 消耗和成本
- 按 trace/session/stage 聚合成本
- 支持成本预警和优化

---

## 风险与缓解

### 风险 1: 性能开销
**影响**: 每次日志写入增加 trace 查询开销
**缓解**: 
- TraceContext 是内存对象，查询很快
- 只在需要时才写入 trace_id/span_id
- 异步写入日志文件

### 风险 2: 向后兼容性
**影响**: 现有日志文件缺少 trace_id/span_id 字段
**缓解**:
- 新字段设为 Optional
- 旧日志仍可正常解析
- API 端点支持缺失字段的情况

### 风险 3: 测试覆盖
**影响**: 集成后可能引入新 bug
**缓解**:
- 保持现有测试通过
- 添加集成测试
- 逐步集成，每个阶段验证

---

## 实施顺序

1. ✅ 阶段 1: 扩展 Schema（5 分钟）
2. ✅ 阶段 2: 增强 StructuredLogger（15 分钟）
3. ✅ 阶段 3: 集成到 langgraph_agent.py（30 分钟）
4. ✅ 阶段 4: 添加日志查询 API（20 分钟）
5. ✅ 阶段 5: 更新测试（20 分钟）

**总计**: 约 1.5 小时

---

## 验收标准

1. ✅ 所有现有测试通过（210+ passed）
2. ✅ 新增测试通过（至少 7 个新测试）
3. ✅ 日志文件包含 trace_id 和 span_id 字段
4. ✅ API 端点可以按 trace_id 查询日志
5. ✅ 运行指标文件正确生成
6. ✅ 无性能回归（日志写入 < 1ms）

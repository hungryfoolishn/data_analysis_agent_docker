# 会话续接记录（recovery matrix 批次）

更新时间：2026-04-01

## 本次已完成

### 1. recovery action schema 已落地
文件：`langgraph_langchain/schemas.py`

已新增：
- `RecoveryAction = Literal[
    "retry_same_scope",
    "retry_narrower_scope",
    "user_action_required",
  ]`

并扩展：
- `FailureInfo.recovery_action: Optional[RecoveryAction] = None`

### 2. API 层 recovery matrix 已统一
文件：`langgraph_langchain/api_server_langgraph.py`

已新增：
- `_failure_policy(code)`

当前统一策略：
- `cancelled` -> `retry_same_scope`
- `python_execution_error` -> `retry_narrower_scope`
- `max_steps_exceeded` -> `retry_narrower_scope`
- `report_rejected` -> `retry_narrower_scope`
- `missing_data_file` -> `user_action_required`
- `session_not_found` -> `user_action_required`
- `session_workspace_missing` -> `user_action_required`
- `session_expired` -> `user_action_required`

`_failure_detail(...)` 现在会统一补齐：
- `retryable`
- `hint`
- `recovery_action`

并且这些入口已接入统一策略：
- `_ensure_session_consistency(...)`
- `_resolve_data_file(...)`
- `_structured_failure_from_output(...)`
- `_run_analysis(...)`

### 3. runtime recovery_action 已贯通
文件：`langgraph_langchain/langgraph_agent.py`

已完成：
- `_Session.fail_stage(...)` 支持 `recovery_action`
- `finish_report` 在 `synthesis` 前提交 -> `report_rejected` + `retry_narrower_scope`
- repeated `python_repl` errors -> `python_execution_error` + `retry_narrower_scope`
- max tool-step exceeded -> `max_steps_exceeded` + `retry_narrower_scope`
- cancelled -> `cancelled` + `retry_same_scope`

## 测试状态

文件：`langgraph_langchain/test_reliability.py`

已补充：
- session / request / analysis failure 的 `recovery_action` 断言
- finish_report 早提交时的 runtime `recovery_action` 断言
- cancelled 的 API 映射断言

执行结果：
```bash
pytest /python/pragrams/data_analysis_agent/langgraph_langchain/test_reliability.py -q
```

结果：
- `100 passed`

## 当前任务状态

已完成：
- recovery action schema
- centralize recovery matrix
- thread runtime recovery actions
- recovery matrix tests

## 下一个最值得继续的批次

按之前确定的顺序，下一步做：

### 第 2 批：把 stage / failure 更完整透出到 API 输出
重点建议：
1. 让非流式和流式输出都更稳定暴露：
   - `stage`
   - `failure.code`
   - `failure.message`
   - `retryable`
   - `recovery_action`
   - `hint`
2. 明确区分：
   - 运行中阶段变化
   - 最终失败
   - 取消结束
   - report gate reject
3. 保持和当前 `_failure_policy(...)` 一致，不要再新增分散映射
4. 优先只做最小可用透出，不先做 benchmark / trace / lineage

## 如果下次让我直接继续

可以直接说：
- “继续下一批，把 stage/failure 更完整透出到 API 输出”

我就会从这里接着做，不需要重新梳理上下文。

# B5 / M5a: Runtime V2 默认与回退策略

## 默认策略

- `RUNTIME_V2_ENABLED` 默认 `false`：Runtime V2 仍是显式启用。
- `RUNTIME_V2_FALLBACK_ENABLED` 默认 `false`：不再隐式 fallback 到 Legacy V1。
- `RUNTIME_V2_STRICT_MODE` 默认 `true`：V2 基础设施失败也必须暴露错误。

Fallback 只有在同时满足以下条件时才可能发生：

1. Runtime V2 已显式启用；
2. `RUNTIME_V2_FALLBACK_ENABLED=true`；
3. `RUNTIME_V2_STRICT_MODE=false`；
4. V2 发生基础设施类停止（`blocked` / `incomplete` / `limit_reached`）且没有结果。

业务失败、工具失败、数据校验失败、Asset 解析失败、Snapshot 完整性失败、
SQL 安全失败都不会 fallback。

## 新增策略模块

```text
langgraph_langchain/runtime/runtime_route.py
```

- `RuntimeSelectionPolicy.from_env()`
- `decide_runtime_route()`
- `log_runtime_route()`

路由结果固定输出：

```text
selected_runtime
fallback
reason
error_type
```

日志同时输出关联的 `task_id` 和 `execution_id`，但不输出用户数据或敏感连接信息。

## 观测语义

- `runtime_v2_disabled`
- `runtime_v2_completed`
- `runtime_v2_task_failure`
- `runtime_v2_infrastructure_failure`
- `runtime_v2_infrastructure_failure_strict_mode`

## 测试覆盖

- 默认 fallback 关闭；
- 环境变量显式覆盖；
- V2 正常完成；
- 业务失败不 fallback；
- strict mode 下基础设施失败不 fallback；
- 显式非 strict 模式允许基础设施 fallback；
- V2 禁用时路由 V1；
- route 日志包含 task / execution ID 且无敏感数据。

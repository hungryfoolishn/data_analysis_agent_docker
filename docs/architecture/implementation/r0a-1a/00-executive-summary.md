# R0a-1A Executive Summary — Execution Entry Hardening

- 日期：2026-11-24
- 基线：`codex/r0a-1a-execution-entry-hardening`（自 `codex/a1.3.3-canary-readiness @ 4a9b254` 创建）
- 最终状态：PASS_WITH_KNOWN_LIMITATIONS（见 R0A_1A_COMPLETION_REPORT.md）

## 完成内容

1. **统一执行策略**：新增 `langgraph_langchain/execution/execution_policy.py`，
   所有产品可达的动态 Python 执行入口必须经 `resolve_execution_backend()`
   解析后端，并经 `WorkerDispatcher` 分发。Fail-Closed：未配置、未知配置、
   后端不合格时拒绝执行，无宿主进程内联执行、无自动降级。
2. **宿主进程执行旁路封堵**：`delegate_analysis`（`tools/tool_delegate.py`）
   原先在 API/Agent 宿主进程内以 `exec(compile(code, ...), sub_ns)` 直接执行
   LLM 生成代码，已迁移到统一执行边界（策略 → Dispatcher → 后端）。
   模块内 `exec(` 计数归零（回归测试守护）。
3. **python_repl / Runtime V2 任务**：`_Session.run_code()`（`langgraph_agent.py`）
   与 `PythonTaskExecutor` 不再硬编码 `ProcessWorkerBackend`，改为策略解析
   （docker / process+显式 opt-in / 拒绝）。
4. **截断标记修复**：`python_executor.py::_trim` 将截断标记计入长度，
   修复父子双重截断导致标记丢失的问题。
5. **测试**：新增 `tests/test_execution_policy.py`（14 tests）、
   `tests/test_execution_entry_hardening.py`（8 tests）、`tests/conftest.py`
   （测试环境显式选择 process + opt-in）。

## 关键修改文件

| 文件 | 变更 |
|---|---|
| `langgraph_langchain/execution/execution_policy.py` | 新增：统一执行策略 |
| `langgraph_langchain/execution/__init__.py` | 导出策略符号 |
| `langgraph_langchain/langgraph_agent.py` | `run_code` 经策略+Dispatcher（:1162-1164 SECURITY_DENIED） |
| `langgraph_langchain/execution/python_task_executor.py` | 懒策略解析，拒绝时返回 `ExecutionPolicyDenied` |
| `langgraph_langchain/tools/tool_delegate.py` | 移除宿主进程内联 exec，迁移至 Dispatcher |
| `langgraph_langchain/execution/python_executor.py` | `_trim` 截断标记保留修复 |

## 明确不做（边界）

- 不接入 Docker Worker 全链路（R0a-1B）。
- 不修改 `RUNTIME_V2_ENABLED` 默认值。
- 不实施 Session 授权（R0b）、Evidence 边界（R0a-1C）。
- 不修改 B11 冻结契约。

## Gate 结论速览

G1-G8、G9-G11：通过（证据见 06/07 号文档）。G12：本文档集 + 提交。
最终状态：PASS_WITH_KNOWN_LIMITATIONS —— 执行边界已封堵，但生产部署在
R0a-1B 完成 Docker 主链路接入前必须显式配置 `WORKER_BACKEND`；
`process` 后端执行不可信代码需显式 opt-in 且不是安全沙箱。

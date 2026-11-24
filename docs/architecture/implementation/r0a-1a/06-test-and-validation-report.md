# 06 — 测试与验证报告

## 测试环境

- docker-host，conda env `analysis`（Python 3.10.18, pytest 9.1.1）
- 工作区：`/python/pragrams/data_analysis_agent_r0a1a`
- 测试环境后端配置（`tests/conftest.py`，显式声明）：
  `WORKER_BACKEND=process`、`ALLOW_UNTRUSTED_PROCESS_EXECUTION=1`

## 实施前基线（真实执行）

```
python -m pytest tests/test_tool_delegate.py tests/test_worker_backend.py \
  tests/test_worker_dispatcher.py tests/test_isolated_python_executor.py \
  tests/test_docker_worker_backend.py tests/test_session_persistence.py \
  tests/test_platform_runtime.py -q
→ 105 passed, 2 warnings in 44.71s
```

## 新增测试（真实执行）

```
python -m pytest tests/test_execution_policy.py tests/test_execution_entry_hardening.py -q
→ 22 passed in 1.35s
```

覆盖矩阵（对应任务书 7.1）：

| 测试要求 | 测试 |
|---|---|
| Backend 未配置 → 拒绝 | `test_untrusted_code_is_refused_when_no_backend_is_configured`；`test_delegate_fails_closed_when_backend_is_not_configured`；`test_executor_refuses_execution_when_policy_denies` |
| 未知 Backend → 拒绝 | `test_unknown_backend_value_fails_closed`、`test_unknown_backend_never_returns_a_backend` |
| process 无 opt-in → 拒绝 | `test_untrusted_code_refused_without_explicit_opt_in`、opt-in 值白名单测试 |
| Docker 不可用 → 不回退 process | `test_delegate_never_falls_back_to_process_when_docker_fails`、`test_executor_does_not_fall_back_when_docker_backend_fails`（守卫式断言：ProcessWorkerBackend 构造被监控） |
| delegate 不再宿主进程执行 | `test_delegate_code_executes_outside_the_host_process`（PID 哨兵：输出 pid != 宿主 pid）、`test_delegate_module_contains_no_inline_exec`（源码级守护） |
| python_repl/统一入口经 Dispatcher | `test_run_code_executes_outside_the_host_process`（PID 哨兵）、`test_executor_runs_code_through_dispatcher_in_child_process` |
| 执行异常不触发 Inline fallback | `test_delegate_reports_code_errors_as_failed` + 守卫式 mock |
| 安全拒绝可区分 | `[SECURITY_DENIED]` 前缀断言、`ExecutionPolicyDenied` 错误类型断言 |
| 策略记录冻结/契约 | `test_resolved_record_is_frozen` |

安全哨兵机制：宿主进程 PID 对比、源码 exec 计数、Dispatcher/Backend 构造
守卫 mock。未在测试中执行真实破坏性 payload。

## 修复后回归（真实执行）

```
python -m pytest tests/test_tool_delegate.py tests/test_isolated_python_executor.py \
  tests/test_execution_policy.py tests/test_execution_entry_hardening.py -q
→ 56 passed in 4.90s
```

发现并修复的回归：
1. `test_output_is_truncated_for_large_results` 失败——根因：子进程
   `_trim` 在 max 长度后追加标记，父进程 `_map_result` 再次硬切片到 max，
   标记被切掉（标记丢失属于双重截断缺陷，同样影响 run_code 输出）。
   修复：`_trim` 将标记计入截断长度（`python_executor.py:77-84`）。
   修复后该测试通过。

## 全量回归

见 `R0A_1A_COMPLETION_REPORT.md`（提交前实际运行结果与基线对比）。

## 未执行/不适用

- Docker Worker 真实容器端到端执行：R0a-1B 范围（本阶段仅验证
  docker 后端选择与 fail-closed 行为，未伪造 E2E）。

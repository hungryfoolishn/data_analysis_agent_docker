# 02 — 实施后执行入口清单（After）

实施 HEAD：`codex/r0a-1a-execution-entry-hardening`（见提交）。

## 全局复扫结果（实施后）

```bash
grep -rn --include="*.py" -F "exec(" langgraph_langchain/ docker/
```

| 位置 | 性质 | 安全状态 |
|---|---|---|
| `runtime/package.py:117` | ZIP 内 replay 脚本模板（字符串字面量，非运行时执行） | SAFE |
| `execution/python_executor.py:189` | IsolatedPythonExecutor 子进程内（fork/spawn 隔离、env 清空、RLIMIT_AS、file audit hook） | CONDITIONAL（策略限定） |
| `docker/worker/worker_entrypoint.py:203` | Docker 容器内（network none 等） | SAFE |

`eval(`：0 处。`compile(`：仅语法预校验（`langgraph_agent.py:1108`）与
executor 内部 exec 组合。`subprocess/os.system/Popen/importlib/__import__/
marshal/cloudpickle/dill`：0 处。`multiprocessing`：仅 IsolatedPythonExecutor。

## 实施后入口处置表

| Entry ID | File | 修改后处置 | Dispatcher | Security Status |
|---|---|---|---|---|
| E-01 delegate_analysis | `tools/tool_delegate.py:150-215` | **迁移**：`exec` 移除；代码经 `resolve_execution_backend(UNTRUSTED)` → `WorkerDispatcher` → 后端子进程/容器执行；策略拒绝时返回 `[SECURITY_DENIED]`；timeout/cancelled 由后端状态处理；depth 计数在所有退出路径递减 | 是 | FAIL-CLOSED（无宿主进程执行路径） |
| E-02 python_repl (`run_code`) | `langgraph_agent.py:1133-1167` | **策略化**：`ProcessWorkerBackend` 硬编码移除；策略解析失败返回 `[SECURITY_DENIED]`（:1164）；保留黑名单校验与仅语法 compile | 是 | CONDITIONAL→FAIL-CLOSED |
| E-03 IsolatedPythonExecutor | `execution/python_executor.py` | 保留为 process 后端底层；`_trim` 标记修复 | 底层 | CONDITIONAL |
| E-04 PythonTaskExecutor | `execution/python_task_executor.py:127-152` | **策略化**：懒解析 `self._resolved_backend`；拒绝时 `ExecutionResult(status=failed, error.type=ExecutionPolicyDenied)`；无 fallback | 是 | FAIL-CLOSED |
| E-06 Docker worker | 不变 | 不变 | 是 | SAFE |
| E-07 replay 模板 | 不变 | 不变 | n/a | SAFE |
| E-09 SQL 工具 | 不变 | 记录（非 Python 代码执行） | n/a | CONDITIONAL（范围外） |

## 修改后统一调用链

```text
Agent Tool (python_repl / delegate_analysis) 或 Runtime V2 python 任务
    |
    v
ExecutionPolicy.resolve_execution_backend(UNTRUSTED)   # fail closed
    |
    v
WorkerDispatcher(resolved.backend, backend_name=...)   # 统一分发
    |
    v
ProcessWorkerBackend（显式 opt-in，受限子进程）
    或 DockerWorkerBackend（WORKER_BACKEND=docker，容器隔离）
    |
    v
WorkerResult（succeeded / timed_out / cancelled / failed）
```

## 仍存在的执行入口（明确声明）

1. `WORKER_BACKEND=process` + `ALLOW_UNTRUSTED_PROCESS_EXECUTION=1` 时，
   不可信代码在**受限子进程**（非容器）中执行 —— 显式操作者选择，
   文档声明为非沙箱（Known Limitation，R0a-1B 由 Docker 替代）。
2. `WORKER_BACKEND=docker` 时全部执行进入容器；Docker 不可用 → 后端返回
   `BACKEND_UNAVAILABLE` 失败，**无** process 回退（回归测试守护）。

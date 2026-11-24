# 03 — 执行边界设计（Execution Boundary Design）

## 目标

任何由用户输入、LLM 输出或外部数据诱导产生的动态代码执行，必须经过
明确的执行策略与受控执行入口；禁止在 API Server / Agent 主进程等宿主
进程中直接执行不可信代码。

## 复用的既有契约（不新建框架）

- `WorkerBackend` / `WorkerDispatcher`（`execution/worker_backend.py`、
  `execution/worker_dispatcher.py`）：既有统一分发器，
  `create_worker_backend` 已保证 unknown 不静默回退。
- `WorkerJob` / `build_process_worker_job`（`execution/job_contract.py`）：
  便携任务契约，不携带运行时对象。
- `DockerWorkerBackend`（A1.4.2.2.x 冻结）：全部失败路径 fail-closed，
  无 process 回退（类文档明示）。

## 新增：ExecutionPolicy（`execution/execution_policy.py`）

### 信任分级

| 级别 | 定义 | 使用方 |
|---|---|---|
| `TRUSTED_INTERNAL` | 平台内置固定代码，非用户/LLM/外部数据衍生 | 内部机制默认路径 |
| `UNTRUSTED` | 内容源自用户请求 / LLM 输出 / 外部数据 | python_repl、delegate_analysis、Runtime V2 python 任务 |

### 后端解析规则（fail closed）

| `WORKER_BACKEND` | UNTRUSTED | TRUSTED_INTERNAL |
|---|---|---|
| `docker` | DockerWorkerBackend（容器失败→任务失败，不回退） | 同左 |
| `process` | 需 `ALLOW_UNTRUSTED_PROCESS_EXECUTION=1` 显式 opt-in；否则 `UnsafeExecutionBackendDeniedError` | 允许（`insecure=False`） |
| 未设置 | `ExecutionBackendNotConfiguredError`（拒绝执行） | ProcessWorkerBackend（`default_trusted_only`） |
| 未知值 | `UnknownExecutionBackendError` | 同左 |

设计依据：任务书 4.3——"不允许因为配置缺失而自动选择不安全后端"；
"不允许未知 backend 静默回退"；"不允许 Docker 初始化失败后自动回退 Process"。

### Fail-Closed 矩阵（实现与测试对应）

| 情况 | 行为 | 实现位置 | 测试 |
|---|---|---|---|
| Backend 未配置 | 拒绝执行 | `execution_policy.py:98-105` | `test_untrusted_code_is_refused_when_no_backend_is_configured` 等 |
| Backend 类型未知 | 拒绝执行 | `execution_policy.py:107-112` | `test_unknown_backend_value_fails_closed` |
| Backend 不合格（process+untrusted 无 opt-in） | 拒绝执行 | `execution_policy.py:88-96` | `test_untrusted_code_refused_without_explicit_opt_in` |
| Docker Backend 不可用 | 任务失败（BACKEND_UNAVAILABLE），不回退 process | `worker_backend.py` fail-closed + 策略无 fallback | `test_delegate_never_falls_back_to_process_when_docker_fails`、`test_executor_does_not_fall_back_when_docker_backend_fails` |
| Worker 超时 | 返回受控超时错误（不重试执行） | `tool_delegate.py` timed_out 分支；`run_code` 既有分支 | 既有 delegate/run_code 超时路径 |
| Worker 取消 | 返回 cancelled（不重试） | `tool_delegate.py` cancelled 分支 | 既有取消测试（test_runtime_v2_cancellation 等） |
| Worker 返回非法协议 | 后端返回 CONTRACT_INVALID 失败 | DockerWorkerBackend 协议校验 | test_docker_worker_backend 既有覆盖 |
| 策略错误被吞 | 禁止：run_code/delegate 返回 `[SECURITY_DENIED]` 前缀，PythonTaskExecutor 返回 `ExecutionPolicyDenied` | 各入口 | `test_run_code_policy_denial_message_is_distinguishable` |

## 为什么 process 后端保留 opt-in 而非彻底禁止

彻底禁止将使产品在全部分析能力上不可用（违反任务书"不得通过禁用所有
分析能力来伪装安全修复"），且 R0a-1B 尚未完成 Docker 主链路接入。
`ALLOW_UNTRUSTED_PROCESS_EXECUTION=1` 是显式、可审计、有文档的操作者
决定；conftest 中为测试环境显式声明。该限制在 R0a-1B 完成 Docker 接入
后收敛（生产 compose 移除 opt-in，仅保留 docker）。

## ProcessWorkerBackend 能力声明（不夸大）

`worker_backend.py:3` 原文："The process backend deliberately wraps the
existing IsolatedPythonExecutor. It is process isolation for reliable
cancellation, not a replacement for an OS/container security sandbox."
实际能力：子进程（setsid）、环境清空、RLIMIT_AS 内存上限、file audit
hook（open 限制在 workspace 与可信读根）、timeout、cancellation。
不具备：网络隔离、容器隔离、文件系统强隔离（capabilities 显式声明）。

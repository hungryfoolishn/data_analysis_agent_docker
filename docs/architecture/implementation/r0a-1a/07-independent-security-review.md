# 07 — 独立安全复核（Independent Security Review）

复核方式：全局复扫 + 调用链重查 + 反向审查；复核证据均来自实施后工作区。

## 全局复扫（实施后）

`exec(`：3 处（replay 模板字符串 / executor 子进程 / docker worker 容器）。
`eval(`：0。宿主进程内联执行：0（`tool_delegate.py` exec 计数归零，
有源码级回归测试）。`subprocess/os.system/Popen/__import__/importlib/
marshal/cloudpickle/dill`：0。`multiprocessing`：仅 IsolatedPythonExecutor。

## 反向审查结论（对应任务书 8.4）

| # | 问题 | 结论 |
|---|---|---|
| 1 | 是否只改了 delegate_analysis，遗漏相同执行函数的其他调用方？ | 否。全局 exec 复扫确认仅剩 3 处非宿主进程执行点；delegate 无其他调用方（`grep delegate_analysis` 全仓复核：注册表 + agent 工具集 + 测试） |
| 2 | 是否只封堵 API 入口、遗漏 Agent 内部入口？ | 否。run_code（Agent 内部）与 PythonTaskExecutor（Runtime V2 内部）均策略化 |
| 3 | 是否存在异步/后台线程/回调绕过边界？ | 复核：原 daemon 线程执行已删除；`grep -rn "Thread(" langgraph_langchain/` 无代码执行线程（仅 UI/日志类线程）；Runtime V2 调度经 PythonTaskExecutor |
| 4 | 是否存在旧 Runtime 路径未封堵？ | Legacy ReAct 的代码执行唯一点是 run_code（已策略化）；Runtime V2 经 PythonTaskExecutor（已策略化） |
| 5 | 是否存在 Tool 包装层绕过 Dispatcher？ | 复核 tools/ 全目录：无其他 exec/eval 调用点 |
| 6 | ProcessWorkerBackend 是否被误描述为安全 Sandbox？ | 否。类文档原文明示非沙箱；策略返回值带 `sandboxed=False, insecure=True`（opt-in 时）；本设计文档重申 |
| 7 | Docker 不可用是否有任何自动降级？ | 否。DockerWorkerBackend 所有异常→失败结果（含 BACKEND_UNAVAILABLE）；策略层无 fallback；双守卫测试证明 process 构造未发生 |
| 8 | 是否因删除/停用 Tool 破坏既有行为？ | 否。delegate_analysis 保留注册与 schema，功能迁移（105 基线测试中除 1 个已修复截断缺陷外全部通过） |
| 9 | 测试是否只 mock Dispatcher 而未证明真实链路？ | 否。PID 哨兵测试使用真实 process 后端子进程执行；mock 仅用于 fail-closed 反向断言 |
| 10 | 安全错误是否被普通异常处理吞掉？ | 否。`[SECURITY_DENIED]` 前缀 + `ExecutionPolicyDenied` 错误类型；dispatcher 不捕获策略错误（策略错误发生在 dispatcher 构造前） |
| 11 | 不可信代码能否经 Shell 或其他 Tool 执行？ | 无 Shell/Notebook 产品工具；SQL 工具执行 SQL（非 Python），记录为范围外 |
| 12 | 未解决问题是否明确移交？ | 是，见 08 号文档 |

## 无法证明的项（标记 UNKNOWN，不标 SAFE）

- Docker Worker 在真实容器中的端到端执行（WORKER_BACKEND=docker 时
  delegate/python_repl 的 namespace 语义、超时、取消、artifact 行为）：
  **UNKNOWN**，R0a-1B 验证。
- 非产品可达路径（测试/脚本/历史归档中的动态执行）的完备性：本轮以
  产品目录（langgraph_langchain/、docker/）为扫描范围，仓库根级脚本
  未逐一审计：**UNKNOWN（范围外）**。

## 复核结论

未发现新的未处理高风险执行旁路；未发现自动降级；未发现遗漏调用方。
产品可达的宿主进程不可信代码执行路径已全部封堵。

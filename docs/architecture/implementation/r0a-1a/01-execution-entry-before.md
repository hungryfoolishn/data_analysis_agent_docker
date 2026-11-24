# 01 — 实施前执行入口清单（Before）

基线 HEAD：`4a9b25474ddf5a8163e8e8925d8e19d128848d87`（工作区干净）。

## 搜索范围与命令

```bash
grep -rn --include="*.py" -F "exec(" langgraph_langchain/ docker/
grep -rn --include="*.py" -F "eval("  langgraph_langchain/ docker/
grep -rn --include="*.py" -F "compile(" langgraph_langchain/ docker/
grep -rn --include="*.py" -E "subprocess|Popen|os\.system|multiprocessing|__import__|importlib|pickle|marshal|cloudpickle|dill" langgraph_langchain/ docker/
```

并逐项追踪调用链（不依赖 grep 表面结果）。

## 实施前动态执行入口盘点

| Entry ID | File | Function | Caller / 调用链 | Reachability | Input Origin | Execution Location | Dispatcher | Security Status | Required Action |
|---|---|---|---|---|---|---|---|---|---|
| E-01 | `tools/tool_delegate.py:163` | `delegate_analysis` | Agent Tool 注册表 → LLM 选择 | 产品可达（Agent 工具） | LLM 生成代码 | **API/Agent 宿主进程**（daemon 线程 + `exec(compile(...), sub_ns)`） | 否 | **UNSAFE (P0)** | 迁移到统一执行边界 |
| E-02 | `langgraph_agent.py:1098-1157` | `_Session.run_code`（python_repl 工具） | Agent Tool 循环 → `session.run_code` | 产品可达 | LLM 生成代码（经 `_validate_code_safety` 黑名单 + 仅语法 `compile`） | IsolatedPythonExecutor 子进程（env 清空、RLIMIT_AS、file audit hook、timeout） | 是（但硬编码 `ProcessWorkerBackend`） | CONDITIONAL | 后端选择交给策略；保留语法预校验 |
| E-03 | `execution/python_executor.py:189` | `IsolatedPythonExecutor._worker` | `ProcessWorkerBackend.execute`（E-02/E-04 的底层） | 产品可达（作为 E-02/E-04 底层） | 继承入口的信任级别 | multiprocessing 子进程（fork/spawn），`os.setsid()`，环境清空，内存限制，file audit hook | 底层实现 | CONDITIONAL（文档明确"非 OS/容器沙箱"） | 保留；由策略限定其用途 |
| E-04 | `execution/python_task_executor.py:43` | `PythonTaskExecutor` | Runtime V2 `build_execution_controller(python_executor=...)`（`langgraph_agent.py:1675`） | 产品可达（Runtime V2 python 任务） | LLM/计划生成代码 | 经 Dispatcher → ProcessWorkerBackend 子进程 | 是（硬编码 process） | CONDITIONAL | 后端选择交给策略 |
| E-05 | `runtime/graph.py:202,228` + `runtime/context.py:1297,1314` | Runtime V2 执行控制器 | Agent 主流程 | 产品可达 | 计划任务 | 经 E-04 | 间接 | CONDITIONAL | 无需修改（经 E-04 收口） |
| E-06 | `docker/worker/worker_entrypoint.py:203` | worker 主循环 | Docker 容器内（A1.4.2.2.x） | 产品可达（当 WORKER_BACKEND=docker） | WorkerJob.code | **Docker 容器**（network none、cap-drop ALL、read-only rootfs、非 root、资源限制） | 是 | SAFE（设计目标） | 保留 |
| E-07 | `runtime/package.py:117` | `build_analysis_package` 生成的 replay 脚本模板 | 分析包导出（API `/download` 类路径） | 产品可达（导出物） | 历史 python_repl 成功步骤代码 | **不执行**：exec 位于三引号字符串模板中，仅作为 ZIP 内 replay.py 文本导出 | 否 | SAFE（代码生成物，非运行时执行；模板头部已声明"Review the generated code before running it"） | 保留 |
| E-08 | `langgraph_agent.py:1108` | `run_code` 语法预校验 | E-02 内部 | 产品可达 | LLM 代码 | 仅 `compile()` 语法检查，不执行 | n/a | SAFE | 保留 |
| E-09 | SQL 工具（`tools/sqlite_sql_tool.py` 等） | SQL 执行 | Agent 工具 | 产品可达 | LLM 生成 SQL | 数据库引擎（非 Python 代码执行） | n/a | CONDITIONAL（SQL 注入/越权属数据库边界，非本阶段范围） | 记录，归属后续任务 |
| E-10 | Multi-Agent（`runtime/multi_agent.py`） | Planner/Reviewer | B11 编排 | 产品可达（V2 启用时） | LLM 文本（非代码执行） | 无代码执行 | n/a | SAFE | 记录 |

其他搜索结论：`eval(` 全仓产品代码 0 处；`subprocess/os.system/Popen` 0 处
（仅出现在 `_DANGEROUS_PATTERNS` 黑名单正则中）；`importlib/__import__` 0 处；
`pickle` 仅用于子进程命名空间序列化（E-03 内部机制）；`marshal/cloudpickle/dill` 0 处。

## 实施前测试基线

```
python -m pytest tests/test_tool_delegate.py tests/test_worker_backend.py \
  tests/test_worker_dispatcher.py tests/test_isolated_python_executor.py \
  tests/test_docker_worker_backend.py tests/test_session_persistence.py \
  tests/test_platform_runtime.py -q
→ 105 passed, 2 warnings in 44.71s
```

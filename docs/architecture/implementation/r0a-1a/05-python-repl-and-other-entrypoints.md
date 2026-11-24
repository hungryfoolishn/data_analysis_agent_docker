# 05 — python_repl 与其他执行入口处置

## python_repl（`_Session.run_code`，`langgraph_agent.py`）

修改内容（`run_code` 内，原硬编码块替换）：

- 移除：`WorkerDispatcher(ProcessWorkerBackend(), backend_name="process")` 硬编码。
- 新增：`resolve_execution_backend(UNTRUSTED)` 策略解析；策略错误返回
  `[SECURITY_DENIED] {exc}`（可区分的安全拒绝，Agent 不会将其当作普通
  分析错误重试执行）。
- 保留：`_validate_code_safety` 黑名单（os.system/subprocess/eval/exec/
  compile/`__import__` 等）——纵深防御，非安全边界本体；仅语法 `compile`
  预校验（不执行）；成功结果的 namespace 合并语义（ProcessWorkerResult）。
- 执行位置：与修改前相同（IsolatedPythonExecutor 子进程），但后端来源
  变为策略控制：`WORKER_BACKEND=docker` 时进入容器（namespace 更新等
  产品级差异由 R0a-1B 端到端验证）。

## Runtime V2 python 任务（`PythonTaskExecutor`）

- `__init__` 不再构造 `ProcessWorkerBackend`；`self._resolved_backend`
  懒解析（首次 execute 时策略决定并缓存）。
- 策略拒绝 → `build_execution_result(status="failed",
  error={"type": "ExecutionPolicyDenied", ...})`；不会落到任何内联执行。
- 显式注入 `executor=IsolatedPythonExecutor(...)` 的旧兼容路径保留，
  但文档标注仅限 dev/test；产品入口（`langgraph_agent.py:1675`）不注入。
- Runtime V2 控制器（`runtime/graph.py`、`runtime/context.py`）无需修改：
  经 PythonTaskExecutor 收口。

## 其他入口处置

| 入口 | 处置 | 理由 |
|---|---|---|
| SQL 工具 | 记录，范围外 | 数据库引擎执行 SQL，非 Python 代码执行；注入/越权属数据库边界任务 |
| Multi-Agent（B11） | 记录，SAFE | Planner/Reviewer 仅产生文本与计划，无代码执行 |
| Shell/Notebook 工具 | 不存在 | 全仓搜索无此类产品工具 |
| replay 模板（`runtime/package.py`） | 保留 | 字符串模板代码生成物，导出物明示需人工审查后运行 |
| Acquisition 工具 | 记录，范围外 | 外部数据获取（HTTP），无动态代码执行；SSRF 防护属 R0b/R4 |
| 测试/开发脚本中的 exec | 不修改 | 非产品可达路径（任务书 2.3 明确分类要求） |

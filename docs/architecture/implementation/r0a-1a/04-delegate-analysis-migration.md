# 04 — delegate_analysis 迁移记录

## 修改前调用链（P0 风险）

```text
LLM 选择 delegate_analysis 工具
  → tools/tool_delegate.py delegate_analysis()
  → 构建 sub_ns（df.copy()、pd、np、session helpers）
  → threading.Thread(_target) 守护线程
  → exec(compile(code, "<delegated>", "exec"), sub_ns)   # 宿主进程内执行
```

风险：LLM 生成代码在 API/Agent 宿主进程内执行，共享宿主内存、环境变量、
文件权限；且超时后守护线程**无法被杀死**（t.join 超时后线程仍在运行），
失控代码可持续占用宿主进程。

## 修改后调用链

```text
LLM 选择 delegate_analysis 工具
  → delegate_analysis()（stage 校验、depth guard、输入校验不变）
  → resolve_execution_backend(UNTRUSTED)            # fail closed
      失败 → depth 递减 + recorder.fail(ExecutionPolicyDenied)
           + tool_error("[SECURITY_DENIED] delegate_analysis refused: ...")
  → PythonExecutionRequest(code, workspace, source_path, namespace=sub_ns,
      timeout=_CODE_TIMEOUT, max_output_chars=_MAX_OUTPUT_LEN,
      allowed_directories=[workspace])
  → build_process_worker_job(...)
  → WorkerDispatcher(resolved.backend).execute(job, cancel_event)
      → succeeded: output = stdout (+stderr)；success=True
      → timed_out: recorder.fail(TimeoutError) + tool_error
      → cancelled: recorder.fail(CancelledError) + tool_error
      → failed:    output = "[ERROR]\n" + (stdout+stderr 或 safe_message)
  → 截断 → 工件扫描（新增图片登记）→ depth 递减 → JSON 结果
```

## 行为保持

- df 副本语义：namespace 传入 `df.copy()`，与原实现一致
  （测试 `test_sub_task_does_not_mutate_original_df` 继续通过）。
- `save_fig` / `fix_chinese` / `load_csv` / `safe_workspace_path`：
  子进程原生提供（`python_executor.py::_worker` namespace）。
- fork 模式下 session.ns 中的分析 helper 闭包随内存继承
  （`_fork_namespace`）；spawn 模式下不可序列化项被过滤。
- 工件追踪：子进程在 workspace 产生的新文件照常进入
  `session.known_image_files`（测试 `test_new_images_tracked` 通过）。
- 输出截断：保留 delegate 层截断；并修复 `_trim` 标记丢失（见 06）。
- depth guard：所有退出路径（策略拒绝、timeout、cancelled、异常结果）
  均递减 `_delegation_depth`（测试 `test_depth_resets_on_error` 通过）。

## 移除的执行旁路

- 宿主进程 `exec()`（模块内归零，`inspect.getsource` 回归测试守护）。
- 宿主进程 `sys.stdout/stderr` 全局重定向（副作用：会污染并发线程输出）。
- 守护线程泄漏（超时后无法终止）——现在由后端 timeout 真正终止子进程。

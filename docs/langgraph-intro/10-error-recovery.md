# 10 - 错误恢复与运行时防护详解

> 本文深入解析项目的多层错误防护体系：运行时防护栏、错误分类、恢复策略、自动重试机制。涵盖 P2 新增的智能错误分类器和抖动退避机制。

---

## 10.1 五层防护体系

```
Layer 1: LangGraph 递归限制
    recursion_limit = 60（框架层安全网）
         │
         ▼
Layer 2: 业务步数限制
    _MAX_AGENT_STEPS = 48（总步数上限）
         │
         ▼
Layer 3: 工具级防护栏
    ├── python_repl: 行数限制(50)、超时(60s)、步骤标记
    ├── finish_report: 最少 finding、报告结构验证
    ├── delegate_analysis: 嵌套深度限制(1)、DataFrame 隔离
    └── 所有工具: 阶段权限验证
         │
         ▼
Layer 4: 智能错误分类（P2 新增）
    ├── error_classifier.py — 11 种错误类型分类
    ├── retry_utils.py — 抖动指数退避
    └── 优先级匹配：状态码 → 消息模式 → 异常类型
         │
         ▼
Layer 5: 错误恢复策略
    ├── 自动重试（同范围/缩小范围）
    ├── 降级策略
    └── 用户提示
```

---

## 10.2 运行时防护栏

### 防护栏常量

```python
# langgraph_agent.py:72-76
_MAX_OUTPUT_LEN = 3000               # 工具输出最大 3000 字符
_CODE_TIMEOUT = 60                   # 代码执行 60 秒超时
_MAX_PYTHON_REPL_LINES = 50          # 单步代码最多 50 行
_MAX_AGENT_STEPS = 48                # 总共最多 48 步工具调用
_MAX_CONSECUTIVE_PYTHON_ERRORS = 3   # 最多连续 3 次错误
```

### 为什么是这些数值？

| 限制 | 值 | 原因 |
|------|-----|------|
| 输出长度 | 3000 | LLM 上下文有限，超长输出浪费 tokens |
| 代码超时 | 60s | 数据分析代码通常 <10s，60s 已留充足余量 |
| 代码行数 | 50 | 强制小步骤，避免一次性写大脚本 |
| 总步数 | 48 | 平衡分析深度与成本/时间 |
| 连续错误 | 3 | 3 次错误后说明方向有误，应停止 |

### 代码提交前的验证

```python
# langgraph_agent.py:108-135
def _validate_python_repl_step(code: str) -> Optional[str]:
    """验证代码是否符合规范，返回错误信息或 None"""

    stripped = code.strip()

    # ① 空代码检查
    if not stripped:
        return "[ERROR] Empty python_repl step."

    # ② 行数检查
    code_lines = [line for line in stripped.splitlines() if line.strip()]
    if len(code_lines) > 50:
        return f"[ERROR] Too large ({len(code_lines)} lines; limit 50)."

    # ③ 步骤标记检查（中英双语）
    _REQUIRED_STEP_MARKER_ALIASES = {
        "step objective": ("step objective", "步骤目标", "目标"),
        "method": ("method", "方法"),
        "key results": ("key results", "关键结果", "结果"),
        "suggested next step": ("suggested next step", "建议下一步", "下一步"),
    }

    missing = []
    for canonical, aliases in _REQUIRED_STEP_MARKER_ALIASES.items():
        if not any(alias.lower() in stripped.lower() for alias in aliases):
            missing.append(canonical)

    if missing:
        return f"[ERROR] Missing markers: {', '.join(missing)}."

    return None  # 验证通过
```

### 代码执行超时机制

```python
# langgraph_agent.py:1160-1192
def run_code(self, code: str) -> str:
    """在子线程中执行代码，实现超时控制"""
    buf = io.StringIO()
    had_exception = []

    def _target():
        # 重定向 stdout/stderr 到 buffer
        old_out, old_err = sys.stdout, sys.stderr
        sys.stdout = sys.stderr = buf
        try:
            exec(compile(code, "<agent>", "exec"), self.ns)
        except Exception:
            traceback.print_exc(file=buf)
            had_exception.append(True)
        finally:
            sys.stdout, sys.stderr = old_out, old_err

    # 在守护线程中执行
    t = threading.Thread(target=_target, daemon=True)
    t.start()
    t.join(timeout=60)  # 最多等 60 秒

    if t.is_alive():
        # 线程仍在运行 → 超时
        return "[ERROR] Execution timed out after 60s. Break into smaller steps."

    output = buf.getvalue()
    if had_exception:
        output = "[ERROR]\n" + output
    if len(output) > 3000:
        output = output[:3000] + "\n...[truncated]"
    return output
```

> **为什么用线程而不是 asyncio？**
> `exec()` 是同步阻塞的，无法被 asyncio 取消。通过 `threading.Thread` + `join(timeout)` 实现超时。

---

## 10.3 错误分类体系

### FailureCode 分类（业务层）

```
用户操作类错误（retryable=False, user_action_required）
├── missing_data_file         → "请先上传数据文件"
├── session_not_found         → "会话不存在，请创建新会话"
├── session_workspace_missing → "工作目录缺失，请重新上传"
└── session_expired           → "会话已过期，请创建新会话"

分析过程错误（retryable=True, retry_narrower_scope）
├── python_execution_error    → "使用更小的分析步骤重试"
├── max_steps_exceeded        → "缩小分析范围重试"
├── report_rejected           → "修改报告后重新提交"
├── tool_execution_failed     → "检查工具参数后重试"
├── reasoning_drift           → "聚焦分析方向重试"
├── report_generation_failed  → "确保有足够发现后重试"
└── timeout                   → "简化分析任务重试"

可恢复错误（retryable=True, retry_same_scope）
├── cancelled                → "准备好后重新开始"
└── session_interrupted       → "重新开始分析"
```

### API 错误智能分类（P2 新增）

`error_classifier.py` 提供对 LLM API 调用异常的自动分类，独立于业务层 FailureCode：

```python
# error_classifier.py
class FailoverReason(Enum):
    auth = "auth"                    # 401/403
    rate_limit = "rate_limit"        # 429
    overloaded = "overloaded"        # 503/529
    server_error = "server_error"    # 500/502
    timeout = "timeout"
    context_overflow = "context_overflow"
    billing = "billing"              # 402
    model_not_found = "model_not_found"
    format_error = "format_error"
    unknown = "unknown"

@dataclass
class ClassifiedError:
    reason: FailoverReason
    status_code: int = None
    retryable: bool = True
    should_compress: bool = False    # 建议压缩上下文
    max_retries: int = 3

def classify_api_error(exception, status_code=None, provider="") -> ClassifiedError:
    """优先级匹配：状态码 → 消息模式 → 异常类型 → 兜底"""
```

#### 分类优先级

```
classify_api_error(exception)
    │
    ├── 1. 状态码匹配（最快、最准确）
    │   ├── 401/403 → auth
    │   ├── 402 → billing
    │   ├── 429 → rate_limit
    │   ├── 503/529 → overloaded
    │   └── 500/502 → server_error
    │
    ├── 2. 消息模式匹配（支持中英文）
    │   ├── "rate limit" / "too many requests" → rate_limit
    │   ├── "context length" / "maximum context" → context_overflow
    │   └── "model not found" → model_not_found
    │
    ├── 3. 异常类型匹配
    │   ├── TimeoutError → timeout
    │   └── ConnectionError → network
    │
    └── 4. 兜底 → unknown
```

#### 在 Agent 中的集成

```python
# langgraph_agent.py — run_analysis_stream 错误处理
except Exception as exc:
    from langgraph_langchain.error_classifier import classify_api_error
    classified = classify_api_error(exc)

    if classified.reason.value in ("rate_limit", "overloaded"):
        # 瞬时错误 — 提供用户友好的重试提示
        yield f"\n\n**API 临时错误** ({classified.reason.value})，请稍后重试。\n"

    elif classified.reason.value in ("auth", "billing"):
        # 认证错误 — 明确告知配置问题
        yield "\n\n**API 认证错误**: 请检查 DEEPSEEK_API_KEY 配置。\n"
```

### 抖动指数退避（P2 新增）

`retry_utils.py` 实现了防止重试惊群的退避算法：

```python
# retry_utils.py
def jittered_backoff(
    attempt: int,           # 当前重试次数（从 1 开始）
    base_delay: float = 5.0,  # 基础延迟（秒）
    max_delay: float = 60.0,  # 最大延迟（秒）
    jitter_ratio: float = 0.5,  # 抖动比例
) -> float:
    """计算退避时间 = min(base * 2^(attempt-1), max_delay) + random jitter"""
```

#### 退避策略示意

```
重试次数    基础延迟    抖动范围          实际延迟
  1         5s       0 ~ 2.5s       5.0 ~ 7.5s
  2        10s       0 ~ 5.0s      10.0 ~ 15.0s
  3        20s       0 ~ 10.0s     20.0 ~ 30.0s
  4        40s       0 ~ 20.0s     40.0 ~ 60.0s
  5        60s       0 ~ 30.0s     60.0 ~ 60.0s (封顶)
```

> **为什么需要抖动？** 如果多个请求同时遇到 429 错误，不加抖动的话它们会在同一时刻重试，再次触发限流。抖动让重试分散到不同时间点。

---

## 10.4 恢复策略矩阵

```python
# api_server_langgraph.py:77-155
_failure_policy = {
    # ━━━ 可自动重试，同范围 ━━━
    "cancelled": {
        "retryable": True,
        "hint": "Restart the analysis when ready.",
        "recovery_action": "retry_same_scope",
    },

    # ━━━ 可自动重试，缩小范围 ━━━
    "python_execution_error": {
        "retryable": True,
        "hint": "Use smaller validated analysis steps before retrying.",
        "recovery_action": "retry_narrower_scope",
    },
    "max_steps_exceeded": {
        "retryable": True,
        "hint": "Narrow the task scope or summarize strongest findings.",
        "recovery_action": "retry_narrower_scope",
    },
    "report_rejected": {
        "retryable": True,
        "hint": "Revise the report to address gate failures.",
        "recovery_action": "retry_narrower_scope",
    },

    # ━━━ 需要用户操作 ━━━
    "missing_data_file": {
        "retryable": True,
        "hint": "Upload a CSV/Excel file to the session.",
        "recovery_action": "user_action_required",
    },
    "session_not_found": {
        "retryable": False,
        "hint": "Create a new session.",
        "recovery_action": "user_action_required",
    },
    "session_expired": {
        "retryable": False,
        "hint": "Create a new session and upload data again.",
        "recovery_action": "user_action_required",
    },
}
```

---

## 10.5 自动重试机制

### API Server 中的自动重试

```python
# api_server_langgraph.py:476-549（简化版）
async def _run_analysis(session_id, session, user_message, file_path, retry_count=0):

    try:
        async for text, artifacts in run_analysis_stream(...):
            parts.append(text)
            ...
    finally:
        _ACTIVE_CANCELS.pop(session_id, None)

    output = "".join(parts)
    failure = _structured_failure_from_output(output)

    if failure:
        # 尝试自动恢复
        recovery = await recovery_executor.attempt_recovery(
            session_id=session_id,
            failure_detail=failure,
            original_instruction=original_instruction,
            retry_count=retry_count,
        )

        if recovery and recovery["strategy"] != "user_action_required":
            # 检查是否还能重试
            if retry_count < max_retries:
                # 自动重试（修改指令后）
                modified_instruction = recovery["modified_instruction"]
                return await _run_analysis(
                    session_id, session, modified_instruction,
                    file_path, retry_count + 1
                )
```

### 重试策略

```
retry_same_scope:
  → 使用原始指令重新运行
  → 适用于: cancelled, session_interrupted

retry_narrower_scope:
  → 修改指令，缩小分析范围
  → 适用于: python_execution_error, max_steps_exceeded
  → 修改示例: "请只分析前 100 行数据" / "只关注 revenue 字段"

user_action_required:
  → 不自动重试，返回错误信息给用户
  → 适用于: missing_data_file, session_expired
```

---

## 10.6 报告验证失败的处理

`finish_report` 的验证是最复杂的。如果验证失败，返回详细的修复指导：

```python
# 报告被拒绝时的返回值
return """REPORT REJECTED. Fix the following before calling finish_report again:
- missing required 'Data Context' section
- key findings lack enough evidence traces
- no metrics were declared using declare_metric
- trend conclusions should mention a time window
- driver claims must include evidence level
"""
```

LLM 看到这个错误后，会：
1. 添加 Data Context 章节
2. 在 Key Findings 中添加具体数字
3. 调用 declare_metric 声明指标
4. 在趋势结论中添加时间窗口
5. 重新调用 finish_report

这就是 ReAct 模式的优势——LLM 可以根据错误信息自我纠正。

---

## 10.7 用户友好的错误消息

```python
# error_messages.py — 将技术错误转为中文友好消息
format_user_friendly_error("python_execution_error", "NameError: xxx")

# 返回:
{
    "title": "代码执行出错",
    "message": "分析过程中代码执行遇到问题，请尝试简化分析步骤后重试。",
    "suggestions": [
        "检查代码是否有拼写错误",
        "将复杂分析拆分为多个小步骤",
        "确认引用的变量已经定义",
    ]
}
```

---

## 10.8 错误处理全景流程

```
Agent 运行
    │
    ├── python_repl 超时 → [ERROR] → LLM 看到错误 → 用更小代码重试
    │
    ├── python_repl 抛异常 → [ERROR] → LLM 看到错误 → 修复代码重试
    │
    ├── 连续 3 次错误 → fail_stage → 自动恢复 → retry_narrower_scope
    │
    ├── 超过 48 步 → fail_stage → 自动恢复 → retry_narrower_scope
    │
    ├── finish_report 被拒 → REPORT REJECTED → LLM 修复报告 → 重新提交
    │
    ├── 用户取消 → fail_stage(cancelled) → 自动恢复 → retry_same_scope
    │
    └── 未知异常 → fail_stage → 自动恢复 → 返回用户友好错误
```

---

> **下一步**：阅读 [11-full-flow.md](11-full-flow.md) 查看一次完整分析的运行全景图。

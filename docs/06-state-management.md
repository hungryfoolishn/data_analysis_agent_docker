# 06 - 状态管理详解

> 本文深入解析 `_Session` 状态类的设计理念、持久化命名空间机制、以及状态如何在工具间流转。

---

## 6.1 为什么需要自定义状态？

### LangGraph 内置状态 vs 业务状态

LangGraph 的 `create_react_agent` 内部使用 `messages` 列表管理对话：

```python
# LangGraph 内置状态（自动管理）
state = {
    "messages": [
        HumanMessage("分析销售数据"),
        AIMessage(tool_calls=[...]),
        ToolMessage("Shape: 200×8"),
        AIMessage(tool_calls=[...]),
        ToolMessage("华东区最高..."),
        ...
    ]
}
```

但数据分析场景需要更多状态：

```
需要的状态                    LangGraph 内置？    解决方案
─────────────────────────    ────────────────    ──────────────
对话历史（messages）           ✅                 自动管理
当前分析阶段                   ❌                 _Session.current_stage
已加载的 DataFrame             ❌                 _Session.ns["df"]
分析发现列表                   ❌                 _Session.findings
工作目录路径                   ❌                 _Session.workspace_dir
连续错误计数                   ❌                 _Session.consecutive_python_errors
生成的图表列表                 ❌                 _Session.new_artifacts
最终报告                       ❌                 _Session.report
```

### 解决方案：闭包 + `_Session`

本项目将业务状态封装在 `_Session` 类中，通过闭包让工具访问：

```python
# 工具通过闭包访问 session
def _make_tools(session: _Session):
    @tool
    def python_repl(code: str):
        output = session.run_code(code)    # 访问 session 的方法
        session.findings.append(...)       # 修改 session 的属性
        return output
```

---

## 6.2 `_Session` 类完整字段解析

```python
# langgraph_agent.py
class _Session:
    def __init__(self, workspace_dir, source_path, session_id, user_question, restore_state=None):
        # ━━━ 核心标识 ━━━
        self.workspace_dir = Path(workspace_dir)  # 会话工作目录
        self.source_path = source_path            # 数据文件路径
        self.session_id = session_id              # 会话唯一 ID
        self.user_question = user_question        # 用户原始问题

        # ━━━ 分析阶段追踪 ━━━
        self.current_stage = "schema_understanding"   # 当前阶段
        self.stage_history: List[StageResult] = []    # 阶段历史
        self.stage_failures: List[FailureInfo] = []   # 失败记录

        # ━━━ 持久化执行命名空间（核心设计）━━━
        self.ns: dict = {
            "WORKSPACE_DIR": workspace_dir,
            "SOURCE_PATH": source_path,
            "Path": Path,
            "save_fig": _save_fig,
            "fix_chinese": _fix_chinese,
            "profile_dimension": _profile_dimension,
            "compare_segments": _compare_segments,
            "time_trend": _time_trend,
            "detect_anomalies": _detect_anomalies,
            "decompose_metric_change": _decompose_metric_change,
            "assess_evidence_level": _assess_evidence_level,
            "rank_driver_candidates": _rank_driver_candidates,
            "check_metric_definition_risk": _check_metric_definition_risk,
            "run_counterfactual_checks": _run_counterfactual_checks,
            "generate_recommendation_candidates": _generate_recommendation_candidates,
            "explanation_bundle": {},  # 解释性结论包
        }

        # ━━━ 分析产出 ━━━
        self.findings: List[Finding] = []         # 分析发现列表
        self.metric_definitions: list = []         # 指标定义列表
        self.assumptions: list = []                # 分析假设列表
        self.report: Optional[str] = None          # 最终报告
        self.pending_report_markdown: Optional[str] = None  # 待审核报告

        # ━━━ 文件追踪 ━━━
        self.new_artifacts: List[Dict] = []        # 新生成的文件
        self.known_image_files: set = set()        # 已知图片文件

        # ━━━ 运行控制 ━━━
        self.total_steps: int = 0                  # 总步数
        self.consecutive_python_errors = 0         # 连续 Python 错误数
        self.cancel_event: asyncio.Event = asyncio.Event()  # 取消事件
        self.last_progress_marker = ""             # 最后进度标记

        # ━━━ 日志 ━━━
        self.process_log: List[str] = []           # 过程日志

        # ━━━ 可观测性 ━━━
        self.logger = _make_session_logger(...)    # 会话日志
        self.structured_logger = StructuredLogger(...)  # 结构化日志
        self.state_machine = AnalysisStateMachine()  # 状态机
```

---

## 6.3 持久化命名空间 — 核心设计

### 问题：变量如何在工具调用间存活？

在 ReAct 循环中，每次 `python_repl` 调用是独立的函数调用。如果只是普通的函数调用，变量不可能存活：

```python
# ❌ 普通函数 — 变量不持久化
def python_repl(code):
    exec(code)  # 执行完变量就没了

python_repl("x = 42")        # x 在 exec 中创建
python_repl("print(x)")      # ❌ NameError: x 未定义
```

### 解决方案：共享命名空间

```python
# ✅ 共享命名空间 — 变量持久化
class _Session:
    def __init__(self):
        self.ns = {}  # 持久命名空间

    def run_code(self, code):
        exec(compile(code, "<agent>", "exec"), self.ns)

session = _Session()
session.run_code("x = 42")   # x 存入 session.ns
session.run_code("print(x)") # ✅ 输出 42，因为 ns 是同一个 dict
```

### Python `exec()` 的 `globals` 参数

```python
# exec() 的第二个参数是全局命名空间
exec("x = 1", my_namespace)
# 等价于：
# my_namespace["x"] = 1

exec("print(x)", my_namespace)
# 在 my_namespace 中查找 x，找到后打印
```

### 实际的命名空间内容

随着分析的进行，`session.ns` 会积累越来越多的变量：

```python
# 初始状态
session.ns = {
    "WORKSPACE_DIR": "/workspace/abc123",
    "SOURCE_PATH": "/workspace/abc123/sales.csv",
    "Path": <class Path>,
    "save_fig": <function>,
    "fix_chinese": <function>,
    "explanation_bundle": {},
}

# load_data 调用后
session.ns["df"] = DataFrame(200行 × 8列)   # ← 新增

# 第 1 次 python_repl 后
session.ns["region_summary"] = DataFrame(...)  # ← 新增
session.ns["fix_chinese_called"] = True         # ← 新增

# 第 2 次 python_repl 后
session.ns["north_df"] = DataFrame(...)         # ← 新增
session.ns["explanation_bundle"]["metric_decomposition"] = {...}  # ← 更新
```

---

## 6.4 状态流转图

### 一个发现（Finding）的诞生过程

```
python_repl 执行分析代码
    │
    ├── 产出统计数据
    │   output = session.run_code("""
    │       result = df.groupby('region')['revenue'].sum()
    │       print(f"华东区: {result['华东']}")
    │   """)
    │
    ▼
LLM 看到输出，决定记录发现
    │
    ├── 调用 record_finding
    │   session.findings.append(Finding(
    │       finding_id="F001",
    │       statement="华东区 revenue 占总量 42%",
    │       evidence=[EvidenceItem(
    │           evidence_text="华东区 total=420K, 占比 42%",
    │           source_fields=["region", "revenue"],
    │           source_artifacts=["revenue_by_region.png"],
    │           stats={"north_revenue": 420000}
    │       )],
    │       evidence_level="A",
    │   ))
    │
    ▼
状态机更新
    ├── session.state_machine.add_condition("findings_recorded")
    └── if len(findings) >= 3:
            session.state_machine.add_condition("min_findings_count")
```

### 一次错误的状态变化

```
python_repl 执行出错
    │
    ├── output = "[ERROR] NameError: name 'xxx' is not defined"
    │
    ▼
更新错误计数
    session.consecutive_python_errors += 1
    # 现在 = 1

    │
    ├── 如果 < 3：LLM 看到错误，尝试修复代码
    │
    └── 如果 >= 3：停止分析
        session.fail_stage("deep_dive", "python_execution_error", ...)
        yield "Analysis stopped: repeated errors without recovery."
        return
```

---

## 6.5 阶段追踪方法

`_Session` 提供了阶段追踪的核心方法：

```python
class _Session:
    def start_stage(self, stage: AnalysisStage):
        """开始一个新阶段"""
        self.current_stage = stage
        self.stage_history.append(
            StageResult(stage=stage, status="started", started_at=datetime.utcnow())
        )
        self.logger.info("stage_started stage=%s", stage)

    def complete_stage(self, stage: AnalysisStage):
        """完成一个阶段"""
        self.stage_history.append(
            StageResult(stage=stage, status="completed", completed_at=datetime.utcnow())
        )
        self.logger.info("stage_completed stage=%s", stage)

    def fail_stage(self, stage, code, message, *, retryable=False, hint=None):
        """标记阶段失败"""
        failure = FailureInfo(code=code, message=message, retryable=retryable)
        self.stage_failures.append(failure)
        self.stage_history.append(
            StageResult(stage=stage, status="failed", failure=failure)
        )
        self.logger.warning("stage_failed stage=%s code=%s", stage, code)

    def try_advance_stage(self):
        """尝试自动推进到下一阶段"""
        next_stage = self.state_machine.get_next_recommended_stage()
        if next_stage:
            can_transition, reason = self.state_machine.can_transition_to(next_stage)
            if can_transition:
                self.state_machine.transition_to(next_stage)
                self.start_stage(next_stage)
```

### 阶段历史示例

```python
session.stage_history = [
    StageResult(stage="schema_understanding", status="started", ...),
    StageResult(stage="schema_understanding", status="completed", ...),
    StageResult(stage="data_quality_check", status="started", ...),
    StageResult(stage="data_quality_check", status="completed", ...),
    StageResult(stage="deep_dive", status="started", ...),
    StageResult(stage="deep_dive", status="completed", ...),
    StageResult(stage="final_report", status="started", ...),
    StageResult(stage="final_report", status="completed", ...),
    StageResult(stage="completed", status="started", ...),
]
```

---

## 6.6 文件追踪机制

`python_repl` 执行代码时可能生成图表文件。`_Session` 通过前后快照对比来检测新文件：

```python
# langgraph_agent.py:1346-1375
@tool
def python_repl(code: str) -> str:
    # 执行前：记录当前所有文件
    files_before = set(session.workspace_dir.rglob("*"))

    # 执行代码
    output = session.run_code(code)

    # 执行后：找出新增的文件
    files_after = set(session.workspace_dir.rglob("*"))
    for f in files_after - files_before:
        if f.is_file():
            session.new_artifacts.append({
                "name": f.name,          # "revenue_by_region.png"
                "path": str(f),          # 完整路径
                "relative_path": str(rel),  # 相对路径
                "url": f"/workspace/files/{rel}",  # 下载 URL
            })

    return output
```

---

## 6.7 会话持久化与断点续传（P3）

### 问题

当后端重启或分析被中断时，所有进度丢失。工作区有图表和发现，但无法恢复。

### 解决方案：SessionPersistence

在每次工具调用后，将 `_Session` 状态序列化到 `workspace/.session_state.json`：

```python
# session_persistence.py
class SessionPersistence:
    def save(self, session) -> None:
        """每次 on_tool_end 后调用，原子写入状态文件"""
        state = {
            "version": 2,
            "session_id": session.session_id,
            "user_question": session.user_question,
            "source_path": session.source_path,
            "current_stage": session.current_stage,
            "total_steps": session.total_steps,
            "findings": [f.model_dump() for f in session.findings],
            "metric_definitions": [...],
            "assumptions": [...],
            "state_machine": {...},      # 完整状态机快照
            "known_image_files": [...],
            "report": session.report,
        }
        # 原子写入：tempfile → os.replace

    def restore(self, workspace_dir) -> Optional[Dict]:
        """加载保存的状态，返回 None 表示无状态或已完成"""

    def can_resume(self, workspace_dir) -> bool:
        """检查是否有可恢复的状态"""
```

### 状态保存触发点

```
run_analysis_stream 事件循环
    │
    ├── on_tool_end(load_data)    → persistence.save(session)
    ├── on_tool_end(eda_profile)  → persistence.save(session)
    ├── on_tool_end(python_repl)  → persistence.save(session)
    ├── on_tool_end(record_finding) → persistence.save(session)
    └── on_tool_end(finish_report) → persistence.save(session)
```

### 断点续传流程

```
POST /sessions/{id}/resume
    │
    ├── 1. 验证会话存在
    ├── 2. persistence.restore(workspace_dir)
    ├── 3. 检查状态有效性（source 文件存在、未完成）
    ├── 4. 创建 _Session(restore_state=saved_state)
    │       ├── 恢复 findings / metrics / assumptions
    │       ├── 恢复 state_machine（阶段、条件、工具历史）
    │       └── 恢复 known_image_files
    ├── 5. 构建 [RESUME] 消息告知 Agent 上下文
    ├── 6. 从保存的 total_steps 继续计数
    └── 7. SSE 流式返回结果
            └── 完成后 persistence.clear(workspace_dir)
```

### 恢复消息格式

```python
# Agent 收到的恢复消息
user_msg = HumanMessage(content=(
    f"[RESUME] This is a resumed analysis session.\n"
    f"Task: {instruction}\n"
    f"Previous stage: deep_dive\n"
    f"Steps completed: 7\n"
    f"Prior findings:\n"
    f"- F001: Sales dropped 15% in Q3\n"
    "Please start by calling load_data to re-load the dataset, "
    "then continue the analysis from where it left off."
))
```

### 相关 API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/sessions/{id}/resume` | POST | 恢复中断的分析（SSE 流式） |
| `/sessions/{id}/resumable` | GET | 检查会话是否可恢复 |

---

> **下一步**：阅读 [07-streaming-sse.md](07-streaming-sse.md) 深入学习流式输出机制。

"""python_repl tool — execute Python code in a persistent namespace."""

from __future__ import annotations

import logging

from langchain_core.tools import tool

from langgraph_langchain.tools.registry import registry
from langgraph_langchain.tools._shared import (
    _validate_python_repl_step,
    _validate_tool_stage_factory,
)
from langgraph_langchain.config import (
    CONVERGENCE_NUDGE_MIN_FINDINGS as _NUDGE_MIN_FINDINGS,
    CONVERGENCE_NUDGE_MIN_STEPS as _NUDGE_MIN_STEPS,
)
from langgraph_langchain.tracing import get_trace_context

logger = logging.getLogger(__name__)


def _build_convergence_nudge(session) -> str:
    """Return a convergence nudge string, or '' if no nudge is warranted.

    The agent tends to diverge (keep opening new python_repl analysis dimensions
    instead of calling finish_report). Injecting a state-based nudge into the
    tool result - earlier and gentler than a hard stop - steers it to converge.

    Two triggers (whichever fires first):
      - enough findings recorded (primary): "time to organize and finish"
      - enough steps taken without enough findings (secondary): "focus and finish"
    """
    findings_count = len(session.findings)
    steps = getattr(session, "total_steps", 0)
    if findings_count >= _NUDGE_MIN_FINDINGS:
        return (
            f"\n[系统提示] 已记录 {findings_count} 个发现,已具备生成最终报告的条件。"
            "建议下一步整理这些发现并调用 finish_report 生成报告,"
            "不要再开启新的分析维度(继续 python_repl 属于无效发散)。"
        )
    if steps >= _NUDGE_MIN_STEPS:
        return (
            f"\n[系统提示] 已执行 {steps} 步。建议聚焦回答用户问题,"
            "尽快 record_finding 记录关键发现并调用 finish_report 收敛,"
            "避免继续开启新的分析维度。"
        )
    return ""


def _factory(session):
    _validate = _validate_tool_stage_factory(session)

    @tool
    def python_repl(code: str) -> str:
        """Execute Python code in a persistent namespace. Variables survive across calls.

        WORKSPACE_DIR (str), SOURCE_PATH (str), and Path are pre-set.
        save_fig(filename) saves and closes the current plt figure to WORKSPACE_DIR.
        fix_chinese() fixes Chinese font rendering - call once before plotting Chinese labels.
        Save files to WORKSPACE_DIR. Returns stdout + stderr.
        If output starts with [ERROR], fix the code and retry - do NOT call finish_report.

        Args:
            code: Python code to execute.
        """
        # Validate stage before execution
        error_msg = _validate("python_repl")
        if error_msg:
            return f"[ERROR] {error_msg}"

        trace_ctx = get_trace_context(session.session_id)
        if trace_ctx:
            trace_ctx.start_span(
                "python_repl",
                attributes={
                    "code_lines": len(code.strip().splitlines()),
                    "tool": "python_repl",
                },
            )

        session.structured_logger.log_tool_call(
            tool_name="python_repl",
            stage=session.state_machine.current_stage,
        )

        session.start_stage("deep_dive")
        validation_error = _validate_python_repl_step(code)
        if validation_error:
            if trace_ctx:
                trace_ctx.end_current_span(status="failed", error_message=validation_error)
            return validation_error
        files_before = set(session.workspace_dir.rglob("*"))
        output = session.run_code(code)
        stripped_output = output.strip()
        has_error = stripped_output.startswith("[ERROR]")

        if has_error:
            session.consecutive_python_errors += 1
            if trace_ctx:
                trace_ctx.current_span.set_attribute("execution_success", False)
                trace_ctx.current_span.set_attribute("output_length", len(output))
                trace_ctx.end_current_span(status="completed")
        else:
            session.consecutive_python_errors = 0
            if stripped_output:
                session.last_progress_marker = stripped_output[:400]
            if trace_ctx:
                trace_ctx.current_span.set_attribute("execution_success", True)
                trace_ctx.current_span.set_attribute("output_length", len(output))
                trace_ctx.end_current_span(status="completed")
        files_after = set(session.workspace_dir.rglob("*"))
        for f in files_after - files_before:
            if f.is_file():
                rel = f.relative_to(session.workspace_dir.parent)
                session.new_artifacts.append({
                    "name": f.name,
                    "path": str(f),
                    "relative_path": str(rel),
                    "url": f"/workspace/files/{rel}",
                })
        result = output if output.strip() else "(no output)"
        # Only nudge toward finish_report on successful steps - on error the
        # priority is fixing the code, not converging.
        if not has_error:
            nudge = _build_convergence_nudge(session)
            if nudge:
                result = result + nudge
        return result

    return python_repl


registry.register(
    name="python_repl",
    toolset="analysis",
    factory=_factory,
    description="Execute Python code in a persistent namespace. Variables survive across calls.",
    emoji="🐍",
)

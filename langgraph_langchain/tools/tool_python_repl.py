"""python_repl tool — execute Python code in a persistent namespace."""

from __future__ import annotations

import logging

from langchain_core.tools import tool

from langgraph_langchain.tools.registry import registry
from langgraph_langchain.tools._shared import (
    _validate_python_repl_step,
    _validate_tool_stage_factory,
)
from langgraph_langchain.tracing import get_trace_context

logger = logging.getLogger(__name__)


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
        return output if output.strip() else "(no output)"

    return python_repl


registry.register(
    name="python_repl",
    toolset="analysis",
    factory=_factory,
    description="Execute Python code in a persistent namespace. Variables survive across calls.",
    emoji="🐍",
)

"""delegate_analysis tool — delegate focused analysis sub-tasks.

Provides a task-decomposition pattern where the main agent can delegate
focused analysis steps (segment, trend, anomaly, comparison) that run in
a fresh Python namespace with a copy of the current DataFrame.

Design constraints:
- No nested delegation (depth limit = 1)
- Sub-tasks share the same workspace for artifacts
- Results must be recorded via ``record_finding`` by the main agent
- Maximum code size and timeout enforced per sub-task
"""

from __future__ import annotations

import json
import logging
import threading
from typing import List, Optional

from langchain_core.tools import tool

from langgraph_langchain.tools.registry import registry, tool_result, tool_error
from langgraph_langchain.tools._shared import (
    _safe_workspace_path,
    _validate_tool_stage_factory,
)
from langgraph_langchain.config import (
    CODE_TIMEOUT as _CODE_TIMEOUT,
    MAX_OUTPUT_LEN as _MAX_OUTPUT_LEN,
)
from langgraph_langchain.runtime import SessionExecutionRecorder

logger = logging.getLogger(__name__)

# Maximum delegation depth — hard block on nested delegation
_MAX_DEPTH = 1


def _factory(session):
    _validate = _validate_tool_stage_factory(session)

    @tool
    def delegate_analysis(
        task_description: str,
        analysis_type: str = "general",
        dimensions: Optional[List[str]] = None,
        code: str = "",
    ) -> str:
        """Delegate a focused analysis sub-task for a specific dimension or segment.

        Use this for parallel-friendly tasks like:
        - "Analyze sales trend for each region separately"
        - "Compare top 5 vs bottom 5 customers on all metrics"
        - "Run anomaly detection on each product category"

        The sub-task runs in a fresh Python namespace with a copy of ``df``.
        Results are returned as structured text for the main agent to record
        via ``record_finding``.

        Args:
            task_description: What to analyze (e.g. "Sales trend for region=East").
            analysis_type: Type hint — one of "segment", "trend", "anomaly",
                           "comparison", or "general".
            dimensions: Column names to focus on (e.g. ["region", "product"]).
            code: Python code to execute for this sub-task. Must print results.
                  Has access to df (copy), WORKSPACE_DIR, save_fig, and all
                  standard helpers. Output is captured and returned.
        """
        # ── Stage validation ───────────────────────────────────────────────
        error_msg = _validate("delegate_analysis")
        if error_msg:
            return tool_error(error_msg)
        recorder = SessionExecutionRecorder(
            session,
            tool_name="delegate_analysis",
            code_or_query=code,
        )

        # ── Depth guard — no nested delegation ─────────────────────────────
        if getattr(session, "_delegation_depth", 0) >= _MAX_DEPTH:
            recorder.fail("Nested delegation not allowed", error_type="DelegationDepthError")
            return tool_error(
                "Nested delegation not allowed. Use python_repl for inner analysis."
            )

        # ── Validate inputs ────────────────────────────────────────────────
        if not task_description.strip():
            recorder.fail("task_description must be non-empty", error_type="ValidationError")
            return tool_error("task_description must be non-empty.")

        valid_types = {"segment", "trend", "anomaly", "comparison", "general"}
        if analysis_type not in valid_types:
            recorder.fail(f"Invalid analysis_type '{analysis_type}'", error_type="ValidationError")
            return tool_error(
                f"Invalid analysis_type '{analysis_type}'. "
                f"Must be one of: {', '.join(sorted(valid_types))}."
            )

        if not code.strip():
            recorder.fail("No code provided", error_type="ValidationError")
            return tool_error(
                "No code provided. Write Python code to perform the sub-task. "
                "The code has access to `df` (a copy of the current DataFrame), "
                "WORKSPACE_DIR, save_fig, and standard analysis helpers."
            )

        # ── Build isolated namespace ────────────────────────────────────────
        df = session.ns.get("df")
        if df is None:
            recorder.fail("DataFrame df not found", error_type="MissingData")
            return tool_error(
                "DataFrame `df` not found in session namespace. "
                "Call load_data first before delegating analysis."
            )

        import pandas as _pd
        try:
            df_copy = df.copy()
        except Exception as exc:
            recorder.fail(str(exc), error_type=type(exc).__name__)
            return tool_error(f"Failed to copy DataFrame: {exc}")

        ws = session.workspace_dir

        # Build namespace with a subset of helpers (safe for sub-tasks)
        sub_ns = {
            "df": df_copy,
            "WORKSPACE_DIR": str(ws),
            "pd": _pd,
            "np": __import__("numpy"),
        }

        # Add helpers from main namespace (they're closures over workspace dir)
        for helper_name in (
            "save_fig", "fix_chinese", "profile_dimension",
            "compare_segments", "time_trend", "detect_anomalies",
            "Path", "safe_workspace_path",
        ):
            if helper_name in session.ns:
                sub_ns[helper_name] = session.ns[helper_name]

        # ── Execute sub-task ────────────────────────────────────────────────
        session._delegation_depth = getattr(session, "_delegation_depth", 0) + 1
        session.structured_logger.log_tool_call(
            tool_name="delegate_analysis",
            stage=session.state_machine.current_stage,
        )
        session.state_machine.record_tool_use("delegate_analysis")

        # ── Execute sub-task through the unified execution boundary (R0a-1A) ──
        # Delegated code is LLM generated (untrusted). It must never run in
        # the API/agent host process. It is dispatched through the configured
        # worker backend and fails closed on policy or backend errors.
        from langgraph_langchain.execution import (
            UNTRUSTED,
            ExecutionPolicyError,
            PythonExecutionRequest,
            WorkerDispatcher,
            build_process_worker_job,
            resolve_execution_backend,
        )

        cancel_event = getattr(session, "cancel_event", None)
        if not isinstance(cancel_event, threading.Event):
            cancel_event = threading.Event()
        source_path = getattr(session, "source_path", "") or ""
        if not isinstance(source_path, str):
            source_path = ""
        session_id = getattr(session, "session_id", None)
        if not isinstance(session_id, str) or not session_id:
            session_id = None

        try:
            resolved = resolve_execution_backend(UNTRUSTED)
        except ExecutionPolicyError as exc:
            session._delegation_depth -= 1
            recorder.fail(str(exc), error_type="ExecutionPolicyDenied")
            return tool_error(f"[SECURITY_DENIED] delegate_analysis refused: {exc}")

        execution_request = PythonExecutionRequest(
            code=code,
            workspace_dir=str(ws),
            source_path=source_path,
            namespace=dict(sub_ns),
            timeout_seconds=_CODE_TIMEOUT,
            max_output_chars=_MAX_OUTPUT_LEN,
            allowed_directories=[str(ws)],
        )
        try:
            job = build_process_worker_job(
                execution_request,
                session_id=session_id,
            )
            result = WorkerDispatcher(
                resolved.backend, backend_name=resolved.backend_name
            ).execute(
                job,
                cancel_event,
            )
        except Exception as exc:
            # Fail closed: dispatch/contract errors never trigger inline
            # execution, and the delegation depth guard stays consistent.
            session._delegation_depth -= 1
            recorder.fail(str(exc), error_type=type(exc).__name__)
            return tool_error(
                f"[SECURITY_DENIED] delegate_analysis dispatch failed: {exc}"
            )

        if result.status.value == "timed_out":
            session._delegation_depth -= 1
            recorder.fail(
                f"Delegated task timed out after {_CODE_TIMEOUT}s",
                error_type="TimeoutError",
            )
            return tool_error(
                f"Delegated task timed out after {_CODE_TIMEOUT}s. "
                "Use smaller, more focused code."
            )
        if result.status.value == "cancelled":
            session._delegation_depth -= 1
            recorder.fail("Delegated task was cancelled", error_type="CancelledError")
            return tool_error("Delegated task was cancelled.")

        output = result.stdout + ("\n" + result.stderr if result.stderr else "")
        had_exception = result.status.value != "succeeded"
        if had_exception and not output.strip() and result.error is not None:
            output = result.error.safe_message
        if had_exception:
            output = "[ERROR]\n" + output

        # Truncate output
        if len(output) > _MAX_OUTPUT_LEN:
            output = output[:_MAX_OUTPUT_LEN] + f"\n...[truncated at {_MAX_OUTPUT_LEN} chars]"

        # ── Track artifacts ─────────────────────────────────────────────────
        image_exts = {".png", ".jpg", ".jpeg", ".svg"}
        for p in sorted(session.workspace_dir.iterdir()):
            if p.suffix.lower() in image_exts and p not in session.known_image_files:
                session.known_image_files.add(p)
                recorder.register_artifact(p)

        session._delegation_depth -= 1

        # ── Return structured result ────────────────────────────────────────
        result = {
            "task": task_description,
            "analysis_type": analysis_type,
            "dimensions": dimensions or [],
            "success": not had_exception,
            "output": output.strip() if output.strip() else "(no output)",
        }
        serialized = json.dumps(result, ensure_ascii=False)
        if had_exception:
            recorder.fail(output, error_type="PythonExecutionError", stdout_preview=serialized)
        else:
            recorder.succeed(serialized)
        return serialized

    return delegate_analysis


registry.register(
    name="delegate_analysis",
    toolset="analysis",
    factory=_factory,
    description=(
        "Delegate a focused analysis sub-task for a specific dimension or segment. "
        "Runs code in a fresh namespace with a copy of df through the isolated "
        "worker execution boundary. "
        "Use for parallel-friendly tasks like segment analysis, trend analysis, "
        "anomaly detection, or group comparison."
    ),
    emoji="🔀",
)

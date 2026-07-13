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

import copy
import io
import json
import logging
import sys
import threading
import traceback
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

        # ── Depth guard — no nested delegation ─────────────────────────────
        if getattr(session, "_delegation_depth", 0) >= _MAX_DEPTH:
            return tool_error(
                "Nested delegation not allowed. Use python_repl for inner analysis."
            )

        # ── Validate inputs ────────────────────────────────────────────────
        if not task_description.strip():
            return tool_error("task_description must be non-empty.")

        valid_types = {"segment", "trend", "anomaly", "comparison", "general"}
        if analysis_type not in valid_types:
            return tool_error(
                f"Invalid analysis_type '{analysis_type}'. "
                f"Must be one of: {', '.join(sorted(valid_types))}."
            )

        if not code.strip():
            return tool_error(
                "No code provided. Write Python code to perform the sub-task. "
                "The code has access to `df` (a copy of the current DataFrame), "
                "WORKSPACE_DIR, save_fig, and standard analysis helpers."
            )

        # ── Build isolated namespace ────────────────────────────────────────
        df = session.ns.get("df")
        if df is None:
            return tool_error(
                "DataFrame `df` not found in session namespace. "
                "Call load_data first before delegating analysis."
            )

        import pandas as _pd
        try:
            df_copy = df.copy()
        except Exception as exc:
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

        buf = io.StringIO()
        had_exception = False

        def _target():
            nonlocal had_exception
            old_out, old_err = sys.stdout, sys.stderr
            sys.stdout = sys.stderr = buf
            try:
                exec(compile(code, "<delegated>", "exec"), sub_ns)  # noqa: S102
            except Exception:
                traceback.print_exc(file=buf)
                had_exception = True
            finally:
                sys.stdout, sys.stderr = old_out, old_err

        t = threading.Thread(target=_target, daemon=True)
        t.start()
        t.join(timeout=_CODE_TIMEOUT)

        if t.is_alive():
            session._delegation_depth -= 1
            return tool_error(
                f"Delegated task timed out after {_CODE_TIMEOUT}s. "
                "Use smaller, more focused code."
            )

        output = buf.getvalue()
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
                rel = p.relative_to(session.workspace_dir.parent)
                session.new_artifacts.append({
                    "name": p.name,
                    "path": str(p),
                    "relative_path": str(rel),
                    "url": f"/workspace/files/{rel}",
                })

        session._delegation_depth -= 1

        # ── Return structured result ────────────────────────────────────────
        result = {
            "task": task_description,
            "analysis_type": analysis_type,
            "dimensions": dimensions or [],
            "success": not had_exception,
            "output": output.strip() if output.strip() else "(no output)",
        }
        return json.dumps(result, ensure_ascii=False)

    return delegate_analysis


registry.register(
    name="delegate_analysis",
    toolset="analysis",
    factory=_factory,
    description=(
        "Delegate a focused analysis sub-task for a specific dimension or segment. "
        "Runs code in a fresh namespace with a copy of df. "
        "Use for parallel-friendly tasks like segment analysis, trend analysis, "
        "anomaly detection, or group comparison."
    ),
    emoji="🔀",
)

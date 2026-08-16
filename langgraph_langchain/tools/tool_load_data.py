"""load_data tool — load CSV/Excel files into the analysis session."""

from __future__ import annotations

import logging
import time

import pandas as pd
from langchain_core.tools import tool

from langgraph_langchain.tools.registry import registry
from langgraph_langchain.tools._shared import (
    _resolve_load_path,
    _validate_tool_stage_factory,
)
from langgraph_langchain.schemas import AnalysisStage
from langgraph_langchain.runtime.context import (
    record_session_execution,
    register_session_dataframe,
)
from langgraph_langchain.tracing import get_trace_context

logger = logging.getLogger(__name__)


def _factory(session):
    _validate = _validate_tool_stage_factory(session)

    @tool
    def load_data(file_path: str, sheet_name: str = "") -> str:
        """Load a CSV or Excel file and return its shape, column types, and a 5-row preview.

        Args:
            file_path: Path to the data file. Accepts an absolute path
                (``/app/workspace/<session>/data.csv``), a CWD-relative path
                (``workspace/<session>/data.csv``), or a workspace-relative path
                (``<session>/data.csv``).
            sheet_name: Sheet name for Excel files (ignored for CSV).
        """
        # Validate stage before execution
        error_msg = _validate("load_data")
        if error_msg:
            return f"[ERROR] {error_msg}"
        started_at = time.monotonic()

        trace_ctx = get_trace_context(session.session_id)
        if trace_ctx:
            trace_ctx.start_span(
                "load_data",
                attributes={
                    "file_path": file_path,
                    "sheet_name": sheet_name,
                    "tool": "load_data",
                },
            )

        try:
            # Track tool usage in state machine
            session.state_machine.record_tool_use("load_data")

            # Transition to schema_understanding stage if not already there
            if session.state_machine.current_stage == AnalysisStage.INIT:
                can_transition, reason = session.state_machine.can_transition_to(
                    AnalysisStage.SCHEMA_UNDERSTANDING
                )
                if can_transition:
                    session.state_machine.transition_to(AnalysisStage.SCHEMA_UNDERSTANDING)
                    session.start_stage("schema_understanding")
                else:
                    return f"[ERROR] Cannot transition to schema_understanding: {reason}"

            # Resolve file_path (absolute / CWD-relative / workspace-relative)
            # and verify it stays within the workspace to prevent path traversal.
            try:
                safe_path = _resolve_load_path(session.workspace_dir, file_path)
            except (ValueError, FileNotFoundError) as e:
                record_session_execution(
                    session,
                    tool_name="load_data",
                    status="failed",
                    code_or_query=file_path,
                    error={"type": type(e).__name__, "message": str(e)},
                    duration_ms=(time.monotonic() - started_at) * 1000,
                )
                if trace_ctx:
                    trace_ctx.end_current_span(status="failed", error_message=str(e))
                return f"[ERROR] {e}"

            path = safe_path
            suffix = path.suffix.lower()
            if suffix == ".csv":
                for enc in ["utf-8", "utf-8-sig", "gbk", "gb2312", "latin-1"]:
                    try:
                        df = pd.read_csv(str(safe_path), encoding=enc)
                        break
                    except UnicodeDecodeError:
                        continue
                else:
                    record_session_execution(
                        session,
                        tool_name="load_data",
                        status="failed",
                        code_or_query=str(safe_path),
                        error={"type": "UnicodeDecodeError", "message": "Cannot decode CSV file"},
                        duration_ms=(time.monotonic() - started_at) * 1000,
                    )
                    if trace_ctx:
                        trace_ctx.end_current_span(status="failed", error_message="Cannot decode CSV file")
                    return "ERROR: Cannot decode CSV file."
            elif suffix in (".xlsx", ".xls"):
                sheets = pd.ExcelFile(str(safe_path)).sheet_names
                actual = sheet_name if sheet_name in sheets else sheets[0]
                df = pd.read_excel(str(safe_path), sheet_name=actual)
            else:
                record_session_execution(
                    session,
                    tool_name="load_data",
                    status="failed",
                    code_or_query=str(safe_path),
                    error={"type": "UnsupportedFileType", "message": f"Unsupported file type '{suffix}'"},
                    duration_ms=(time.monotonic() - started_at) * 1000,
                )
                if trace_ctx:
                    trace_ctx.end_current_span(status="failed", error_message=f"Unsupported file type '{suffix}'")
                return f"ERROR: Unsupported file type '{suffix}'."

            # Inject df into exec namespace so subsequent python_repl calls can use it
            session.ns["df"] = df
            asset = register_session_dataframe(
                session,
                dataframe=df,
                source_path=path,
                source_type=suffix.lstrip("."),
                sheet_name=actual if suffix in (".xlsx", ".xls") else None,
            )
            if asset is not None:
                session.current_asset_id = asset.asset_id

            # Mark schema as documented
            session.state_machine.add_condition("schema_documented")
            session.state_machine.add_condition("fields_understood")

            session.complete_stage("schema_understanding")

            # Try to advance to next stage (DATA_QUALITY_CHECK)
            session.try_advance_stage()

            if trace_ctx:
                trace_ctx.current_span.set_attribute("rows", df.shape[0])
                trace_ctx.current_span.set_attribute("columns", df.shape[1])
                trace_ctx.end_current_span(status="completed")

            lines = [
                f"File: {path.name}",
                f"Shape: {df.shape[0]} rows × {df.shape[1]} columns",
                "\nColumns and dtypes:",
            ]
            for col, dtype in df.dtypes.items():
                null_count = df[col].isna().sum()
                lines.append(f"  {col} ({dtype}) — {null_count} nulls")
            lines.append("\nPreview (first 5 rows):")
            lines.append(df.head(5).to_string(index=False))
            result = "\n".join(lines)
            current_asset_id = getattr(session, "current_asset_id", None)
            record_session_execution(
                session,
                tool_name="load_data",
                status="succeeded",
                code_or_query=str(path),
                input_asset_ids=[current_asset_id] if current_asset_id else [],
                stdout_preview=result,
                duration_ms=(time.monotonic() - started_at) * 1000,
            )
            return result
        except Exception as exc:
            record_session_execution(
                session,
                tool_name="load_data",
                status="failed",
                code_or_query=file_path,
                error={"type": type(exc).__name__, "message": str(exc)},
                duration_ms=(time.monotonic() - started_at) * 1000,
            )
            if trace_ctx:
                trace_ctx.end_current_span(status="failed", error_message=str(exc))
            return f"ERROR: {exc}"

    return load_data


registry.register(
    name="load_data",
    toolset="analysis",
    factory=_factory,
    description="Load a CSV or Excel file and return its shape, column types, and a 5-row preview.",
    emoji="📂",
)

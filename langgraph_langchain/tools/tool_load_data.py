"""load_data tool — load CSV/Excel files into the analysis session."""

from __future__ import annotations

import logging
import time

from langchain_core.tools import tool

from langgraph_langchain.data import DataCache, FileDataSource, SamplingSpec, cache_key
from langgraph_langchain.config import DATA_SAMPLE_ROWS, MAX_IN_MEMORY_ROWS
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
            actual = sheet_name or None
            if suffix in (".xlsx", ".xls"):
                # Preserve historical fallback to the first sheet while routing
                # all physical reads through the DataSource contract.
                import pandas as pd
                sheets = pd.ExcelFile(str(path)).sheet_names
                actual = sheet_name if sheet_name in sheets else sheets[0]
            source = FileDataSource(path, sheet_name=actual)
            scan = source.scan_schema()
            sampling_spec = (
                SamplingSpec(method="random", target_rows=DATA_SAMPLE_ROWS, random_seed=20260817)
                if scan.estimated_rows > MAX_IN_MEMORY_ROWS else None
            )
            data_cache = DataCache(session.workspace_dir / ".data_cache")
            load_key = cache_key(
                source_hash=scan.source_hash or "unknown",
                adapter_version=source.adapter_version,
                parameters={
                    "sheet_name": actual or "",
                    "sampling": sampling_spec.model_dump() if sampling_spec else None,
                },
            )
            df = data_cache.get(load_key)
            cache_hit = df is not None
            sampling_metadata = None
            if df is None:
                if sampling_spec:
                    df, sampling_metadata = source.sample(sampling_spec)
                else:
                    df = source.materialize()
                data_cache.put(load_key, df, metadata={
                    "source_hash": scan.source_hash,
                    "source_type": source.source_type,
                    "sheet_name": actual,
                    "sampling": sampling_metadata.model_dump() if sampling_metadata else None,
                })
            elif sampling_spec:
                sampling_metadata = {
                    "method": sampling_spec.method,
                    "random_seed": sampling_spec.random_seed,
                    "target_rows": sampling_spec.target_rows,
                    "original_row_count": scan.estimated_rows,
                    "sampled_row_count": len(df),
                }

            # Inject df into exec namespace so subsequent python_repl calls can use it
            session.ns["df"] = df
            asset = register_session_dataframe(
                session,
                dataframe=df,
                source_path=path,
                source_type=suffix.lstrip("."),
                sheet_name=actual if suffix in (".xlsx", ".xls") else None,
                source_metadata={
                    **scan.metadata,
                    "source_hash": scan.source_hash,
                    "estimated_rows": scan.estimated_rows,
                    "cache_key": load_key,
                    "cache_hit": cache_hit,
                    "sampling": (
                        sampling_metadata.model_dump()
                        if hasattr(sampling_metadata, "model_dump") else sampling_metadata
                    ),
                },
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
                f"Source scan: estimated {scan.estimated_rows} rows; cache {'hit' if cache_hit else 'miss'}",
                "\nColumns and dtypes:",
            ]
            if sampling_spec:
                lines.insert(3, (
                    f"Sampling: deterministic random, seed={sampling_spec.random_seed}, "
                    f"{len(df)}/{scan.estimated_rows} rows"
                ))
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

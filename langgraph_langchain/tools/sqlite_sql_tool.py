"""Controlled SQLite SQL execution as a registered Runtime asset."""

from __future__ import annotations

from typing import Any, Mapping
from uuid import uuid4

from pydantic import BaseModel, Field

from langgraph_langchain.data.assets import DataAsset
from langgraph_langchain.data.database import (
    BoundedReadResult,
    SQLiteDataSource,
)
from langgraph_langchain.runtime.context import AnalysisRuntime


class SQLiteQueryToolError(RuntimeError):
    """Raised when a controlled SQL tool execution cannot be registered."""


class SQLiteQueryToolResult(BaseModel):
    execution_id: str
    status: str
    output_asset_id: str
    row_count: int
    column_count: int
    truncated: bool
    query_fingerprint: str
    query_type: str
    selected_columns: list[str]
    duration_seconds: float
    requested_row_limit: int


class SQLiteQueryTool:
    """Execute one bounded SQLite query and register its result asset."""

    def __init__(
        self,
        *,
        source: SQLiteDataSource,
        runtime: AnalysisRuntime,
        source_identity: str,
    ) -> None:
        self.source = source
        self.runtime = runtime
        self.source_identity = source_identity

    def execute(
        self,
        *,
        sql: str,
        parameters: tuple[Any, ...] | Mapping[str, Any] | None = None,
        max_rows: int | None = None,
        result_size_limit: int | None = None,
        timeout_seconds: float | None = None,
        execution_id: str | None = None,
    ) -> tuple[SQLiteQueryToolResult, BoundedReadResult, DataAsset]:
        """Execute SQL and register the actual returned rows.

        SQLiteDataSource owns all safety and resource checks.  This method does
        not retry, rewrite SQL, or recover a failed query.
        """
        result = self.source.execute_bounded_query(
            sql,
            parameters=parameters,
            max_rows=max_rows,
            result_size_limit=result_size_limit,
            timeout_seconds=timeout_seconds,
        )
        try:
            asset = self.runtime.register_sqlite_result(
                result=result,
                execution_id=execution_id,
            )
        except Exception as exc:
            raise SQLiteQueryToolError(
                "SQL execution succeeded but result asset registration failed"
            ) from exc
        metadata = result.metadata
        if metadata.source_identity != self.source_identity:
            raise SQLiteQueryToolError(
                "SQLite result source identity does not match tool source"
            )
        tool_result = SQLiteQueryToolResult(
            execution_id=execution_id or f"exec_{uuid4().hex}",
            status="succeeded",
            output_asset_id=asset.asset_id,
            row_count=len(result.dataframe),
            column_count=len(result.dataframe.columns),
            truncated=metadata.truncated,
            query_fingerprint=metadata.query_sha256 or "",
            query_type=metadata.query_type,
            selected_columns=list(metadata.selected_columns),
            duration_seconds=metadata.execution_duration_seconds,
            requested_row_limit=metadata.requested_row_limit,
        )
        return tool_result, result, asset

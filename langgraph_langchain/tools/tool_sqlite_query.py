"""Session-bound read-only SQLite SQL tool."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Mapping

from langchain_core.tools import tool

from langgraph_langchain.data.database import (
    SQLiteConnectionConfig,
    SQLiteDatabase,
    SQLiteDataSource,
)
from langgraph_langchain.evidence import collect_sqlite_query_evidence
from langgraph_langchain.runtime import SessionExecutionRecorder
from langgraph_langchain.tools.sqlite_sql_tool import SQLiteQueryTool
from langgraph_langchain.tools._shared import _validate_tool_stage_factory
from langgraph_langchain.tools.registry import registry, tool_error

logger = logging.getLogger(__name__)


def _parameters(raw: str) -> tuple[Any, ...] | Mapping[str, Any] | None:
    if not raw or raw.strip() in {"", "{}", "[]"}:
        return None
    parsed = json.loads(raw)
    if isinstance(parsed, list):
        return tuple(parsed)
    if isinstance(parsed, dict):
        return parsed
    raise ValueError("parameters_json must be a JSON object or array")


def _factory(session):
    _validate = _validate_tool_stage_factory(session)

    @tool
    def run_sqlite_query(
        asset_id: str,
        sql: str,
        parameters_json: str = "{}",
        max_rows: int = 100000,
        timeout_seconds: float = 30.0,
    ) -> str:
        """Run one read-only SQLite query against a registered database asset.

        Args:
            asset_id: Registered sqlite_database DataAsset ID.
            sql: Single SELECT or CTE query.
            parameters_json: JSON object or array of bound parameters.
            max_rows: Maximum rows returned by the bounded query.
            timeout_seconds: SQLite progress-handler deadline.
        """
        error_msg = _validate("run_sqlite_query")
        recorder = SessionExecutionRecorder(
            session,
            tool_name="run_sqlite_query",
            code_or_query=sql,
        )
        if error_msg:
            recorder.fail(error_msg, error_type="StageValidationError")
            return tool_error(error_msg)

        runtime = getattr(session, "analysis_runtime", None)
        asset = runtime.assets.get(asset_id) if runtime is not None else None
        if asset is None or asset.source_type != "sqlite_database":
            message = f"Database asset is not registered: {asset_id}"
            recorder.fail(message, error_type="AssetNotFoundError")
            return tool_error(message)
        try:
            database_path = Path(asset.location)
            source = SQLiteDataSource(
                SQLiteDatabase(SQLiteConnectionConfig(database_path=database_path))
            )
            query_tool = SQLiteQueryTool(
                source=source,
                runtime=runtime,
                source_identity=source.source_identity,
            )
            parameters = _parameters(parameters_json)
            tool_result, bounded, output_asset = query_tool.execute(
                sql=sql,
                parameters=parameters,
                max_rows=max_rows,
                timeout_seconds=timeout_seconds,
                execution_id=recorder.execution_id,
            )
            evidence = collect_sqlite_query_evidence(
                execution_id=recorder.execution_id,
                result=bounded,
                output_asset=output_asset,
                source_asset_id=asset_id,
            )
        except Exception as exc:
            logger.warning("sqlite_query_failed asset=%s error=%s", asset_id, exc)
            recorder.fail("SQLite query failed", error_type="SQLiteQueryError")
            return tool_error("SQLite query failed")

        evidence_dir = runtime.workspace_dir / ".analysis_evidence"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        evidence_path = evidence_dir / f"{recorder.execution_id}.json"
        evidence_path.write_text(
            evidence.model_dump_json(indent=2), encoding="utf-8"
        )
        evidence_metadata = recorder.register_artifact(evidence_path)
        session.current_asset_id = output_asset.asset_id
        result_payload = {
            "status": "succeeded",
            "output_asset_id": output_asset.asset_id,
            "row_count": tool_result.row_count,
            "column_count": tool_result.column_count,
            "truncated": tool_result.truncated,
            "query_fingerprint": tool_result.query_fingerprint,
            "execution_id": recorder.execution_id,
            "evidence_id": evidence.evidence_id,
            "evidence_artifact_id": evidence_metadata.get("artifact_id"),
            "preview": bounded.dataframe.head(20).to_string(index=False),
        }
        recorder.succeed(json.dumps(result_payload, ensure_ascii=False))
        return json.dumps(result_payload, ensure_ascii=False)

    return run_sqlite_query


registry.register(
    name="run_sqlite_query",
    toolset="analysis",
    factory=_factory,
    description="Execute one controlled read-only SQLite query and register a snapshot-backed result.",
    emoji="🗄️",
)

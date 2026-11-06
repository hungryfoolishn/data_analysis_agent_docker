"""Structured, deterministic dataframe analysis operation tool."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from langchain_core.tools import tool

from langgraph_langchain.evidence import (
    canonical_from_operation_evidence,
    collect_operation_evidence,
)
from langgraph_langchain.runtime import SessionExecutionRecorder
from langgraph_langchain.runtime.analysis_operations import (
    AnalysisOperationError,
    execute_analysis_operation,
)
from langgraph_langchain.tools._shared import _validate_tool_stage_factory
from langgraph_langchain.tools.registry import registry, tool_error

logger = logging.getLogger(__name__)


def _factory(session):
    _validate = _validate_tool_stage_factory(session)

    @tool
    def run_analysis_operation(
        asset_id: str,
        operation: str,
        parameters_json: str = "{}",
    ) -> str:
        """Run one allowlisted dataframe analysis operation and register its output.

        Args:
            asset_id: Registered input DataAsset ID.
            operation: One of the planner allowlist operations.
            parameters_json: JSON object with operation parameters.
        """
        error_msg = _validate("run_analysis_operation")
        recorder = SessionExecutionRecorder(
            session,
            tool_name="run_analysis_operation",
            code_or_query=json.dumps(
                {"asset_id": asset_id, "operation": operation},
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
        if error_msg:
            recorder.fail(error_msg, error_type="StageValidationError")
            return tool_error(error_msg)
        try:
            parameters = json.loads(parameters_json or "{}")
            if not isinstance(parameters, dict):
                raise ValueError("parameters_json must contain a JSON object")
        except Exception as exc:
            message = f"Invalid parameters_json: {exc}"
            recorder.fail(message, error_type="RequestError")
            return tool_error(message)

        runtime = getattr(session, "analysis_runtime", None)
        if runtime is None:
            message = "AnalysisRuntime is unavailable"
            recorder.fail(message, error_type="RuntimeUnavailable")
            return tool_error(message)
        try:
            resolved = runtime.asset_resolver.resolve(asset_id)
            output = execute_analysis_operation(
                resolved.dataframe,
                operation=operation,
                parameters=parameters,
            )
        except AnalysisOperationError as exc:
            message = str(exc)
            recorder.fail(message, error_type="AnalysisOperationError")
            return tool_error(message)
        except Exception as exc:
            message = f"Input asset resolution failed"
            logger.warning("analysis_operation_failed asset=%s error=%s", asset_id, exc)
            recorder.fail(message, error_type="AssetResolutionError")
            return tool_error(message)

        execution_id = recorder.execution_id
        output_dir = runtime.workspace_dir / ".analysis_outputs"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{execution_id}.parquet"
        output.to_parquet(output_path, index=False)
        asset = runtime.register_dataframe(
            dataframe=output,
            source_path=output_path,
            source_type="parquet",
            source_metadata={
                "operation": operation,
                "parameters": parameters,
                "input_asset_ids": [asset_id],
                "execution_id": execution_id,
            },
        )
        metadata = recorder.register_artifact(output_path)
        operation_evidence = collect_operation_evidence(
            execution_id=execution_id,
            operation=operation,
            parameters=parameters,
            input_asset_ids=[asset_id],
            output_asset=asset,
            numeric_values={
                "row_count": float(len(output)),
                "column_count": float(len(output.columns)),
            },
        )
        canonical_evidence = canonical_from_operation_evidence(
            operation_evidence,
            task_id=session.current_runtime_step_id,
            artifact_ids=[metadata.get("artifact_id")],
        )
        runtime.register_canonical_evidence(
            canonical_evidence,
            artifact_ids=[metadata.get("artifact_id")],
        )
        session.current_asset_id = asset.asset_id
        preview = output.head(20).to_string(index=False)
        recorder.succeed(
            json.dumps(
                {
                    "status": "succeeded",
                    "operation": operation,
                    "output_asset_id": asset.asset_id,
                    "row_count": len(output),
                    "column_count": len(output.columns),
                    "artifact": metadata,
                    "preview": preview,
                },
                ensure_ascii=False,
            )
        )
        return json.dumps(
            {
                "status": "succeeded",
                "operation": operation,
                "output_asset_id": asset.asset_id,
                "row_count": len(output),
                "column_count": len(output.columns),
                "artifact_id": metadata.get("artifact_id"),
            },
            ensure_ascii=False,
        )

    return run_analysis_operation


registry.register(
    name="run_analysis_operation",
    toolset="analysis",
    factory=_factory,
    description="Execute one deterministic dataframe analysis operation from a structured plan.",
    emoji="🧮",
)

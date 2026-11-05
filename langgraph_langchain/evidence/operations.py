"""Evidence records for deterministic dataframe operations."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Sequence
from uuid import uuid4

from pydantic import BaseModel, Field

from langgraph_langchain.data.assets import DataAsset


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class OperationEvidence(BaseModel):
    evidence_id: str = Field(default_factory=lambda: f"evid_{uuid4().hex}")
    evidence_type: Literal["analysis_operation"] = "analysis_operation"
    execution_id: str
    operation: str
    input_asset_ids: list[str]
    output_asset_id: str
    parameters: dict[str, Any]
    numeric_values: dict[str, float]
    validation_status: Literal["verified", "unverified"] = "verified"
    truncated: bool = False
    created_at: str = Field(default_factory=_utc_now)


def collect_operation_evidence(
    *,
    execution_id: str,
    operation: str,
    parameters: dict[str, Any],
    input_asset_ids: Sequence[str],
    output_asset: DataAsset,
    numeric_values: dict[str, float],
    truncated: bool = False,
) -> OperationEvidence:
    if not execution_id:
        raise ValueError("execution_id is required")
    if output_asset.content_hash is None and output_asset.location is None:
        raise ValueError("Operation evidence requires a registered output asset")
    return OperationEvidence(
        execution_id=execution_id,
        operation=operation,
        input_asset_ids=list(input_asset_ids),
        output_asset_id=output_asset.asset_id,
        parameters=parameters,
        numeric_values=numeric_values,
        truncated=truncated,
    )

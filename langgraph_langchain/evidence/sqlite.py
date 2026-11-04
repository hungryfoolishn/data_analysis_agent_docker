"""Evidence records for controlled SQLite SQL executions."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Mapping, Sequence
from uuid import uuid4

from pydantic import BaseModel, Field

from langgraph_langchain.data.assets import DataAsset
from langgraph_langchain.data.database import BoundedReadResult


class SqliteEvidenceError(ValueError):
    """Raised when a SQL execution cannot produce trusted evidence."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SqliteQueryEvidence(BaseModel):
    """A traceable record tying SQL execution to a registered result asset."""

    evidence_id: str = Field(default_factory=lambda: f"evid_{uuid4().hex}")
    evidence_type: Literal["sqlite_query"] = "sqlite_query"
    execution_id: str
    source_asset_id: str | None
    input_asset_ids: list[str]
    output_asset_id: str
    operation: str = "sqlite_query"
    query_fingerprint: str
    validation_status: Literal["verified", "unverified"] = "verified"
    truncated: bool
    provenance: dict[str, Any]
    created_at: str = Field(default_factory=_utc_now)


def collect_sqlite_query_evidence(
    *,
    execution_id: str,
    result: BoundedReadResult,
    output_asset: DataAsset,
    source_asset_id: str | None = None,
    input_asset_ids: Sequence[str] = (),
    validation_status: Literal["verified", "unverified"] = "verified",
) -> SqliteQueryEvidence:
    """Build evidence from an actual bounded query and registered asset.

    Raw SQL and raw bound parameters are not stored.  The SQL fingerprint in
    ``BoundedReadResult`` already includes normalized SQL and parameter identity.
    """
    if not execution_id:
        raise SqliteEvidenceError("execution_id is required")
    if result.metadata.source_type != "sqlite":
        raise SqliteEvidenceError("result is not a SQLite bounded read")
    if output_asset.source_type != "sqlite":
        raise SqliteEvidenceError("output asset is not a SQLite asset")
    if validation_status == "verified" and output_asset.content_hash is None:
        raise SqliteEvidenceError("verified evidence requires output content hash")

    metadata = result.metadata
    provenance: dict[str, Any] = {
        "source_type": output_asset.source_type,
        "source_identity": output_asset.source_metadata.get("source_identity"),
        "query_type": metadata.query_type,
        "query_fingerprint": metadata.query_sha256,
        "selected_columns": list(metadata.selected_columns),
        "returned_row_count": metadata.returned_row_count,
        "requested_row_limit": metadata.requested_row_limit,
        "execution_duration_seconds": metadata.execution_duration_seconds,
        "content_hash": output_asset.content_hash,
        "snapshot_reference": output_asset.source_metadata.get(
            "snapshot_reference"
        ),
    }
    return SqliteQueryEvidence(
        execution_id=execution_id,
        source_asset_id=source_asset_id,
        input_asset_ids=list(input_asset_ids),
        output_asset_id=output_asset.asset_id,
        query_fingerprint=metadata.query_sha256 or "",
        validation_status=validation_status,
        truncated=metadata.truncated,
        provenance=provenance,
    )

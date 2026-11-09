"""Canonical evidence contract shared by Runtime tools and reports."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

EvidenceType = Literal["analysis_operation", "sqlite_query", "legacy"]
EvidenceStatus = Literal[
    "unverified", "reference_validated", "verified", "failed"
]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class CanonicalEvidence(BaseModel):
    """Single evidence contract consumed by Runtime report validation."""

    evidence_id: str = Field(default_factory=lambda: f"evid_{uuid4().hex}")
    evidence_type: EvidenceType
    execution_id: str
    task_id: str | None = None
    tool_name: str
    input_asset_ids: list[str] = Field(default_factory=list)
    output_asset_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    evidence_text: str
    numeric_values: dict[str, float] = Field(default_factory=dict)
    validation_status: EvidenceStatus = "unverified"
    source_metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=utc_now_iso)

    def report_record(self) -> dict[str, Any]:
        return {
            "validation_status": self.validation_status,
            "truncated": bool(self.source_metadata.get("truncated", False)),
            "numeric_values": dict(self.numeric_values),
            "evidence_text": self.evidence_text,
            "asset_ids": list(self.output_asset_ids),
            "execution_id": self.execution_id,
        }

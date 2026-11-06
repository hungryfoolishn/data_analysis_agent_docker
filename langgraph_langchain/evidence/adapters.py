"""Adapters from existing evidence models to CanonicalEvidence."""

from __future__ import annotations

from typing import Sequence

from langgraph_langchain.evidence.models import CanonicalEvidence
from langgraph_langchain.evidence.operations import OperationEvidence
from langgraph_langchain.evidence.sqlite import SqliteQueryEvidence
from langgraph_langchain.schemas import EvidenceItem


def canonical_from_operation_evidence(
    evidence: OperationEvidence,
    *,
    task_id: str | None = None,
    tool_name: str = "run_analysis_operation",
    artifact_ids: Sequence[str] = (),
) -> CanonicalEvidence:
    """Adapt a deterministic operation evidence record.

    The returned canonical evidence remains unverified until EvidenceRegistry
    explicitly verifies its references and content.
    """
    return CanonicalEvidence(
        evidence_id=evidence.evidence_id,
        evidence_type="analysis_operation",
        execution_id=evidence.execution_id,
        task_id=task_id,
        tool_name=tool_name,
        input_asset_ids=list(evidence.input_asset_ids),
        output_asset_ids=[evidence.output_asset_id],
        artifact_ids=list(artifact_ids),
        evidence_text=f"Deterministic {evidence.operation} operation completed.",
        numeric_values=dict(evidence.numeric_values),
        validation_status="unverified",
        source_metadata={
            "operation": evidence.operation,
            "parameters": evidence.parameters,
            "truncated": evidence.truncated,
        },
        created_at=evidence.created_at,
    )


def canonical_from_sqlite_query_evidence(
    evidence: SqliteQueryEvidence,
    *,
    task_id: str | None = None,
    tool_name: str = "run_sqlite_query",
    artifact_ids: Sequence[str] = (),
) -> CanonicalEvidence:
    """Adapt SQLite query evidence while preserving SQL provenance."""
    provenance = dict(evidence.provenance)
    return CanonicalEvidence(
        evidence_id=evidence.evidence_id,
        evidence_type="sqlite_query",
        execution_id=evidence.execution_id,
        task_id=task_id,
        tool_name=tool_name,
        input_asset_ids=list(evidence.input_asset_ids)
        + ([evidence.source_asset_id] if evidence.source_asset_id else []),
        output_asset_ids=[evidence.output_asset_id],
        artifact_ids=list(artifact_ids),
        evidence_text="Controlled SQLite query completed.",
        numeric_values={
            "returned_row_count": float(
                provenance.get("returned_row_count", 0)
            )
        },
        validation_status="unverified",
        source_metadata={
            "query_fingerprint": evidence.query_fingerprint,
            "truncated": evidence.truncated,
            "snapshot_reference": provenance.get("snapshot_reference"),
            "content_hash": provenance.get("content_hash"),
            "source_asset_id": evidence.source_asset_id,
        },
        created_at=evidence.created_at,
    )


def canonical_from_legacy_evidence_item(
    evidence: EvidenceItem,
    *,
    task_id: str | None = None,
    tool_name: str = "legacy_tool",
) -> CanonicalEvidence:
    """Adapt legacy EvidenceItem without marking it verified."""
    execution_ids = list(evidence.source_execution_ids)
    numeric_values = {
        str(key): float(value)
        for key, value in (evidence.stats or {}).items()
        if isinstance(value, (int, float))
    }
    return CanonicalEvidence(
        evidence_id=evidence.evidence_id,
        evidence_type="legacy",
        execution_id=execution_ids[0] if execution_ids else "",
        task_id=task_id,
        tool_name=tool_name,
        input_asset_ids=list(evidence.source_asset_ids),
        output_asset_ids=list(evidence.source_artifact_ids),
        artifact_ids=list(evidence.source_artifact_ids),
        evidence_text=evidence.evidence_text,
        numeric_values=numeric_values,
        validation_status=(
            "verified"
            if evidence.verification_status == "verified"
            else "unverified"
        ),
        source_metadata={
            "calculation_method": evidence.calculation_method,
            "verification_result_id": evidence.verification_result_id,
        },
        created_at=evidence.created_at or "",
    )

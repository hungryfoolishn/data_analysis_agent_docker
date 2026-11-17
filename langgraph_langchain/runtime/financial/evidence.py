"""Evidence bridge from canonical Financial results to EvidenceRegistry."""

from __future__ import annotations

from typing import Any, Mapping

from langgraph_langchain.evidence.models import CanonicalEvidence
from langgraph_langchain.evidence.registry import EvidenceRegistry
from langgraph_langchain.runtime.models import ExecutionResult

from .catalog import CALCULATED, FinancialIndicatorResult

__all__ = ["FinancialEvidenceError", "register_financial_evidence"]


class FinancialEvidenceError(ValueError):
    """Raised when canonical Financial evidence cannot be bridged."""


def register_financial_evidence(
    registry: EvidenceRegistry,
    *,
    execution: ExecutionResult,
    indicator: FinancialIndicatorResult,
    evidence_text: str,
    input_asset_ids: tuple[str, ...] = (),
    source_metadata: Mapping[str, Any] | None = None,
) -> CanonicalEvidence:
    """Bridge one successful Financial execution into the existing EvidenceRegistry."""
    if execution.status != "succeeded":
        raise FinancialEvidenceError("only successful executions can create evidence")
    if indicator.status != CALCULATED or indicator.value is None:
        raise FinancialEvidenceError("only calculated indicators can create evidence")
    if not evidence_text.strip():
        raise FinancialEvidenceError("evidence_text is required")
    evidence = CanonicalEvidence(
        evidence_type="analysis_operation",
        execution_id=execution.execution_id,
        task_id=execution.task_id,
        tool_name=execution.tool_name,
        input_asset_ids=list(input_asset_ids),
        output_asset_ids=list(execution.output_artifact_ids),
        evidence_text=evidence_text,
        numeric_values={indicator.metric_id: float(indicator.value)},
        source_metadata={
            **dict(source_metadata or {}),
            "financial_metric": {
                "metric_id": indicator.metric_id,
                "market_scope": indicator.market_scope,
                "statistic_kind": indicator.statistic_kind,
                "unit": indicator.unit,
                "inputs": dict(indicator.inputs),
                "lineage": dict(indicator.lineage),
            },
        },
    )
    return registry.register(evidence)

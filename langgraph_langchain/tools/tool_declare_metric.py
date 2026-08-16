"""declare_metric tool — document metric definitions."""

from __future__ import annotations

import logging

from langchain_core.tools import tool

from langgraph_langchain.tools.registry import registry
from langgraph_langchain.tools._shared import (
    _is_rd_domain_session,
    _validate_tool_stage_factory,
)
from langgraph_langchain.schemas import MetricDefinition
from langgraph_langchain.rd_validators import validate_rd_metric_definition
from langgraph_langchain.rd_efficiency_domain import suggest_related_metrics

logger = logging.getLogger(__name__)


def _factory(session):
    _validate = _validate_tool_stage_factory(session)

    @tool
    def declare_metric(
        metric_name: str,
        definition_text: str,
        time_window: str = None,
        dedup_rule: str = None,
        denominator: str = None,
        semantic_uncertainty: str = None,
    ) -> str:
        """Declare a metric definition to make analysis assumptions explicit.

        Call this tool to document how you're defining and calculating key metrics.
        This prevents misinterpretation and makes your analysis reproducible.

        Args:
            metric_name: Name of the metric (e.g., "revenue", "conversion_rate", "active_users")
            definition_text: Clear definition (e.g., "Sum of order_amount for completed orders")
            time_window: Time scope (e.g., "2025-Q3", "Last 30 days")
            dedup_rule: How duplicates are handled (e.g., "Deduplicated by order_id")
            denominator: For ratios, what's the denominator (e.g., "Total visitors")
            semantic_uncertainty: Any ambiguity in field meaning (e.g., "Assuming 'status=completed' means paid orders")

        Returns:
            Confirmation message
        """
        # Validate stage before execution
        error_msg = _validate("declare_metric")
        if error_msg:
            return f"[ERROR] {error_msg}"

        # Track tool usage in state machine
        session.state_machine.record_tool_use("declare_metric")

        metric_def = MetricDefinition(
            metric_name=metric_name,
            definition_text=definition_text,
            time_window=time_window,
            dedup_rule=dedup_rule,
            denominator=denominator,
            semantic_uncertainty=semantic_uncertainty,
        )

        # Always append the metric definition first
        session.metric_definitions.append(metric_def)
        runtime = getattr(session, "analysis_runtime", None)
        if runtime is not None and hasattr(runtime, "record_metric_definition"):
            runtime.record_metric_definition(metric_def)

        is_valid, rd_errors, related = True, [], []
        if _is_rd_domain_session(session):
            is_valid, rd_errors = validate_rd_metric_definition(metric_def)
            related = suggest_related_metrics(metric_name)

        # Build response message
        if not is_valid:
            msg = (
                f"[WARNING] Metric definition recorded but differs from R&D standards:\n"
                + "\n".join(f"  - {err}" for err in rd_errors)
                + f"\n\nMetric '{metric_name}' recorded anyway. Consider using standard definition."
            )
            return msg

        if related:
            return (
                f"Metric '{metric_name}' definition recorded. "
                f"Related metrics to consider: {', '.join(related[:3])}"
            )

        return f"Metric '{metric_name}' definition recorded."

    return declare_metric


registry.register(
    name="declare_metric",
    toolset="analysis",
    factory=_factory,
    description="Declare a metric definition to make analysis assumptions explicit.",
    emoji="📏",
)

"""record_finding tool — record structured findings with evidence."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import List

from langchain_core.tools import tool

from langgraph_langchain.tools.registry import registry
from langgraph_langchain.tools._shared import _validate_tool_stage_factory
from langgraph_langchain.schemas import EvidenceItem, Finding
from langgraph_langchain.rd_validators import validate_rd_finding
from langgraph_langchain.tracing import get_trace_context

logger = logging.getLogger(__name__)


def _factory(session):
    _validate = _validate_tool_stage_factory(session)

    @tool
    def record_finding(
        statement: str,
        evidence_text: str,
        confidence_level: str = "medium",
        evidence_level: str = "B",
        hypothesis_flag: bool = False,
        category: str = None,
        source_fields: List[str] = None,
        source_artifacts: List[str] = None,
        time_window: str = None,
        group_dimension: str = None,
        filters: List[str] = None,
        stats: dict = None,
        calculation_method: str = None,
    ) -> str:
        """Record a structured finding with evidence during analysis.

        Call this tool whenever you discover an important insight, pattern, or conclusion.
        Each finding must be backed by concrete evidence.

        EVIDENCE LEVEL GUIDELINES:
        - Level A (Facts): Pure factual descriptions without relationship claims
          Example: "North region Q3 revenue is $420K, accounting for 42% of total"
        - Level B (Correlations): Observed patterns, trends, or correlations
          Example: "Revenue decline coincides with order count decrease in the same period"
        - Level C (Causal): Claims about causation (requires strong evidence)
          Example: "Price increase caused 15% drop in conversion rate"
          Requirements: temporal ordering, control groups, mechanism explanation, alternatives ruled out

        Args:
            statement: The core conclusion or insight (e.g., "North region accounts for 42% of Q3 revenue")
            evidence_text: Detailed evidence supporting this finding (numbers, stats, observations)
            confidence_level: Your confidence in this finding - "low", "medium", or "high"
            evidence_level: Evidence quality - "A" (facts), "B" (correlations), "C" (causal claims). Default: "B"
            hypothesis_flag: True if this is a hypothesis requiring validation, False if it is an observation
            category: Finding category (e.g., "trend", "anomaly", "comparison", "attribution")
            source_fields: List of data fields used (e.g., ["region", "revenue", "date"])
            source_artifacts: List of charts or files supporting this (e.g., ["revenue_by_region.png"])
            time_window: Time period for this finding (e.g., "2025-Q3", "2025-07-01 to 2025-09-30")
            group_dimension: Grouping dimension if applicable (e.g., "region", "product_category")
            filters: Any filters applied (e.g., ["revenue > 0", "status = 'completed'"])
            stats: Key statistics as dict (e.g., {"north_revenue": 420000, "total_revenue": 1000000})
            calculation_method: How the finding was calculated (e.g., "SUM(revenue) GROUP BY region")

        Returns:
            Confirmation message with finding ID
        """
        # Validate stage before execution
        error_msg = _validate("record_finding")
        if error_msg:
            return f"[ERROR] {error_msg}"

        # Track tool usage in state machine
        session.state_machine.record_tool_use("record_finding")

        # Log tool call start
        session.structured_logger.log_tool_call(
            tool_name="record_finding",
            stage=session.state_machine.current_stage,
        )

        # Start trace span for this finding
        trace_ctx = get_trace_context(session.session_id)
        span_id = None
        if trace_ctx:
            span = trace_ctx.start_span(
                "record_finding",
                attributes={
                    "statement": statement,
                    "evidence_level": evidence_level,
                    "category": category,
                },
            )
            span_id = span.span_id

        # Pre-validate critical fields before Pydantic to give clearer errors
        if not evidence_text or not evidence_text.strip():
            if trace_ctx:
                trace_ctx.end_current_span(status="failed", error_message="evidence_text is empty")
            return "[ERROR] evidence_text is empty — provide concrete evidence (numbers, stats, observations)."

        try:
            evidence_item = EvidenceItem(
                evidence_text=evidence_text,
                source_fields=source_fields or [],
                source_artifacts=source_artifacts or [],
                time_window=time_window,
                group_dimension=group_dimension,
                filters=filters or [],
                stats=stats,
                calculation_method=calculation_method,
                # Add trace info
                trace_id=trace_ctx.trace_id if trace_ctx else None,
                span_id=span_id,
                tool_name="record_finding",
                timestamp=datetime.now().isoformat() if trace_ctx else None,
            )
        except Exception as exc:
            if trace_ctx:
                trace_ctx.end_current_span(status="failed", error_message=str(exc))
            return f"[ERROR] Invalid evidence: {exc}"

        finding_id = f"F{len(session.findings) + 1:03d}"

        finding = Finding(
            finding_id=finding_id,
            statement=statement,
            evidence=[evidence_item],
            confidence_level=confidence_level,
            evidence_level=evidence_level,
            hypothesis_flag=hypothesis_flag,
            category=category,
            # Add trace info
            trace_id=trace_ctx.trace_id if trace_ctx else None,
        )

        # R&D domain: Validate finding against R&D best practices
        is_valid, rd_errors = validate_rd_finding(finding)
        if not is_valid:
            if trace_ctx and span_id:
                trace_ctx.end_span(span_id, status="warning", validation_errors=rd_errors)
            return (
                f"[WARNING] Finding recorded but has R&D domain issues:\n"
                + "\n".join(f"  - {err}" for err in rd_errors)
                + f"\n\nFinding {finding_id} recorded anyway. Consider revising."
            )

        # P1.2: Validate evidence binding
        from langgraph_langchain.evidence_binding import (
            validate_evidence_binding,
            validate_evidence_completeness,
        )

        # Get available artifacts for validation
        available_artifacts = {
            art.get("artifact_id"): art
            for art in session.new_artifacts
            if art.get("artifact_id")
        }

        # Critical validation: evidence must be properly bound
        is_valid, binding_errors = validate_evidence_binding(finding, available_artifacts)
        if not is_valid:
            error_msg = (
                "[ERROR] Evidence binding validation failed:\n"
                + "\n".join(f"  - {err}" for err in binding_errors)
            )
            if trace_ctx and span_id:
                trace_ctx.end_span(span_id, status="error", validation_errors=binding_errors)
            return error_msg + "\n\nFinding NOT recorded. Please provide proper evidence."

        # Completeness check: warn about missing optional fields
        is_complete, completeness_warnings = validate_evidence_completeness(
            finding, available_artifacts
        )

        session.findings.append(finding)

        # Build response message
        response_msg = f"Finding {finding_id} recorded: {statement[:80]}..."
        if not is_complete:
            response_msg += (
                "\n[INFO] Evidence completeness suggestions:\n"
                + "\n".join(f"  - {warn}" for warn in completeness_warnings)
            )

        # Mark findings as recorded for state machine
        session.state_machine.add_condition("findings_recorded")

        # If we have enough findings, mark for synthesis
        if len(session.findings) >= 3:
            session.state_machine.add_condition("min_findings_count")

        # End trace span
        if trace_ctx and span_id:
            trace_ctx.end_span(span_id, status="completed", finding_id=finding_id)

        return response_msg

    return record_finding


registry.register(
    name="record_finding",
    toolset="analysis",
    factory=_factory,
    description="Record a structured finding with evidence during analysis.",
    emoji="🔍",
)

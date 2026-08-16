"""declare_assumption tool — record analysis assumptions."""

from __future__ import annotations

import logging

from langchain_core.tools import tool

from langgraph_langchain.tools.registry import registry
from langgraph_langchain.tools._shared import _validate_tool_stage_factory
from langgraph_langchain.schemas import AnalysisAssumption

logger = logging.getLogger(__name__)


def _factory(session):
    _validate = _validate_tool_stage_factory(session)

    @tool
    def declare_assumption(assumption_text: str, risk_level: str = "medium") -> str:
        """Declare an analysis assumption to make limitations explicit.

        Call this tool when you make assumptions about data, business logic, or field semantics.

        Args:
            assumption_text: The assumption being made (e.g., "Assuming 'region' field has no missing values")
            risk_level: Risk if assumption is wrong - "low", "medium", or "high"

        Returns:
            Confirmation message
        """
        # Validate stage before execution
        error_msg = _validate("declare_assumption")
        if error_msg:
            return f"[ERROR] {error_msg}"

        # Track tool usage in state machine
        session.state_machine.record_tool_use("declare_assumption")

        assumption = AnalysisAssumption(
            assumption_text=assumption_text,
            risk_level=risk_level,
        )

        session.assumptions.append(assumption)
        runtime = getattr(session, "analysis_runtime", None)
        if runtime is not None and hasattr(runtime, "record_assumption"):
            runtime.record_assumption(assumption)

        return f"Assumption recorded: {assumption_text[:80]}..."

    return declare_assumption


registry.register(
    name="declare_assumption",
    toolset="analysis",
    factory=_factory,
    description="Declare an analysis assumption to make limitations explicit.",
    emoji="📝",
)

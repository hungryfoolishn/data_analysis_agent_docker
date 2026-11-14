"""Planning orchestration for the General Planner (B4.2.1 skeleton).

Implements the frozen ``PlanningOutcome`` state machine (B4.2.0.1 §9):
``planned`` / ``fallback_planned`` / ``needs_clarification`` /
``planning_failed``.

The LLM planner is injected as ``planner_fn`` — B4.2.1 never calls a real
model.  Every produced plan passes ``validate_general_plan``; the deterministic
fallback is re-validated in full.  Fallback never rewrites the user's intent:
an unmatched goal becomes ``needs_clarification``.  Nothing here silently
switches to Legacy (B4.2.0.1 DD-2 rule 9).
"""

from __future__ import annotations

from typing import Callable, Literal, Optional

from pydantic import BaseModel, Field

from langgraph_langchain.runtime.general_planner import (
    DeterministicAnalysisPlanner,
    GeneralAnalysisPlan,
    PlannerClarificationError,
    PlanValidationError,
    validate_general_plan,
)
from langgraph_langchain.runtime.planner_models import PlannerInput

PlanningStatus = Literal[
    "planned",
    "fallback_planned",
    "needs_clarification",
    "planning_failed",
]

MAX_LLM_REPAIR_RETRIES = 2

ReasonCode = Literal[
    "llm_unavailable",
    "llm_invalid_output",
    "llm_plan_invalid",
    "no_matching_operation",
    "clarification_required",
    "planning_exception",
]


class PlanningOutcome(BaseModel):
    status: PlanningStatus
    plan: Optional[GeneralAnalysisPlan] = None
    clarification_questions: list[str] = Field(default_factory=list)
    reason_code: Optional[ReasonCode] = None
    detail: Optional[str] = None


def _planner_asset_columns(planner_input: PlannerInput) -> dict[str, list[str]]:
    return {
        context.asset_id: [
            column.name for column in context.schema_snapshot.columns
        ]
        for context in planner_input.assets
    }


def _validated_outcome(
    plan: GeneralAnalysisPlan,
    planner_input: PlannerInput,
    *,
    status: str,
    reason_code: Optional[str],
) -> PlanningOutcome:
    """Re-validate a produced plan in full before accepting it (§9 rule 3)."""
    errors = validate_general_plan(
        plan,
        asset_columns=_planner_asset_columns(planner_input),
    )
    if errors:
        return PlanningOutcome(
            status="planning_failed",
            reason_code="llm_plan_invalid" if status == "planned" else "planning_exception",
            detail="; ".join(errors),
        )
    return PlanningOutcome(status=status, plan=plan, reason_code=reason_code)


def run_planning(
    planner_input: PlannerInput,
    *,
    deterministic_planner: DeterministicAnalysisPlanner,
    llm_planner_fn: Optional[Callable[..., str]] = None,
    max_repair_retries: int = MAX_LLM_REPAIR_RETRIES,
) -> PlanningOutcome:
    """Run the frozen planner state machine for one analysis request."""
    asset_columns = _planner_asset_columns(planner_input)
    input_asset_ids = [context.asset_id for context in planner_input.assets]

    if llm_planner_fn is not None:
        last_errors: list[str] = []
        detail: Optional[str] = None
        reason_code: Optional[ReasonCode] = None
        for _attempt in range(max_repair_retries + 1):
            try:
                raw_output = llm_planner_fn(planner_input, last_errors)
            except Exception as exc:  # LLM unavailable / transport failure
                reason_code = "llm_unavailable"
                detail = f"{type(exc).__name__}: {exc}"
                break
            try:
                plan = GeneralAnalysisPlan.model_validate_json(raw_output)
            except Exception:
                last_errors = ["output is not a valid GeneralAnalysisPlan JSON"]
                reason_code = "llm_invalid_output"
                continue
            errors = validate_general_plan(plan, asset_columns=asset_columns)
            if not errors:
                return PlanningOutcome(status="planned", plan=plan)
            last_errors = errors
            reason_code = "llm_plan_invalid"
        # LLM produced nothing usable within the retry budget — deterministic
        # fallback, fully re-validated; never a silent Legacy switch.
        fallback = _run_deterministic(
            planner_input, deterministic_planner, asset_columns, input_asset_ids
        )
        if fallback is not None:
            if fallback.status == "planned":
                fallback.status = "fallback_planned"
                fallback.reason_code = reason_code
            return fallback
        return PlanningOutcome(
            status="planning_failed",
            reason_code=reason_code,
            detail=detail,
        )

    return _run_deterministic(
        planner_input, deterministic_planner, asset_columns, input_asset_ids
    )

def _run_deterministic(
    planner_input: PlannerInput,
    deterministic_planner: DeterministicAnalysisPlanner,
    asset_columns: dict[str, list[str]],
    input_asset_ids: list[str],
) -> Optional[PlanningOutcome]:
    """Run the deterministic planner and re-validate its plan in full.

    Returns ``None`` when an unexpected (non-planning) exception escapes, so
    ``run_planning`` can translate it into ``planning_failed``.
    """
    try:
        result = deterministic_planner.plan(
            goal=planner_input.question,
            input_asset_ids=input_asset_ids,
            asset_columns=asset_columns,
        )
    except PlannerClarificationError as exc:
        return PlanningOutcome(
            status="needs_clarification",
            clarification_questions=list(exc.questions),
            reason_code="clarification_required",
        )
    except PlanValidationError as exc:
        return PlanningOutcome(
            status="planning_failed",
            reason_code="no_matching_operation",
            detail=str(exc),
        )
    errors = validate_general_plan(result.plan, asset_columns=asset_columns)
    if errors:
        return PlanningOutcome(
            status="planning_failed",
            reason_code="planning_exception",
            detail="; ".join(errors),
        )
    return PlanningOutcome(
        status="fallback_planned",
        plan=result.plan,
        clarification_questions=list(result.clarification_questions),
        reason_code="llm_unavailable",
    )

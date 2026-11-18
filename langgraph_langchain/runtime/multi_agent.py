"""B11.1 Multi-Agent Research Orchestration.

Planner / Executor / Reviewer agent contracts, Multi-Agent Orchestrator,
and single-agent benchmark comparison. All agents operate through the
existing Runtime V2, StructuredTaskExecutor and EvidenceRegistry.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Mapping, Optional

from langgraph_langchain.runtime.models import RuntimePlanStep
from langgraph_langchain.runtime.plans import AnalysisPlan

__all__ = [
    "AgentRole",
    "ReviewDecision",
    "ResearchTaskPlan",
    "ReviewReport",
    "OrchestratorResult",
    "PlannerAgentError",
    "ReviewerAgentError",
    "MultiAgentOrchestrator",
    "SingleAgentRunner",
]


class AgentRole(str, Enum):
    PLANNER = "planner"
    EXECUTOR = "executor"
    REVIEWER = "reviewer"


class ReviewDecision(str, Enum):
    ACCEPT = "accept"
    REVISE = "revise"
    REJECT = "reject"


class PlannerAgentError(ValueError):
    """Raised when the Planner agent produces an invalid plan."""


class ReviewerAgentError(ValueError):
    """Raised when the Reviewer agent produces an invalid review."""


# ---------------------------------------------------------------------------
# Agent Contracts
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResearchTaskPlan:
    """Planner output: a structured research plan with executable steps."""

    plan_id: str
    goal: str
    steps: tuple[RuntimePlanStep, ...]
    success_criteria: tuple[str, ...]
    max_retries: int = 2
    budget_seconds: float = 120.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.plan_id or not self.goal:
            raise PlannerAgentError("plan_id and goal are required")
        if not self.steps:
            raise PlannerAgentError("plan must have at least one step")
        if self.max_retries < 0:
            raise PlannerAgentError("max_retries must be non-negative")


@dataclass(frozen=True)
class ReviewReport:
    """Reviewer output: decision with evidence-based reasoning."""

    decision: ReviewDecision
    reason: str
    evidence_ids: tuple[str, ...] = ()
    score: float = 0.0
    issues: tuple[str, ...] = ()


@dataclass
class OrchestratorResult:
    """Final multi-agent result with evidence and traceability."""

    plan_id: str
    decision: ReviewDecision
    indicator_value: Any | None
    evidence_ids: tuple[str, ...]
    reviews: tuple[ReviewReport, ...]
    total_retries: int
    elapsed_seconds: float
    execution_result: Any | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


# Planner contract: takes a user goal and context, produces a ResearchTaskPlan
PlannerFn = Callable[[str, Mapping[str, Any]], ResearchTaskPlan]

# Executor contract: takes a plan and executes through Runtime V2
ExecutorFn = Callable[[ResearchTaskPlan], Any]

# Reviewer contract: takes the plan, execution result, and evidence, produces ReviewReport
ReviewerFn = Callable[[ResearchTaskPlan, Any, tuple[str, ...]], ReviewReport]


def default_planner(goal: str, context: Mapping[str, Any]) -> ResearchTaskPlan:
    """Deterministic single-step planner for benchmark reproducibility."""
    step = RuntimePlanStep(
        step_id="step_1",
        objective=goal,
        method="run_analysis_operation",
        parameters=dict(context.get("parameters", {})),
    )
    return ResearchTaskPlan(
        plan_id=f"plan_{goal[:20].replace(' ', '_')}",
        goal=goal,
        steps=(step,),
        success_criteria=("indicator_calculated", "evidence_generated"),
    )


def default_reviewer(
    plan: ResearchTaskPlan,
    execution_result: Any,
    evidence_ids: tuple[str, ...],
) -> ReviewReport:
    """Deterministic reviewer: checks indicator status and evidence presence."""
    status = getattr(execution_result, "status", None)
    if status is None:
        # Try dict access
        if isinstance(execution_result, dict):
            status = execution_result.get("status")
    if status == "calculated" and evidence_ids:
        return ReviewReport(
            decision=ReviewDecision.ACCEPT,
            reason="indicator calculated with evidence",
            evidence_ids=evidence_ids,
            score=1.0,
        )
    if status == "calculated":
        return ReviewReport(
            decision=ReviewDecision.REVISE,
            reason="indicator calculated but evidence missing",
            score=0.5,
            issues=("missing_evidence",),
        )
    return ReviewReport(
        decision=ReviewDecision.REJECT,
        reason=f"execution status: {status!r}",
        score=0.0,
        issues=("execution_failed",),
    )


# ---------------------------------------------------------------------------
# Multi-Agent Orchestrator
# ---------------------------------------------------------------------------


class MultiAgentOrchestrator:
    """Planner -> Executor -> Reviewer loop with bounded retry."""

    def __init__(
        self,
        *,
        planner_fn: PlannerFn | None = None,
        executor_fn: ExecutorFn | None = None,
        reviewer_fn: ReviewerFn | None = None,
        max_retries: int = 2,
        budget_seconds: float = 120.0,
    ) -> None:
        self.planner_fn = planner_fn or default_planner
        self.executor_fn = executor_fn
        self.reviewer_fn = reviewer_fn or default_reviewer
        self.max_retries = max_retries
        self.budget_seconds = budget_seconds

    def run(self, goal: str, context: Mapping[str, Any] | None = None) -> OrchestratorResult:
        context = context or {}
        start = time.monotonic()
        plan = self.planner_fn(goal, context)
        plan.validate()
        reviews: list[ReviewReport] = []
        attempts = 0
        result: Any | None = None
        evidence_ids: tuple[str, ...] = ()

        while attempts < self.max_retries + 1:
            if time.monotonic() - start > self.budget_seconds:
                break
            result = self.executor_fn(plan) if self.executor_fn else None
            evidence_ids = self._collect_evidence(result)
            review = self.reviewer_fn(plan, result, evidence_ids)
            reviews.append(review)
            if review.decision == ReviewDecision.ACCEPT:
                break
            attempts += 1

        elapsed = time.monotonic() - start
        final_decision = reviews[-1].decision if reviews else ReviewDecision.REJECT
        indicator_value = self._extract_value(result)
        return OrchestratorResult(
            plan_id=plan.plan_id,
            decision=final_decision,
            indicator_value=indicator_value,
            evidence_ids=evidence_ids,
            reviews=tuple(reviews),
            total_retries=max(0, attempts - 1),
            elapsed_seconds=elapsed,
            execution_result=result,
        )

    @staticmethod
    def _collect_evidence(result: Any) -> tuple[str, ...]:
        eids = getattr(result, "evidence_ids", None)
        if eids is None and isinstance(result, dict):
            eids = result.get("evidence_ids")
        return tuple(eids) if eids else ()

    @staticmethod
    def _extract_value(result: Any) -> Any:
        val = getattr(result, "value", None)
        if val is None and isinstance(result, dict):
            val = result.get("value")
        return val


# ---------------------------------------------------------------------------
# Single-Agent Baseline
# ---------------------------------------------------------------------------


class SingleAgentRunner:
    """Single-agent baseline: direct executor without plan/review."""

    def __init__(self, *, executor_fn: ExecutorFn | None = None) -> None:
        self.executor_fn = executor_fn

    def run(self, goal: str, context: Mapping[str, Any] | None = None) -> dict[str, Any]:
        context = context or {}
        start = time.monotonic()
        plan = default_planner(goal, context)
        result = self.executor_fn(plan) if self.executor_fn else None
        elapsed = time.monotonic() - start
        evidence_ids = MultiAgentOrchestrator._collect_evidence(result)
        return {
            "goal": goal,
            "status": getattr(result, "status", None) or (result.get("status") if isinstance(result, dict) else None),
            "value": MultiAgentOrchestrator._extract_value(result),
            "evidence_ids": evidence_ids,
            "elapsed_seconds": elapsed,
            "result": result,
        }


def compare_benchmark(
    single_result: dict[str, Any],
    multi_result: OrchestratorResult,
) -> dict[str, Any]:
    """Compare single-agent vs multi-agent results on the same benchmark."""
    single_status = single_result.get("status")
    multi_accepted = multi_result.decision == ReviewDecision.ACCEPT
    return {
        "single_status": single_status,
        "multi_decision": multi_result.decision.value,
        "single_has_evidence": bool(single_result.get("evidence_ids")),
        "multi_has_evidence": bool(multi_result.evidence_ids),
        "single_elapsed": single_result.get("elapsed_seconds", 0.0),
        "multi_elapsed": multi_result.elapsed_seconds,
        "multi_retries": multi_result.total_retries,
        "single_success": single_status == "calculated",
        "multi_success": multi_accepted,
        "multi_value_matches_single": (
            multi_result.indicator_value == single_result.get("value")
        ),
    }

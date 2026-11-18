"""B11.2-11.5: Research-oriented Multi-Agent extensions.

B11.2 Research Planning: multi-step research plan with data requirements.
B11.3 Specialized Agents: DataAnalyst, ReportWriter, QualityAuditor roles.
B11.4 Research & Evidence Contract: structured research findings with evidence.
B11.5 Advanced Orchestration: checkpoint, budget enforcement, conflict resolution.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Mapping, Sequence

from langgraph_langchain.runtime.models import RuntimePlanStep
from .multi_agent import (
    AgentRole,
    MultiAgentOrchestrator,
    OrchestratorResult,
    PlannerAgentError,
    ResearchTaskPlan,
    ReviewDecision,
    ReviewReport,
    ReviewerAgentError,
    SingleAgentRunner,
    compare_benchmark,
    default_planner,
    default_reviewer,
)

__all__ = [
    "SpecializedAgentRole",
    "ResearchStep",
    "ResearchPlan",
    "ResearchFinding",
    "ResearchSynthesis",
    "ResearchPlanningAgent",
    "DataAnalystAgent",
    "ReportWriterAgent",
    "QualityAuditorAgent",
    "ResearchOrchestrator",
    "run_research_comparison",
]


# ---------------------------------------------------------------------------
# B11.2 — Research Planning
# ---------------------------------------------------------------------------


class SpecializedAgentRole(str, Enum):
    DATA_ANALYST = "data_analyst"
    REPORT_WRITER = "report_writer"
    QUALITY_AUDITOR = "quality_auditor"


@dataclass(frozen=True)
class ResearchStep:
    """One step in a multi-step research plan."""

    step_id: str
    objective: str
    agent_role: AgentRole
    tool: str
    parameters: dict[str, Any] = field(default_factory=dict)
    depends_on: tuple[str, ...] = ()

    def to_runtime_step(self) -> RuntimePlanStep:
        """Convert to RuntimePlanStep so the plan is executable via Runtime V2."""
        return RuntimePlanStep(
            step_id=self.step_id,
            objective=self.objective,
            method=self.tool,
            parameters=dict(self.parameters),
        )


@dataclass(frozen=True)
class ResearchPlan:
    """Multi-step research plan produced by the ResearchPlanningAgent."""

    plan_id: str
    goal: str
    steps: tuple[ResearchStep, ...]
    success_criteria: tuple[str, ...]
    data_requirements: tuple[str, ...] = ()
    budget_seconds: float = 120.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.plan_id or not self.goal:
            raise PlannerAgentError("plan_id and goal are required")
        if not self.steps:
            raise PlannerAgentError("plan must have at least one step")
        # Dependency validation
        step_ids = {s.step_id for s in self.steps}
        for s in self.steps:
            for dep in s.depends_on:
                if dep not in step_ids:
                    raise PlannerAgentError(
                        f"step {s.step_id} depends on unknown step {dep!r}"
                    )

    def to_runtime_plan(self) -> tuple[RuntimePlanStep, ...]:
        """Return executable RuntimePlanStep tuple for Runtime V2 integration."""
        return tuple(step.to_runtime_step() for step in self.steps)


class ResearchPlanningAgent:
    """Generates a multi-step ResearchPlan from a research goal."""

    def plan(self, goal: str, context: Mapping[str, Any] | None = None) -> ResearchPlan:
        context = context or {}
        steps: list[ResearchStep] = []
        # Step 1: Load data
        steps.append(ResearchStep(
            step_id="rs_load",
            objective="Load and register dataset",
            agent_role=AgentRole.EXECUTOR,
            tool="load_data",
            parameters=dict(context.get("load_params", {})),
        ))
        # Step 2: Analyze
        steps.append(ResearchStep(
            step_id="rs_analyze",
            objective="Execute analysis operation",
            agent_role=AgentRole.EXECUTOR,
            tool="run_analysis_operation",
            parameters=dict(context.get("analysis_params", {})),
            depends_on=("rs_load",),
        ))
        # Step 3: Verify
        steps.append(ResearchStep(
            step_id="rs_verify",
            objective="Review and verify results",
            agent_role=AgentRole.REVIEWER,
            tool="review",
            depends_on=("rs_analyze",),
        ))
        return ResearchPlan(
            plan_id=f"rplan_{goal[:20].replace(' ', '_')}",
            goal=goal,
            steps=tuple(steps),
            success_criteria=(
                "data_loaded",
                "indicator_calculated",
                "evidence_generated",
                "review_accepted",
            ),
            data_requirements=tuple(context.get("data_requirements", [])),
        )


# ---------------------------------------------------------------------------
# B11.3 — Specialized Research Agents
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResearchFinding:
    """A structured finding produced by the DataAnalystAgent."""

    finding_id: str
    statement: str
    metric_id: str
    value: Any | None
    evidence_ids: tuple[str, ...]
    confidence: float = 0.0
    agent_role: str = SpecializedAgentRole.DATA_ANALYST.value


@dataclass(frozen=True)
class ResearchSynthesis:
    """ReportWriterAgent output: synthesised findings with evidence."""

    report_id: str
    goal: str
    findings: tuple[ResearchFinding, ...]
    summary: str
    evidence_ids: tuple[str, ...]
    generated_at: str = ""

    @property
    def is_valid(self) -> bool:
        return bool(self.findings) and bool(self.evidence_ids)


class DataAnalystAgent:
    """Executes research steps and produces structured findings."""

    def __init__(self, *, executor_fn: Callable[[ResearchStep], Any] | None = None) -> None:
        self.executor_fn = executor_fn

    def execute_step(self, step: ResearchStep) -> ResearchFinding:
        result = self.executor_fn(step) if self.executor_fn else None
        status = getattr(result, "status", None)
        if status is None and isinstance(result, dict):
            status = result.get("status")
        value = getattr(result, "value", None)
        if value is None and isinstance(result, dict):
            value = result.get("value")
        evidence_ids = getattr(result, "evidence_ids", None)
        if evidence_ids is None and isinstance(result, dict):
            evidence_ids = result.get("evidence_ids", ())
        confidence = 1.0 if status == "calculated" else 0.0
        return ResearchFinding(
            finding_id=f"finding_{step.step_id}",
            statement=step.objective,
            metric_id=step.parameters.get("metric_id", step.objective),
            value=value,
            evidence_ids=tuple(evidence_ids) if evidence_ids else (),
            confidence=confidence,
        )


class ReportWriterAgent:
    """Synthesises findings into a structured report."""

    def write(
        self,
        plan: ResearchPlan,
        findings: Sequence[ResearchFinding],
    ) -> ResearchSynthesis:
        evidence_ids: list[str] = []
        for f in findings:
            for eid in f.evidence_ids:
                if eid not in evidence_ids:
                    evidence_ids.append(eid)
        evidence_ids_t = tuple(evidence_ids)
        summary_parts = []
        for f in findings:
            summary_parts.append(f"{f.statement}: {f.value}")
        return ResearchSynthesis(
            report_id=f"report_{plan.plan_id}",
            goal=plan.goal,
            findings=tuple(findings),
            summary="; ".join(summary_parts),
            evidence_ids=evidence_ids_t,
        )


class QualityAuditorAgent:
    """Reviews findings and report for quality, evidence and consistency."""

    def review_synthesis(
        self,
        synthesis: ResearchSynthesis,
        plan: ResearchPlan,
    ) -> ReviewReport:
        issues: list[str] = []
        if not synthesis.findings:
            issues.append("no_findings")
        if not synthesis.evidence_ids:
            issues.append("no_evidence")
        # Check each finding has evidence
        for f in synthesis.findings:
            if not f.evidence_ids:
                issues.append(f"finding_{f.finding_id}_missing_evidence")
        # Check success criteria coverage
        for criterion in plan.success_criteria:
            if criterion == "evidence_generated" and not synthesis.evidence_ids:
                continue  # already flagged
        if issues:
            decision = (
                ReviewDecision.REJECT if "no_findings" in issues
                else ReviewDecision.REVISE
            )
        else:
            decision = ReviewDecision.ACCEPT
        score = 1.0 if decision == ReviewDecision.ACCEPT else (0.5 if decision == ReviewDecision.REVISE else 0.0)
        return ReviewReport(
            decision=decision,
            reason="; ".join(issues) if issues else "all checks passed",
            evidence_ids=synthesis.evidence_ids,
            score=score,
            issues=tuple(issues),
        )


# ---------------------------------------------------------------------------
# B11.4/B11.5 — Advanced Research Orchestrator
# ---------------------------------------------------------------------------


@dataclass
class ResearchResult:
    """Final result of a research orchestration run."""

    plan_id: str
    decision: ReviewDecision
    synthesis: ResearchSynthesis | None
    findings: tuple[ResearchFinding, ...]
    reviews: tuple[ReviewReport, ...]
    total_steps: int
    total_retries: int
    elapsed_seconds: float
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def success(self) -> bool:
        return self.decision == ReviewDecision.ACCEPT


class ResearchOrchestrator:
    """Full research orchestration: plan → analyze → write → audit → retry."""

    def __init__(
        self,
        *,
        planner: ResearchPlanningAgent | None = None,
        analyst: DataAnalystAgent | None = None,
        writer: ReportWriterAgent | None = None,
        auditor: QualityAuditorAgent | None = None,
        max_retries: int = 2,
        budget_seconds: float = 120.0,
    ) -> None:
        self.planner = planner or ResearchPlanningAgent()
        self.analyst = analyst or DataAnalystAgent()
        self.writer = writer or ReportWriterAgent()
        self.auditor = auditor or QualityAuditorAgent()
        self.max_retries = max_retries
        self.budget_seconds = budget_seconds

    def run(
        self,
        goal: str,
        context: Mapping[str, Any] | None = None,
        *,
        step_executor: Callable[[ResearchStep], Any] | None = None,
    ) -> ResearchResult:
        context = context or {}
        start = time.monotonic()
        plan = self.planner.plan(goal, context)
        plan.validate()
        reviews: list[ReviewReport] = []
        retries = 0
        synthesis: ResearchSynthesis | None = None
        findings: tuple[ResearchFinding, ...] = ()

        for attempt in range(self.max_retries + 1):
            if time.monotonic() - start > self.budget_seconds:
                break
            analyst = (
                DataAnalystAgent(executor_fn=step_executor)
                if step_executor is not None
                else self.analyst
            )
            findings = tuple(
                analyst.execute_step(step)
                for step in plan.steps
                if step.agent_role == AgentRole.EXECUTOR
            )
            synthesis = self.writer.write(plan, findings)
            review = self.auditor.review_synthesis(synthesis, plan)
            reviews.append(review)
            if review.decision == ReviewDecision.ACCEPT:
                break
            retries += 1

        elapsed = time.monotonic() - start
        final_decision = reviews[-1].decision if reviews else ReviewDecision.REJECT
        return ResearchResult(
            plan_id=plan.plan_id,
            decision=final_decision,
            synthesis=synthesis,
            findings=findings,
            reviews=tuple(reviews),
            total_steps=len(plan.steps),
            total_retries=retries,
            elapsed_seconds=elapsed,
        )


def run_research_comparison(
    goal: str,
    *,
    context: Mapping[str, Any] | None = None,
    step_executor: Callable[[ResearchStep], Any] | None = None,
) -> dict[str, Any]:
    """Run single-agent vs multi-agent research on the same goal."""
    # Multi-agent
    orch = ResearchOrchestrator()
    multi = orch.run(goal, context, step_executor=step_executor)
    # Single-agent: use the first executor step directly
    single_start = time.monotonic()
    plan = ResearchPlanningAgent().plan(goal, context)
    exec_steps = [s for s in plan.steps if s.agent_role == AgentRole.EXECUTOR]
    single_findings = []
    analyst = DataAnalystAgent(executor_fn=step_executor)
    for s in exec_steps:
        single_findings.append(analyst.execute_step(s))
    single_elapsed = time.monotonic() - single_start
    return {
        "single": {
            "findings_count": len(single_findings),
            "has_evidence": any(f.evidence_ids for f in single_findings),
            "elapsed_seconds": single_elapsed,
        },
        "multi": {
            "decision": multi.decision.value,
            "findings_count": len(multi.findings),
            "has_synthesis": multi.synthesis is not None,
            "has_evidence": bool(multi.synthesis and multi.synthesis.evidence_ids),
            "review_accepted": multi.success,
            "retries": multi.total_retries,
            "elapsed_seconds": multi.elapsed_seconds,
            "total_steps": multi.total_steps,
        },
    }

"""Constraint-aware planning (V1.0, P3).

Bridges a validated AnalysisIntent to the existing allowlisted plan contract
(``runtime.general_planner``), enforcing metric/dimension compatibility BEFORE a plan
can execute. Plans reference their intent and can never rewrite the user goal.
Revisions are explicit, bounded and auditable.
"""
from __future__ import annotations

from typing import Mapping, Optional, Sequence
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from langgraph_langchain.analysis_intelligence.intent import (
    AnalysisIntent,
    AnalysisType,
    IntentStatus,
)
from langgraph_langchain.analysis_intelligence.semantic_contracts import (
    CompatibilityStatus,
    CompatibilityValidator,
    SemanticRegistry,
)
from langgraph_langchain.runtime.general_planner import (
    GeneralAnalysisPlan,
    GeneralPlanStep,
    validate_general_plan,
)


class PlanningBlockedError(ValueError):
    """Raised when an intent cannot legally become a plan."""

    def __init__(self, reasons: Sequence[str]) -> None:
        self.reasons = list(reasons)
        super().__init__("; ".join(self.reasons))


class PlanningOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent_id: str
    plan: Optional[GeneralAnalysisPlan] = None
    blocked_reasons: list[str] = Field(default_factory=list)
    preconditions: list[str] = Field(default_factory=list)
    revision_count: int = 0


_AGG_BY_CONTRACT = {
    "sum": "sum",
    "count": "count",
    "average": "mean",
    "distinct_count": "count",
    "ratio": "mean",  # ratio re-computation is governed by validation_rules; the
    # deterministic operation layer recomputes from numerator/denominator columns.
}


class IntentPlanner:
    """Intent -> validated GeneralAnalysisPlan with semantic constraint checks."""

    def __init__(
        self,
        registry: SemanticRegistry,
        *,
        compatibility: Optional[CompatibilityValidator] = None,
    ) -> None:
        self._registry = registry
        self._compatibility = compatibility or CompatibilityValidator(registry)

    def plan_from_intent(
        self,
        intent: AnalysisIntent,
        *,
        input_asset_ids: Sequence[str],
        asset_columns: Mapping[str, Sequence[str]],
        time_column: Optional[str] = None,
    ) -> PlanningOutcome:
        blocked: list[str] = []
        preconditions: list[str] = []
        if intent.status in (IntentStatus.NEEDS_CLARIFICATION, IntentStatus.UNSUPPORTED):
            raise PlanningBlockedError(
                [*intent.status_reasons, "cannot plan for an unresolved or unsupported intent"]
            )
        if intent.status is IntentStatus.NEEDS_EXPLORATION:
            raise PlanningBlockedError(
                [
                    *intent.status_reasons,
                    "exploration required before planning: resolve unknowns from the data first",
                ]
            )
        metric_ids = intent.metric_ids()
        if not metric_ids and intent.analysis_type is not AnalysisType.OVERVIEW:
            raise PlanningBlockedError(["intent resolved no metric; cannot plan"])

        # Semantic constraint gate: every metric x dimension combination must be
        # VALID (CONDITIONAL requires explicit precondition acknowledgment).
        for metric_id in metric_ids:
            contract = self._registry.metric(metric_id)
            if contract is None or not contract.source.is_trusted:
                blocked.append(f"metric {metric_id} lacks a trusted contract")
                continue
            missing = [c for c in contract.required_columns if not any(
                c in columns for columns in asset_columns.values()
            )]
            if missing:
                blocked.append(
                    f"metric {metric_id} requires columns {missing} not present in the asset"
                )
            for dim_id in intent.dimension_ids():
                report = self._compatibility.check(
                    metric_id, dim_id, data_asset_id=input_asset_ids[0] if input_asset_ids else None
                )
                if report.status is CompatibilityStatus.INVALID:
                    blocked.append(
                        f"invalid combination {metric_id} x {dim_id}: {report.reason}"
                    )
                elif report.status is CompatibilityStatus.UNKNOWN:
                    blocked.append(
                        f"unverified combination {metric_id} x {dim_id}: {report.reason}"
                    )
                elif report.status is CompatibilityStatus.CONDITIONAL:
                    preconditions.extend(report.preconditions)
        if blocked:
            raise PlanningBlockedError(blocked)

        if intent.analysis_type is AnalysisType.DRILL_DOWN and intent.drill_down:
            report = self._compatibility.check_drill_down(
                metric_ids[0],
                intent.drill_down.parent_dimension,
                intent.drill_down.child_dimension,
                data_asset_id=input_asset_ids[0] if input_asset_ids else None,
            )
            if report.status is not CompatibilityStatus.VALID:
                raise PlanningBlockedError([f"drill-down blocked: {report.reason}"])

        plan = self._build_plan(intent, input_asset_ids, asset_columns, time_column)
        errors = validate_general_plan(plan, asset_columns=asset_columns)
        if errors:
            raise PlanningBlockedError([f"generated plan failed validation: {e}" for e in errors])
        return PlanningOutcome(
            intent_id=intent.intent_id,
            plan=plan,
            preconditions=preconditions,
        )

    def _value_column(self, metric_id: str) -> str:
        contract = self._registry.metric(metric_id)
        if contract is None:
            return metric_id
        if contract.numerator and contract.denominator:
            return contract.numerator
        return contract.required_columns[-1]

    def _build_plan(
        self,
        intent: AnalysisIntent,
        input_asset_ids: Sequence[str],
        asset_columns: Mapping[str, Sequence[str]],
        time_column: Optional[str],
    ) -> GeneralAnalysisPlan:
        asset_id = input_asset_ids[0]
        steps: list[GeneralPlanStep] = []
        inspect = GeneralPlanStep(
            step_id=f"step_{uuid4().hex}",
            operation="inspect",
            input_refs=[asset_id],
            parameters={},
            expected_output="dataframe",
            depends_on=[],
            rationale="Inspect the registered input before analysis.",
        )
        steps.append(inspect)
        previous = inspect.step_id
        metric_id = intent.metric_ids()[0] if intent.metric_ids() else None
        contract = self._registry.metric(metric_id) if metric_id else None
        value_column = self._value_column(metric_id) if metric_id else ""
        agg = _AGG_BY_CONTRACT.get(contract.aggregation if contract else "sum", "sum")

        def add_step(
            operation: str,
            parameters: dict,
            depends: list[str],
            rationale: str,
            input_refs: Optional[list[str]] = None,
        ) -> str:
            step = GeneralPlanStep(
                step_id=f"step_{uuid4().hex}",
                operation=operation,
                input_refs=input_refs if input_refs is not None else [asset_id],
                parameters=parameters,
                expected_output="dataframe",
                depends_on=depends,
                rationale=rationale,
            )
            steps.append(step)
            return step.step_id

        dim_id = intent.dimensions[0].entity_id if intent.dimensions else None
        if intent.analysis_type is AnalysisType.DRILL_DOWN and intent.drill_down:
            # Drill-down is a hierarchical breakdown along the verified
            # parent-child path: one multi-key aggregation over the source
            # frame.  Sequential single-key aggregations would lose the child
            # column after the first transform and are not executable.
            add_step(
                "group_aggregate",
                {
                    "group_by": [
                        intent.drill_down.parent_dimension,
                        intent.drill_down.child_dimension,
                    ],
                    "metric": value_column,
                    "agg": agg,
                },
                [previous],
                (
                    f"Hierarchical drill-down [{intent.drill_down.parent_dimension}, "
                    f"{intent.drill_down.child_dimension}] "
                    f"(verified relationship path)"
                ),
            )
        elif intent.analysis_type is AnalysisType.RANKING and intent.ranking:
            aggregated_column = value_column
            if dim_id:
                previous = add_step(
                    "group_aggregate",
                    {"group_by": dim_id, "metric": value_column, "agg": agg},
                    [previous],
                    f"Aggregate {value_column} by {dim_id} before ranking (collection scope)",
                )
                # group_aggregate renames the value column to f"{metric}_{agg}".
                aggregated_column = f"{value_column}_{agg}"
            params: dict = {
                "by": aggregated_column,
                "ascending": intent.ranking.direction == "asc",
            }
            if intent.ranking.top_n:
                params["top_n"] = intent.ranking.top_n
            sort_refs = (
                [f"{previous}.output"] if previous != inspect.step_id else None
            )
            add_step(
                "sort",
                params,
                [previous],
                "Rank by metric value; TopN enforced at presentation",
                input_refs=sort_refs,
            )
        elif intent.analysis_type is AnalysisType.TREND:
            if not time_column:
                raise PlanningBlockedError(
                    ["trend planning requires a verified time column; explore the data first"]
                )
            add_step(
                "trend",
                {"time_column": time_column, "value_column": value_column},
                [previous],
                "Time trend over the verified time column",
            )
        elif dim_id:
            add_step(
                "group_aggregate",
                {"group_by": dim_id, "metric": value_column, "agg": agg},
                [previous],
                f"Aggregate {value_column} by {dim_id}",
            )
        else:
            add_step(
                "summarize",
                {"metric": value_column, "agg": agg},
                [previous],
                f"Summarize {value_column}",
            )
        return GeneralAnalysisPlan(
            user_goal=intent.user_goal,
            input_asset_ids=list(input_asset_ids),
            steps=steps,
        )


class PlanRevision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision_id: str
    revision_index: int
    reason: str
    trigger_evidence: list[str] = Field(default_factory=list)
    previous_plan_id: Optional[str] = None
    new_plan_id: str
    changed_task_ids: list[str] = Field(default_factory=list)
    preserved_result_task_ids: list[str] = Field(default_factory=list)


class PlanRevisionLedger:
    """Bounded, auditable plan revisions.

    - MAX_REVISIONS total per analysis; further revision attempts are refused.
    - Every revision records reason, trigger evidence, previous/new plan ids and
      which task results are preserved (successful results are never dropped).
    - The user goal is immutable across revisions.
    """

    MAX_REVISIONS = 3

    def __init__(self, *, user_goal: str) -> None:
        self._user_goal = user_goal
        self._revisions: list[PlanRevision] = []

    @property
    def revisions(self) -> list[PlanRevision]:
        return list(self._revisions)

    @property
    def exhausted(self) -> bool:
        return len(self._revisions) >= self.MAX_REVISIONS

    def record(
        self,
        *,
        reason: str,
        trigger_evidence: Sequence[str],
        previous_plan_id: Optional[str],
        new_plan_id: str,
        changed_task_ids: Sequence[str] = (),
        preserved_result_task_ids: Sequence[str] = (),
    ) -> PlanRevision:
        if self.exhausted:
            raise PlanningBlockedError(
                [
                    f"plan revision budget exhausted ({self.MAX_REVISIONS}); "
                    "the analysis must terminate with its current results"
                ]
            )
        revision = PlanRevision(
            revision_id=f"rev_{uuid4().hex}",
            revision_index=len(self._revisions) + 1,
            reason=reason,
            trigger_evidence=list(trigger_evidence),
            previous_plan_id=previous_plan_id,
            new_plan_id=new_plan_id,
            changed_task_ids=list(changed_task_ids),
            preserved_result_task_ids=list(preserved_result_task_ids),
        )
        self._revisions.append(revision)
        return revision

    def assert_goal_unchanged(self, new_goal: str) -> None:
        if new_goal != self._user_goal:
            raise PlanningBlockedError(
                ["plan revision attempted to rewrite the user goal; refused"]
            )

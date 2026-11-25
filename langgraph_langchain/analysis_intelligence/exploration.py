"""Dynamic data exploration & feedback (V1.0, P4).

Question-driven exploration over bounded schema profiles. The engine never reads data
itself: callers supply a profile already obtained through the governed data layer
(``scan_schema`` / bounded reads). Decisions are deterministic and diverge with the
exploration result — this is not a fixed pipeline.
"""
from __future__ import annotations

import hashlib
from enum import Enum
from typing import Mapping, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field

from langgraph_langchain.analysis_intelligence.constraints import PlanningBlockedError
from langgraph_langchain.analysis_intelligence.intent import (
    AnalysisIntent,
    AnalysisType,
    IntentStatus,
)
from langgraph_langchain.analysis_intelligence.semantic_contracts import (
    DimensionContract,
    SemanticRegistry,
    derive_dimension_candidates,
)


class ExplorationDecision(str, Enum):
    BUILD_PLAN = "build_plan"
    EXPLORE_MORE = "explore_more"
    VALIDATE_CANDIDATE = "validate_candidate"
    ASK_USER = "ask_user"
    STOP = "stop"


class ExplorationQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str
    question: str
    # What unknown this question resolves: dimension_candidates | time_column | grain
    target: str
    data_asset_id: Optional[str] = None


class SchemaProfile(BaseModel):
    """Bounded schema snapshot supplied by the caller (never raw data)."""

    model_config = ConfigDict(extra="forbid")

    data_asset_id: str
    columns: dict[str, str]
    cardinalities: dict[str, int] = Field(default_factory=dict)
    null_ratios: dict[str, float] = Field(default_factory=dict)
    row_estimate: Optional[int] = None


class ExplorationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exploration_id: str
    question_id: str
    operation: str
    findings: dict[str, object] = Field(default_factory=dict)
    dimension_candidates: list[DimensionContract] = Field(default_factory=list)
    time_column: Optional[str] = None
    evidence_refs: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    limitations: list[str] = Field(default_factory=list)
    decision: ExplorationDecision
    next_question_id: Optional[str] = None


class SchemaExplorationEngine:
    """Deterministic answers to concrete exploration questions."""

    def explore(
        self,
        question: ExplorationQuestion,
        profile: SchemaProfile,
        registry: SemanticRegistry,
    ) -> ExplorationResult:
        base = dict(
            exploration_id=f"expl_{hashlib.sha1(question.question_id.encode()).hexdigest()[:12]}",
            question_id=question.question_id,
            operation=f"profile:{question.target}",
        )
        if question.target == "dimension_candidates":
            candidates = [
                c
                for c in derive_dimension_candidates(
                    profile.columns,
                    cardinalities=profile.cardinalities,
                    data_asset_id=profile.data_asset_id,
                )
                if c.semantic_type == "categorical" and c.confidence >= 0.5
            ]
            if candidates:
                return ExplorationResult(
                    **base,
                    dimension_candidates=candidates,
                    findings={
                        "candidate_count": len(candidates),
                        "candidates": [c.name for c in candidates],
                    },
                    confidence=max(c.confidence for c in candidates),
                    limitations=["candidates are INFERRED; register + verify before trusted use"],
                    decision=ExplorationDecision.VALIDATE_CANDIDATE,
                )
            return ExplorationResult(
                **base,
                findings={"candidate_count": 0},
                confidence=0.0,
                limitations=["no categorical dimension candidate found in the schema"],
                decision=ExplorationDecision.ASK_USER,
            )
        if question.target == "time_column":
            candidates = derive_dimension_candidates(
                profile.columns,
                cardinalities=profile.cardinalities,
                data_asset_id=profile.data_asset_id,
            )
            temporal = [c for c in candidates if c.semantic_type == "temporal"]
            if temporal:
                best = temporal[0]
                return ExplorationResult(
                    **base,
                    time_column=best.source_column,
                    findings={"time_column": best.source_column, "dtype": profile.columns.get(best.source_column)},
                    confidence=best.confidence,
                    evidence_refs=[f"schema:{profile.data_asset_id}:{best.source_column}"],
                    limitations=["time column verified by dtype pattern only"],
                    decision=ExplorationDecision.BUILD_PLAN,
                )
            return ExplorationResult(
                **base,
                findings={"time_column": None},
                confidence=0.0,
                limitations=["no temporal column detected"],
                decision=ExplorationDecision.ASK_USER,
            )
        if question.target == "grain":
            nulls = {k: v for k, v in profile.null_ratios.items() if v > 0.2}
            return ExplorationResult(
                **base,
                findings={
                    "row_estimate": profile.row_estimate,
                    "high_null_columns": list(nulls),
                },
                confidence=0.6,
                limitations=["grain inferred from schema only"],
                decision=ExplorationDecision.EXPLORE_MORE if nulls else ExplorationDecision.BUILD_PLAN,
            )
        return ExplorationResult(
            **base,
            findings={},
            confidence=0.0,
            limitations=[f"unknown exploration target: {question.target}"],
            decision=ExplorationDecision.STOP,
        )


class ExplorationLedger:
    """Bounded, auditable exploration history with redundancy detection."""

    MAX_EXPLORATIONS = 5

    def __init__(self) -> None:
        self._results: list[ExplorationResult] = []
        self._seen_questions: set[str] = set()
        self.redundant_count = 0

    @property
    def results(self) -> list[ExplorationResult]:
        return list(self._results)

    @property
    def exhausted(self) -> bool:
        return len(self._results) >= self.MAX_EXPLORATIONS

    def record(self, result: ExplorationResult) -> ExplorationResult:
        if self.exhausted:
            raise ValueError(
                f"exploration budget exhausted ({self.MAX_EXPLORATIONS}); stop exploring"
            )
        key = f"{result.question_id}:{result.operation}"
        if key in self._seen_questions:
            self.redundant_count += 1
            raise ValueError(f"redundant exploration: {key} already executed")
        self._seen_questions.add(key)
        self._results.append(result)
        return result


class ExplorationDecider:
    """Chooses the next exploration from the intent's actual unknowns.

    The decision depends on the intent AND the previous exploration result — two
    different results lead to different next steps (no fixed pipeline).
    """

    def __init__(self, registry: SemanticRegistry, engine: Optional[SchemaExplorationEngine] = None) -> None:
        self._registry = registry
        self._engine = engine or SchemaExplorationEngine()

    def next_question(self, intent: AnalysisIntent, *, data_asset_id: str) -> Optional[ExplorationQuestion]:
        if intent.status is IntentStatus.NEEDS_CLARIFICATION:
            return None
        for dim in intent.dimensions:
            if dim.entity_id is None:
                return ExplorationQuestion(
                    question_id=f"q_dim_{dim.term}",
                    question=f"Is there a usable dimension for term '{dim.term}' in the data?",
                    target="dimension_candidates",
                    data_asset_id=data_asset_id,
                )
        if intent.analysis_type is AnalysisType.TREND and (
            intent.time is None or intent.time.granularity is None
        ):
            return ExplorationQuestion(
                question_id="q_time_column",
                question="Which column is a usable time column for the trend?",
                target="time_column",
                data_asset_id=data_asset_id,
            )
        return None

    def apply_result(
        self,
        intent: AnalysisIntent,
        result: ExplorationResult,
        *,
        data_asset_id: str,
    ) -> ExplorationDecision:
        """Fold an exploration result back into the intent (real state change)."""
        if result.decision is ExplorationDecision.VALIDATE_CANDIDATE and result.dimension_candidates:
            # Candidate verified against the schema: register as DECLARED dimension.
            for candidate in result.dimension_candidates:
                if self._registry.dimension(candidate.dimension_id) is None:
                    self._registry.register_dimension(
                        candidate.model_copy(
                            update={
                                "validation_status": __import__(
                                    "langgraph_langchain.analysis_intelligence.semantic_contracts",
                                    fromlist=["ValidationStatus"],
                                ).ValidationStatus.DECLARED,
                            }
                        )
                    )
            if intent.dimensions and intent.dimensions[0].entity_id is None:
                term = intent.dimensions[0].term.casefold()
                resolved = next(
                    (c for c in result.dimension_candidates if c.name.casefold() == term),
                    result.dimension_candidates[0],
                )
                intent.dimensions[0] = intent.dimensions[0].model_copy(
                    update={"entity_id": resolved.dimension_id, "resolution_method": "explored", "confidence": resolved.confidence}
                )
            intent.status_reasons.append(
                f"exploration {result.exploration_id} resolved dimension candidate"
            )
            self._refresh_intent_status(intent)
            return result.decision
        if result.decision is ExplorationDecision.BUILD_PLAN and result.time_column:
            if intent.time is not None and intent.time.granularity is None:
                intent.time = intent.time.model_copy(update={"granularity": "monthly"})
            intent.status_reasons.append(
                f"exploration {result.exploration_id} resolved time column {result.time_column}"
            )
            self._refresh_intent_status(intent)
            return result.decision
        if result.decision is ExplorationDecision.ASK_USER:
            intent.status = IntentStatus.NEEDS_CLARIFICATION
            intent.status_reasons.append(
                f"exploration {result.exploration_id} could not resolve the unknown"
            )
            return result.decision
        return result.decision

    def _refresh_intent_status(self, intent: AnalysisIntent) -> None:
        unresolved_dims = [d for d in intent.dimensions if d.entity_id is None]
        if intent.analysis_type is AnalysisType.TREND and (
            intent.time is None or intent.time.granularity is None
        ):
            intent.status = IntentStatus.NEEDS_EXPLORATION
        elif unresolved_dims:
            intent.status = IntentStatus.NEEDS_EXPLORATION
        elif intent.assumptions:
            intent.status = IntentStatus.READY_WITH_ASSUMPTIONS
        else:
            intent.status = IntentStatus.READY

    def build_plan_after_exploration(
        self,
        intent: AnalysisIntent,
        planner,  # IntentPlanner
        *,
        input_asset_ids: Sequence[str],
        asset_columns: Mapping[str, Sequence[str]],
        time_column: Optional[str] = None,
    ):
        try:
            return planner.plan_from_intent(
                intent,
                input_asset_ids=input_asset_ids,
                asset_columns=asset_columns,
                time_column=time_column,
            )
        except PlanningBlockedError:
            raise

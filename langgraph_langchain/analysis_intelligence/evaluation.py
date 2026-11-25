"""Analysis Intelligence evaluation (V1.0, P6).

Deterministic end-to-end case runner: intent -> exploration -> constraint-checked plan ->
REAL computation through the allowlisted operations -> evidence/claim verification.
Positive cases must compute correctly; negative cases must be rejected at the right
stage; execution success never substitutes for analysis correctness.
"""
from __future__ import annotations

from enum import Enum
from typing import Callable, Optional

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from langgraph_langchain.analysis_intelligence.constraints import (
    IntentPlanner,
    PlanningBlockedError,
)
from langgraph_langchain.analysis_intelligence.exploration import (
    ExplorationDecider,
    ExplorationLedger,
    ExplorationQuestion,
    SchemaExplorationEngine,
    SchemaProfile,
)
from langgraph_langchain.analysis_intelligence.intent import (
    AnalysisIntent,
    AnalysisType,
    IntentResolver,
    IntentStatus,
)
from langgraph_langchain.analysis_intelligence.reasoning import (
    AnalysisState,
    Claim,
    ClaimType,
    ClaimVerifier,
    ClaimVerdict,
    EvidenceRecord,
    TaskOutcome,
)
from langgraph_langchain.analysis_intelligence.semantic_contracts import (
    SemanticRegistry,
)
from langgraph_langchain.runtime.analysis_operations import (
    execute_analysis_operation,
)


class CaseKind(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    BOUNDARY = "boundary"
    CONSTRAINT = "constraint"
    DYNAMIC = "dynamic"
    MULTI_STEP = "multi_step"


class ExpectedRejection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: str  # intent | planning | claim
    reason_contains: str


class IntelligenceCase(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    case_id: str
    kind: CaseKind
    question: str
    registry_factory: Callable[[], SemanticRegistry]
    frame_factory: Callable[[], pd.DataFrame]
    asset_id: str = "asset_sales"
    expected_metrics: list[str] = Field(default_factory=list)
    expected_dimensions: list[str] = Field(default_factory=list)
    expected_analysis_type: Optional[AnalysisType] = None
    expected_group_by: Optional[str] = None
    expected_value_column: Optional[str] = None
    ground_truth: Optional[dict[str, float]] = Field(default_factory=dict)
    expected_rejection: Optional[ExpectedRejection] = None


class CaseResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    kind: CaseKind
    passed: bool
    critical_gates: dict[str, bool] = Field(default_factory=dict)
    failures: list[str] = Field(default_factory=list)
    score: float = 0.0
    intent_status: str = ""
    plan_operations: list[str] = Field(default_factory=list)
    computed: dict[str, float] = Field(default_factory=dict)
    executed: bool = False


WEIGHTS = {"intent": 0.2, "planning": 0.2, "computation": 0.3, "evidence": 0.2, "behavior": 0.1}


class IntelligenceCaseRunner:
    def run(self, case: IntelligenceCase) -> CaseResult:
        registry = case.registry_factory()
        frame = case.frame_factory()
        failures: list[str] = []
        gates: dict[str, bool] = {}
        plan_operations: list[str] = []
        computed: dict[str, float] = {}
        executed = False
        intent_status = ""

        resolver = IntentResolver(registry, known_columns=list(frame.columns))
        intent = resolver.resolve(case.question, intent_id=f"intent_{case.case_id}")
        intent_status = intent.status.value

        gates["intent_type"] = (
            case.expected_analysis_type is None
            or intent.analysis_type is case.expected_analysis_type
        )
        if not gates["intent_type"]:
            failures.append(f"intent type {intent.analysis_type} != expected {case.expected_analysis_type}")
        resolved_metrics = intent.metric_ids()
        gates["intent_metrics"] = (
            not case.expected_metrics or resolved_metrics == case.expected_metrics
        )
        # dimension gate evaluated after the exploration loop (dynamic discovery
        # folds candidates back into the intent)

        # Exploration loop (bounded) for NEEDS_EXPLORATION intents.
        decider = ExplorationDecider(registry)
        ledger = ExplorationLedger()
        time_column: Optional[str] = None
        profile = SchemaProfile(
            data_asset_id=case.asset_id,
            columns={name: str(dtype) for name, dtype in frame.dtypes.items()},
            cardinalities={c: int(frame[c].nunique()) for c in frame.columns},
        )
        while intent.status is IntentStatus.NEEDS_EXPLORATION and not ledger.exhausted:
            question = decider.next_question(intent, data_asset_id=case.asset_id)
            if question is None:
                break
            try:
                result = SchemaExplorationEngine().explore(question, profile, registry)
                ledger.record(result)
            except ValueError:
                failures.append("exploration budget exhausted before resolution")
                break
            decider.apply_result(intent, result, data_asset_id=case.asset_id)
            if result.time_column:
                time_column = result.time_column
            intent_status = intent.status.value

        resolved_dims = intent.dimension_ids()
        gates["intent_dimensions"] = (
            not case.expected_dimensions or resolved_dims == case.expected_dimensions
        )
        planner = IntentPlanner(registry)
        plan = None
        blocked_reasons: list[str] = []
        try:
            outcome = planner.plan_from_intent(
                intent,
                input_asset_ids=[case.asset_id],
                asset_columns={case.asset_id: list(frame.columns)},
                time_column=time_column,
            )
            plan = outcome.plan
        except PlanningBlockedError as exc:
            blocked_reasons = exc.reasons
            # Planning contract: trend requires a verified time column. Follow
            # the same exploration trust path as dimensions, then retry once.
            if case.expected_rejection is None and any(
                "verified time column" in reason for reason in blocked_reasons
            ):
                time_question = ExplorationQuestion(
                    question_id=f"q_time_{case.case_id}",
                    question="Which column is a usable time column for the trend?",
                    target="time_column",
                    data_asset_id=case.asset_id,
                )
                try:
                    time_result = SchemaExplorationEngine().explore(time_question, profile, registry)
                    ledger.record(time_result)
                except ValueError:
                    time_result = None
                if time_result is not None:
                    decider.apply_result(intent, time_result, data_asset_id=case.asset_id)
                    if time_result.time_column:
                        time_column = time_result.time_column
                        try:
                            outcome = planner.plan_from_intent(
                                intent,
                                input_asset_ids=[case.asset_id],
                                asset_columns={case.asset_id: list(frame.columns)},
                                time_column=time_column,
                            )
                            plan = outcome.plan
                            blocked_reasons = []
                        except PlanningBlockedError as exc_retry:
                            blocked_reasons = exc_retry.reasons

        # Rejection gates.
        if case.expected_rejection is not None:
            stage = case.expected_rejection.stage
            if stage == "intent":
                gates["rejection"] = intent.status.value in {
                    "needs_clarification", "unsupported"
                } and case.expected_rejection.reason_contains in " ".join(
                    intent.status_reasons + intent.ambiguity
                )
            elif stage == "planning":
                gates["rejection"] = plan is None and any(
                    case.expected_rejection.reason_contains in reason for reason in blocked_reasons
                )
            if not gates["rejection"]:
                failures.append(f"expected rejection at {stage} did not occur correctly")
        elif plan is None:
            # No expected rejection: a blocked plan for a case that should
            # execute is a failure, never a silent pass.
            gates["rejection"] = True
            gates["planning_ops"] = False
            failures.append("plan was blocked for a case that expected execution")

        # Plan execution with REAL allowlisted operations.
        if plan is not None and case.expected_rejection is None:
            gates["planning_ops"] = True
            plan_operations = [s.operation for s in plan.steps]
            current = frame.copy()
            executed = True
            try:
                for step in plan.steps:
                    current = execute_analysis_operation(
                        current, operation=step.operation, parameters=step.parameters
                    )
            except Exception as exc:  # noqa: BLE001 - evaluation records the failure
                gates["planning_ops"] = False
                failures.append(f"plan execution failed: {exc}")
                current = None
            if current is not None and case.ground_truth:
                gates["computation"] = True
                first_col = current.columns[0] if len(current.columns) else ""
                if case.expected_group_by and first_col in current.columns:
                    for key, expected in case.ground_truth.items():
                        row = current[current[first_col].astype(str) == key]
                        if row.empty:
                            gates["computation"] = False
                            failures.append(f"missing computed group {key}")
                            continue
                        value_col = [
                            c for c in current.columns if c != first_col
                        ][0]
                        actual = float(row[value_col].iloc[0])
                        computed[key] = actual
                        if abs(actual - expected) > 1e-6:
                            gates["computation"] = False
                            failures.append(
                                f"group {key}: computed {actual} != ground truth {expected}"
                            )
                else:
                    gates["computation"] = False
                    failures.append("computation output shape mismatch")

        # Evidence/claim verification with a deliberately wrong claim (critical gate):
        # a successful execution must NOT make an incorrect analysis pass.
        if case.ground_truth and computed:
            first_key = next(iter(computed))
            state = AnalysisState(
                intent_user_goal=intent.user_goal,
                verifier=ClaimVerifier(_evidence_from(computed, case)),
            )
            state.record_result(TaskOutcome(task_id="eval_task", status="succeeded"))
            good_claim = Claim(
                claim_id=f"claim_{case.case_id}", claim_type=ClaimType.CONCLUSION,
                text=f"{first_key} = {computed[first_key]}",
                evidence_ids=[f"ev_{case.case_id}_{first_key}"],
                metric_id=case.expected_metrics[0] if case.expected_metrics else None,
                data_scope=case.expected_group_by or "",
                value=computed[first_key],
            )
            verification = state.add_claim(good_claim)
            gates["evidence_binding"] = verification.verdict is ClaimVerdict.SUPPORTED
            wrong_claim = good_claim.model_copy(
                update={
                    "claim_id": f"wrong_{case.case_id}",
                    "value": computed[first_key] + 12345.0,
                }
            )
            wrong_verification = state.add_claim(wrong_claim)
            gates["wrong_analysis_detected"] = (
                wrong_verification.verdict is ClaimVerdict.REJECTED
            )
            if not gates["wrong_analysis_detected"]:
                failures.append("incorrect analysis was not rejected — evaluator integrity failure")
        else:
            gates["evidence_binding"] = True
            gates["wrong_analysis_detected"] = True

        # Behavior gates for rejection cases.
        if case.expected_rejection is not None:
            if case.expected_rejection.stage == "intent":
                gates["planning_ops"] = True
                gates["computation"] = True
                gates["evidence_binding"] = True
                gates["wrong_analysis_detected"] = True

        score = (
            WEIGHTS["intent"] * (1.0 if gates.get("intent_type") and gates.get("intent_metrics") and gates.get("intent_dimensions") else 0.0)
            + WEIGHTS["planning"] * (1.0 if gates.get("planning_ops") else 0.0)
            + WEIGHTS["computation"] * (1.0 if gates.get("computation") else 0.0)
            + WEIGHTS["evidence"] * (1.0 if gates.get("evidence_binding") else 0.0)
            + WEIGHTS["behavior"] * (1.0 if gates.get("rejection", True) and gates.get("wrong_analysis_detected") else 0.0)
        )
        passed = all(gates.values())
        return CaseResult(
            case_id=case.case_id,
            kind=case.kind,
            passed=passed,
            critical_gates=gates,
            failures=failures,
            score=round(score, 4),
            intent_status=intent_status,
            plan_operations=plan_operations,
            computed=computed,
            executed=executed,
        )


def _evidence_from(computed: dict[str, float], case: IntelligenceCase) -> dict[str, EvidenceRecord]:
    evidence = {}
    for key, value in computed.items():
        evidence[f"ev_{case.case_id}_{key}"] = EvidenceRecord(
            evidence_id=f"ev_{case.case_id}_{key}",
            metric_id=case.expected_metrics[0] if case.expected_metrics else None,
            dimension_id=case.expected_group_by,
            data_scope=f"{case.expected_group_by or ''}={key}",
            value=value,
            method="group_aggregate",
            source_execution_id=f"exec_{case.case_id}",
            verified=True,
        )
    return evidence


class IntelligenceReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_cases: int
    passed_cases: int
    failed_cases: int
    pass_rate: float
    average_score: float
    by_kind: dict[str, int]
    results: list[CaseResult]


class IntelligenceEvaluator:
    def run_suite(self, cases: list[IntelligenceCase]) -> IntelligenceReport:
        results = [IntelligenceCaseRunner().run(case) for case in cases]
        passed = [r for r in results if r.passed]
        by_kind: dict[str, int] = {}
        for result in results:
            if not result.passed:
                by_kind[result.kind.value] = by_kind.get(result.kind.value, 0) + 1
        return IntelligenceReport(
            total_cases=len(results),
            passed_cases=len(passed),
            failed_cases=len(results) - len(passed),
            pass_rate=round(len(passed) / len(results), 4) if results else 0.0,
            average_score=round(sum(r.score for r in results) / len(results), 4) if results else 0.0,
            by_kind=by_kind,
            results=results,
        )

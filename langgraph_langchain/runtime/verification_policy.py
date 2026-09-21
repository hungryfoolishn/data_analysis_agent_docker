"""Deterministic verification policy for Runtime V7.

The policy turns verification from an optional annotation into a task-aware
quality gate.  It uses only explicit verification contracts supplied by tasks;
it never infers business numbers from free-form output.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from langgraph_langchain.runtime.models import ExecutionResult
from langgraph_langchain.schemas import AnalysisTask, VerificationResult
from langgraph_langchain.verification import (
    verify_aggregation_consistency,
    verify_artifact_existence,
    verify_execution_result,
    verify_group_consistency,
    verify_numeric_consistency,
    verify_schema_consistency,
    verify_time_consistency,
)


_DEFAULT_BY_TASK_TYPE: dict[str, tuple[str, ...]] = {
    "schema": ("artifact_existence", "schema_consistency"),
    "profile": ("artifact_existence",),
    "metric": ("numeric_consistency",),
    "comparison": ("group_consistency",),
    "trend": ("time_consistency",),
    "breakdown": ("aggregation_consistency",),
    "contribution": ("aggregation_consistency",),
    "anomaly": ("artifact_existence",),
    "visualization": ("artifact_existence",),
    "report": ("evidence_existence",),
}


def _value(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _contract(task: Any) -> dict[str, Any]:
    constraints = _value(task, "constraints", {}) or {}
    if not isinstance(constraints, Mapping):
        return {}
    contract = constraints.get("verification_contract")
    return dict(contract) if isinstance(contract, Mapping) else {}


class VerificationPolicy:
    """Return deterministic checks required by a scheduled task's contract."""

    def __init__(
        self,
        *,
        required_by_task_type: Optional[Mapping[str, Sequence[str]]] = None,
        strict_missing_contract: bool = False,
    ) -> None:
        self.required_by_task_type = dict(required_by_task_type or {})
        self.strict_missing_contract = strict_missing_contract

    def required_for(self, task: AnalysisTask) -> tuple[str, ...]:
        explicit = self.required_by_task_type.get(str(_value(task, "task_type", "")))
        if explicit:
            return tuple(explicit)
        task_type = str(_value(task, "task_type", "")).lower()
        return _DEFAULT_BY_TASK_TYPE.get(task_type, ("artifact_existence",))

    def _verify_contract_check(
        self,
        *,
        check_type: str,
        contract: Mapping[str, Any],
        task: AnalysisTask,
        execution: ExecutionResult,
    ) -> Optional[VerificationResult]:
        spec = contract.get(check_type)
        if spec is None and check_type in {"numeric_consistency", "aggregation_consistency", "time_consistency", "group_consistency", "schema_consistency"}:
            if self.strict_missing_contract:
                return VerificationResult(
                    check_type="custom",
                    status="failed",
                    passed=False,
                    expected=f"{check_type} verification contract",
                    actual=None,
                    message=f"Task requires {check_type} but no verification contract was supplied",
                )
            return None
        if not isinstance(spec, Mapping):
            return VerificationResult(
                check_type="custom",
                status="failed",
                passed=False,
                expected=f"{check_type} contract mapping",
                actual=spec,
                message=f"Invalid {check_type} verification contract",
            )

        context = {
            "run_id": _value(execution, "run_id"),
            "task_id": _value(execution, "task_id"),
            "step_id": _value(execution, "step_id"),
            "execution_id": _value(execution, "execution_id"),
        }
        tolerance = spec.get("tolerance", 0.0)

        if check_type == "numeric_consistency":
            return verify_numeric_consistency(
                expected=spec.get("expected"),
                actual=spec.get("actual"),
                tolerance=tolerance,
                context=context,
            )
        if check_type == "aggregation_consistency":
            return verify_aggregation_consistency(
                total=spec.get("total"),
                components=spec.get("components"),
                tolerance=tolerance,
                component_field=str(spec.get("component_field", "value")),
                context=context,
            )
        if check_type == "time_consistency":
            return verify_time_consistency(
                start_time=spec.get("start_time"),
                end_time=spec.get("end_time"),
                context=context,
            )
        if check_type == "group_consistency":
            return verify_group_consistency(
                expected=spec.get("expected"),
                actual=spec.get("actual"),
                tolerance=tolerance,
                context=context,
            )
        if check_type == "schema_consistency":
            return verify_schema_consistency(
                expected_fields=spec.get("expected_fields"),
                actual_fields=spec.get("actual_fields"),
                context=context,
            )
        return None

    def verify(
        self,
        task: AnalysisTask,
        execution: ExecutionResult,
        *,
        artifacts: Optional[Mapping[str, Any]] = None,
        workspace_dir: Any = None,
    ) -> list[VerificationResult]:
        """Return all policy checks for one execution result."""
        contract = _contract(task)
        required = self.required_for(task)
        checks: list[VerificationResult] = []

        # Artifact checks are useful for every task and are the existing V2 gate.
        checks.extend(
            verify_execution_result(
                execution,
                artifacts=artifacts,
                workspace_dir=workspace_dir,
                context={
                    "run_id": _value(execution, "run_id"),
                    "task_id": _value(execution, "task_id"),
                    "step_id": _value(execution, "step_id"),
                    "execution_id": _value(execution, "execution_id"),
                },
            )
        )

        for check_type in required:
            check = self._verify_contract_check(
                check_type=check_type,
                contract=contract,
                task=task,
                execution=execution,
            )
            if check is not None:
                checks.append(check)

        # Every task must receive at least one verification record.  This makes
        # the quality gate explicit even for scalar tasks without artifacts.
        if not checks:
            checks.append(
                VerificationResult(
                    run_id=_value(execution, "run_id"),
                    task_id=_value(execution, "task_id"),
                    step_id=_value(execution, "step_id"),
                    execution_id=_value(execution, "execution_id"),
                    check_type="custom",
                    status="passed",
                    passed=True,
                    expected="succeeded execution",
                    actual=_value(execution, "status"),
                    message="Execution completed successfully",
                )
            )
        return checks

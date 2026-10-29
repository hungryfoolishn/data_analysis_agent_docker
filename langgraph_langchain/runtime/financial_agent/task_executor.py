"""Task-level financial execution adapter for Runtime V10.3.1.

The adapter executes one ``ExecutionTask`` at a time, reuses the V9.3 metric
and verification engines, and exposes dependency outputs through a typed
store so derived metrics can consume upstream task results.
"""

from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel

from langgraph_langchain.runtime.financial.metrics import (
    CALCULATED,
    INVALID,
    UNAVAILABLE,
    MetricComputation,
    compute_metric,
    financial_metric_registry,
)
from langgraph_langchain.runtime.financial.period import (
    PeriodBasisType,
    PeriodComparisonType,
    PeriodNormalizer,
    PeriodNormalizationError,
    PeriodType,
)
from langgraph_langchain.runtime.financial.period_basis import (
    derive_single_period_cash_flow,
    derive_single_period_income,
    is_first_cumulative_period,
    statement_source_ids,
)
from langgraph_langchain.runtime.financial.models import (
    FinancialCalculation,
    FinancialEvidence,
    FinancialObservation,
    FinancialVerification,
)

from .execution_models import ExecutionTask
from .models import ExecutionStatus
from .scheduler import TaskExecutionOutcome

TASK_EXECUTION_ADAPTER_NAME = "financial_task_v10_3_1"
_TASK_PERIOD_NORMALIZER = PeriodNormalizer()


class TaskExecutionOutput(BaseModel):
    """A successful task output that downstream tasks may consume."""

    task_id: str
    company_id: str
    company_name: str
    period: str
    metric_id: str
    value: float
    calculation: FinancialCalculation
    verification: FinancialVerification
    evidence: FinancialEvidence
    observation: FinancialObservation


class TaskOutputStore:
    """Store successful task outputs and resolve dependency outputs."""

    def __init__(self) -> None:
        self._outputs: dict[str, TaskExecutionOutput] = {}

    def record(
        self,
        task: ExecutionTask,
        outcome: TaskExecutionOutcome,
    ) -> None:
        if outcome.status != ExecutionStatus.SUCCEEDED:
            return
        output = outcome.output
        if not isinstance(output, TaskExecutionOutput):
            raise TypeError(
                "Successful task executor output must be TaskExecutionOutput, "
                f"got {type(output).__name__}."
            )
        if output.task_id != task.task_id:
            raise ValueError(
                "Task execution output task_id mismatch: "
                f"expected {task.task_id}, got {output.task_id}."
            )
        self._outputs[task.task_id] = output

    def has(self, task_id: str) -> bool:
        return task_id in self._outputs

    def get(self, task_id: str) -> TaskExecutionOutput:
        return self._outputs[task_id]

    def dependency_outputs(self, task: ExecutionTask) -> list[TaskExecutionOutput]:
        return [
            self._outputs[dependency_id]
            for dependency_id in dict.fromkeys(task.dependencies)
            if dependency_id in self._outputs
        ]

    def outputs_in_plan_order(
        self,
        tasks: list[ExecutionTask],
    ) -> list[TaskExecutionOutput]:
        return [
            self._outputs[task.task_id]
            for task in tasks
            if task.task_id in self._outputs
        ]


class TaskLevelFinancialExecutor:
    """Execute a financial task with real metric, verification, and evidence."""

    def __init__(self, workflow, output_store: TaskOutputStore) -> None:
        self.workflow = workflow
        self.data_service = workflow.data_service
        self.verification_engine = workflow.verification_engine
        self.output_store = output_store

    def __call__(self, task: ExecutionTask) -> TaskExecutionOutcome:
        try:
            return self._execute(task)
        except KeyError as exc:
            return self._data_unavailable(task, f"missing_financial_data: {exc}")
        except Exception as exc:  # noqa: BLE001 - task execution boundary
            error = f"{type(exc).__name__}: {exc}"
            return self._failure(task, error)

    def _execute(self, task: ExecutionTask) -> TaskExecutionOutcome:
        definition = financial_metric_registry.get_optional(task.metric_id)
        if definition is None:
            return self._failure(
                task,
                f"unsupported financial metric: {task.metric_id}",
            )
        try:
            parsed_period = _TASK_PERIOD_NORMALIZER.parse(task.period)
        except PeriodNormalizationError as exc:
            reason = (
                "unsupported_growth_period:ttm"
                if "TTM" in str(exc).upper() or "LTM" in str(exc).upper()
                else f"unsupported_period:{exc}"
            )
            return self._data_unavailable(task, reason)
        if parsed_period.period_type == PeriodType.TTM:
            return self._data_unavailable(
                task,
                "unsupported_growth_period:ttm",
            )
        company = self.data_service.resolve_company(task.company_name)
        comparison_type = PeriodComparisonType(
            task.inputs.get("growth_comparison_type")
            or PeriodComparisonType.YEAR_OVER_YEAR
        )
        period_basis = PeriodBasisType(
            task.inputs.get("growth_period_basis")
            or PeriodBasisType.CUMULATIVE
        )
        previous_period = task.inputs.get("previous_period")
        if previous_period is None:
            previous_period = self.data_service.previous_period(
                company.company_id,
                task.period,
                comparison_type=(
                    comparison_type
                    if definition.calculation_type == "growth"
                    else PeriodComparisonType.SEQUENTIAL
                ),
            )
        income, balance, cash_flow, previous_income = self.data_service.statements(
            company.company_id,
            task.period,
            previous_period,
        )
        previous_balance = self.data_service.previous_balance(
            company.company_id,
            task.period,
        )
        conversion_source_ids: list[str] = []

        input_sources: dict[str, str] = {}
        if definition.calculation_type == "growth":
            growth_income = income
            growth_cash_flow = cash_flow
            growth_previous_income = previous_income
            if period_basis == PeriodBasisType.SINGLE_PERIOD:
                current_predecessor_period = self.data_service.previous_period(
                    company.company_id,
                    task.period,
                    comparison_type=PeriodComparisonType.SEQUENTIAL,
                )
                baseline_predecessor_period = (
                    self.data_service.previous_period(
                        company.company_id,
                        previous_period,
                        comparison_type=PeriodComparisonType.SEQUENTIAL,
                    )
                    if previous_period is not None
                    else None
                )
                _, _, current_predecessor_cash_flow, current_predecessor_income = (
                    self.data_service.statements(
                        company.company_id,
                        task.period,
                        current_predecessor_period,
                    )
                )
                _, _, baseline_cash_flow, baseline_income = (
                    self.data_service.statements(
                        company.company_id,
                        previous_period,
                        baseline_predecessor_period,
                    )
                    if previous_period is not None
                    else (None, None, None, None)
                )
                current_is_first = is_first_cumulative_period(
                    task.period,
                    normalizer=_TASK_PERIOD_NORMALIZER,
                )
                baseline_is_first = (
                    is_first_cumulative_period(
                        previous_period,
                        normalizer=_TASK_PERIOD_NORMALIZER,
                    )
                    if previous_period is not None
                    else True
                )
                growth_income = derive_single_period_income(
                    income,
                    current_predecessor_income,
                    is_first_period=current_is_first,
                )
                growth_cash_flow = derive_single_period_cash_flow(
                    cash_flow,
                    current_predecessor_cash_flow,
                    is_first_period=current_is_first,
                )
                growth_previous_income = (
                    derive_single_period_income(
                        previous_income,
                        baseline_income,
                        is_first_period=baseline_is_first,
                    )
                    if previous_income is not None
                    else None
                )
                conversion_source_ids = statement_source_ids(
                    income,
                    current_predecessor_income,
                    previous_income,
                    baseline_income,
                )
            computation = self._compute_growth(
                task,
                definition=definition,
                income=growth_income,
                balance=balance,
                cash_flow=growth_cash_flow,
                previous_income=growth_previous_income,
                previous_period=previous_period,
                comparison_type=comparison_type,
                period_basis=period_basis,
                input_sources=input_sources,
            )
            formula = self._comparison_formula(
                definition.formula,
                comparison_type,
                period_basis,
            )
        else:
            computation = compute_metric(
                task.metric_id,
                income=income,
                balance=balance,
                cash_flow=cash_flow,
                previous_income=previous_income,
                previous_balance=previous_balance,
            )
            input_sources["value"] = "data_service"
            formula = definition.formula

        source_ids = self.workflow._source_ids_for_metric(
            definition,
            income=income,
            balance=balance,
            cash_flow=cash_flow,
            previous_income=previous_income,
            previous_balance=previous_balance,
        )
        source_ids = list(dict.fromkeys([*source_ids, *conversion_source_ids]))
        calculation = FinancialCalculation(
            metric_id=task.metric_id,
            company_id=company.company_id,
            company_name=company.company_name,
            period=task.period,
            formula=formula,
            inputs=computation.inputs,
            result=computation.value,
            unit=definition.unit,
            status=computation.status,
            status_reason=computation.reason,
            missing_fields=computation.missing_fields,
            source_ids=source_ids,
            source_fields=list(definition.source_fields),
        )

        if computation.status == UNAVAILABLE:
            return self._data_unavailable(
                task,
                computation.reason or "metric_unavailable",
                calculation_id=calculation.calculation_id,
            )
        if computation.status == INVALID:
            return self._failure(
                task,
                computation.reason or "metric_invalid",
                calculation_id=calculation.calculation_id,
            )

        verification = self.verification_engine.verify(
            calculation=calculation,
            metric_id=task.metric_id,
            income=(
                growth_income
                if definition.calculation_type == "growth"
                else income
            ),
            balance=balance,
            cash_flow=(
                growth_cash_flow
                if definition.calculation_type == "growth"
                else cash_flow
            ),
            previous_income=(
                growth_previous_income
                if definition.calculation_type == "growth"
                else previous_income
            ),
            previous_balance=previous_balance,
        )
        if not verification.passed:
            return self._failure(
                task,
                verification.message or "verification_failed",
                calculation_id=calculation.calculation_id,
                verification_id=verification.verification_id,
            )

        evidence = FinancialEvidence(
            metric_id=task.metric_id,
            metric_name=definition.name,
            company_id=company.company_id,
            company_name=company.company_name,
            stock_code=company.stock_code,
            period=task.period,
            value=computation.value,
            unit=definition.unit,
            formula=formula,
            fact=(
                f"{company.company_name} {task.period} "
                f"{definition.name}为{computation.value:,.4f}{definition.unit}"
            ),
            source_ids=source_ids,
            source_fields=list(definition.source_fields),
            calculation_id=calculation.calculation_id,
            verification_result_id=verification.verification_id,
            verification_status="verified",
        )
        observation = FinancialObservation(
            company_id=company.company_id,
            company_name=company.company_name,
            stock_code=company.stock_code,
            period=task.period,
            metric_id=task.metric_id,
            metric_name=definition.name,
            value=computation.value,
            unit=definition.unit,
            formula=definition.formula,
            fact=evidence.fact,
            calculation=calculation,
            source_ids=source_ids,
            source_fields=list(definition.source_fields),
            source_period=task.period,
            balance_policy=definition.balance_policy,
        )
        output = TaskExecutionOutput(
            task_id=task.task_id,
            company_id=company.company_id,
            company_name=company.company_name,
            period=task.period,
            metric_id=task.metric_id,
            value=computation.value,
            calculation=calculation,
            verification=verification,
            evidence=evidence,
            observation=observation,
        )
        output_refs = [
            calculation.calculation_id,
            verification.verification_id,
            evidence.evidence_id,
        ]
        return TaskExecutionOutcome(
            status=ExecutionStatus.SUCCEEDED,
            status_reason="task succeeded",
            output_refs=output_refs,
            metadata={
                "execution_adapter": TASK_EXECUTION_ADAPTER_NAME,
                "calculation_id": calculation.calculation_id,
                "verification_id": verification.verification_id,
                "evidence_id": evidence.evidence_id,
                "input_sources": input_sources,
                "growth_comparison_type": comparison_type.value,
                "growth_period_basis": period_basis.value,
                "comparison_period": previous_period,
            },
            output=output,
        )

    def _compute_growth(
        self,
        task: ExecutionTask,
        *,
        definition,
        income,
        balance,
        cash_flow,
        previous_income,
        previous_period: str | None,
        comparison_type: PeriodComparisonType,
        period_basis: PeriodBasisType,
        input_sources: dict[str, str],
    ) -> MetricComputation:
        base_metric_id = definition.growth_of or ""
        # A cumulative base-metric task cannot be mixed with a single-period
        # growth contract. In that case derive both sides from source
        # statements so cumulative and single-period semantics stay explicit.
        dependency_outputs = (
            {}
            if period_basis == PeriodBasisType.SINGLE_PERIOD
            else {
                (item.period, item.metric_id): item
                for item in self.output_store.dependency_outputs(task)
            }
        )
        current_output = dependency_outputs.get((task.period, base_metric_id))
        previous_output = (
            dependency_outputs.get((previous_period, base_metric_id))
            if previous_period is not None
            else None
        )

        current: float | None = None
        previous: float | None = None

        if current_output is not None:
            current = current_output.value
            input_sources["current"] = f"dependency_task:{current_output.task_id}"
        else:
            input_sources["current"] = "data_service"
            current_computation = compute_metric(
                base_metric_id,
                income=income,
                balance=balance,
                cash_flow=cash_flow,
            )
            if current_computation.status != CALCULATED:
                return self._growth_base_status(
                    task,
                    phase="current",
                    base_metric_id=base_metric_id,
                    computation=current_computation,
                    other_value=previous,
                )
            current = current_computation.value

        if previous_output is not None:
            previous = previous_output.value
            input_sources["previous"] = f"dependency_task:{previous_output.task_id}"
        else:
            input_sources["previous"] = "data_service"
            if previous_income is None:
                # Missing baseline must be explicit and match the workflow
                # path semantics: it is data unavailability, not a zero value.
                return MetricComputation(
                    metric_id=task.metric_id,
                    status=UNAVAILABLE,
                    inputs={"current": current, "previous": None},
                    missing_fields=[f"previous.{base_metric_id}"],
                    reason="growth_previous_period_missing",
                )
            else:
                previous_computation = compute_metric(
                    base_metric_id,
                    income=previous_income,
                    balance=balance,
                    cash_flow=cash_flow,
                )
                if previous_computation.status != CALCULATED:
                    return self._growth_base_status(
                        task,
                        phase="previous",
                        base_metric_id=base_metric_id,
                        computation=previous_computation,
                        other_value=current,
                    )
                previous = previous_computation.value

        missing_fields: list[str] = []
        if current is None:
            missing_fields.append(f"current.{base_metric_id}")
        if previous is None:
            missing_fields.append(f"previous.{base_metric_id}")
        if current is None or previous is None:
            return MetricComputation(
                metric_id=task.metric_id,
                status=UNAVAILABLE,
                inputs={"current": current, "previous": previous},
                missing_fields=missing_fields,
                reason="growth_value_missing",
            )
        if previous == 0:
            return MetricComputation(
                metric_id=task.metric_id,
                status=INVALID,
                inputs={"current": current, "previous": previous},
                reason="growth_previous_period_zero",
            )
        if not math.isfinite(current) or not math.isfinite(previous):
            return MetricComputation(
                metric_id=task.metric_id,
                status=INVALID,
                inputs={"current": current, "previous": previous},
                reason="growth_value_not_finite",
            )
        value = (current - previous) / abs(previous) * 100
        return MetricComputation(
            metric_id=task.metric_id,
            status=CALCULATED,
            value=round(value, 6),
            inputs={
                "current": round(current, 6),
                "previous": round(previous, 6),
            },
        )

    @staticmethod
    def _comparison_formula(
        formula: str,
        comparison_type: PeriodComparisonType,
        period_basis: PeriodBasisType = PeriodBasisType.CUMULATIVE,
    ) -> str:
        label = (
            "同比"
            if comparison_type == PeriodComparisonType.YEAR_OVER_YEAR
            else "环比"
        )
        basis = (
            "累计"
            if period_basis == PeriodBasisType.CUMULATIVE
            else "单期"
        )
        return f"{formula}（{label}/{basis}）"

    @staticmethod
    def _growth_base_status(
        task: ExecutionTask,
        *,
        phase: str,
        base_metric_id: str,
        computation: MetricComputation,
        other_value: float | None,
    ) -> MetricComputation:
        inputs = {
            "current": other_value if phase == "previous" else None,
            "previous": other_value if phase == "current" else None,
        }
        if computation.status == UNAVAILABLE:
            return MetricComputation(
                metric_id=task.metric_id,
                status=UNAVAILABLE,
                inputs=inputs,
                missing_fields=[
                    f"{phase}.{base_metric_id}",
                    *computation.missing_fields,
                ],
                reason=(
                    f"{phase}_base_metric_unavailable:"
                    f"{computation.reason or 'base_metric_unavailable'}"
                ),
            )
        return MetricComputation(
            metric_id=task.metric_id,
            status=INVALID,
            inputs=inputs,
            reason=(
                f"{phase}_base_metric_invalid:"
                f"{computation.reason or 'base_metric_invalid'}"
            ),
        )

    @staticmethod
    def _base_metadata(task: ExecutionTask) -> dict[str, Any]:
        return {
            "execution_adapter": TASK_EXECUTION_ADAPTER_NAME,
            "task_id": task.task_id,
        }

    def _data_unavailable(
        self,
        task: ExecutionTask,
        reason: str,
        *,
        calculation_id: str | None = None,
        verification_id: str | None = None,
    ) -> TaskExecutionOutcome:
        metadata = self._base_metadata(task)
        if calculation_id:
            metadata["calculation_id"] = calculation_id
        if verification_id:
            metadata["verification_id"] = verification_id
        return TaskExecutionOutcome(
            status=ExecutionStatus.UNAVAILABLE,
            status_reason=reason,
            error=reason,
            metadata=metadata,
        )

    def _failure(
        self,
        task: ExecutionTask,
        error: str,
        *,
        calculation_id: str | None = None,
        verification_id: str | None = None,
    ) -> TaskExecutionOutcome:
        metadata = self._base_metadata(task)
        if calculation_id:
            metadata["calculation_id"] = calculation_id
        if verification_id:
            metadata["verification_id"] = verification_id
        return TaskExecutionOutcome(
            status=ExecutionStatus.FAILED,
            status_reason=error,
            error=error,
            metadata=metadata,
        )

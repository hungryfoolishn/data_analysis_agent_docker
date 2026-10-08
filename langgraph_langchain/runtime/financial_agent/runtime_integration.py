"""Runtime integration between V10 execution plans and the V9.3 workflow."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from langgraph_langchain.runtime.financial.models import (
    FinancialAnalysisResult,
    FinancialQuery,
)

from .execution_models import (
    ExecutionPlan,
    ExecutionPlanStatus,
    ExecutionStatus,
    RuntimeExecutionResult,
    RuntimeExecutionStatus,
    RuntimeTaskResult,
)
from .models import PlanStatus, VerificationStatus


class RuntimeIntegrationError(ValueError):
    """Raised when an execution plan cannot be run against V9.3."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class FinancialRuntimeExecutor:
    """Execute a validated V10.2 plan through the existing V9.3 workflow."""

    def __init__(self, workflow) -> None:
        self.workflow = workflow

    def execute_plan(
        self,
        plan: ExecutionPlan,
        *,
        understanding,
    ) -> RuntimeExecutionResult:
        if plan.status != ExecutionPlanStatus.PENDING:
            raise RuntimeIntegrationError(
                f"Execution plan must be PENDING before execution, got {plan.status.value}."
            )
        if not plan.tasks:
            raise RuntimeIntegrationError("Execution plan has no tasks.")

        company_names = list(dict.fromkeys(
            task.company_name for task in plan.tasks
        ))
        metric_ids = list(dict.fromkeys(
            task.metric_id for task in plan.tasks
        ))
        years = [int(task.period) for task in plan.tasks]

        query = FinancialQuery(
            question=understanding.raw_question,
            task_type=understanding.task_type,
            company_names=company_names,
            metrics=metric_ids,
            start_year=min(years),
            end_year=max(years),
        )

        plan.status = ExecutionPlanStatus.RUNNING
        plan.started_at = _utc_now()
        analysis_result: FinancialAnalysisResult = self.workflow.run(query)
        analysis_result.query = query

        calculations = self._index(
            analysis_result.calculations,
            lambda item: (item.company_id, item.period, item.metric_id),
        )
        verifications = self._index(
            analysis_result.verifications,
            lambda item: (item.company_id, item.period, item.metric_id),
        )
        observations = self._index(
            analysis_result.observations,
            lambda item: (item.company_id, item.period, item.metric_id),
        )
        evidence = self._index(
            analysis_result.evidence,
            lambda item: (item.company_id, item.period, item.metric_id),
        )

        task_results: list[RuntimeTaskResult] = []
        succeeded_count = 0
        unavailable_count = 0
        failed_count = 0

        for task in plan.tasks:
            key = (task.company_id, task.period, task.metric_id)
            if task.status == ExecutionStatus.UNAVAILABLE:
                task_results.append(RuntimeTaskResult(
                    execution_task_id=task.task_id,
                    company_id=task.company_id,
                    company_name=task.company_name,
                    period=task.period,
                    metric_id=task.metric_id,
                    status=ExecutionStatus.UNAVAILABLE,
                    verification_status=VerificationStatus.NOT_REQUIRED,
                    status_reason=task.status_reason or "data_unavailable",
                    metadata={"source": "semantic_coverage"},
                ))
                unavailable_count += 1
                continue

            calculation = calculations.get(key)
            verification = verifications.get(key)
            observation = observations.get(key)
            evidence_item = evidence.get(key)

            if calculation is None:
                status = ExecutionStatus.NOT_EXECUTABLE
                error = "V9.3 workflow did not return a calculation."
                verification_status = VerificationStatus.NOT_REQUIRED
            elif calculation.status == "unavailable":
                status = ExecutionStatus.UNAVAILABLE
                error = calculation.status_reason or "metric_unavailable"
                verification_status = VerificationStatus.UNAVAILABLE
            elif calculation.status == "invalid":
                status = ExecutionStatus.FAILED
                error = calculation.status_reason or "metric_invalid"
                verification_status = VerificationStatus.FAILED
            elif verification is None:
                status = ExecutionStatus.FAILED
                error = "V9.3 workflow did not return verification."
                verification_status = VerificationStatus.NOT_REQUIRED
            elif not verification.passed:
                status = ExecutionStatus.FAILED
                error = verification.message or "verification_failed"
                verification_status = VerificationStatus.FAILED
            elif observation is None or evidence_item is None:
                status = ExecutionStatus.FAILED
                error = "Verified calculation did not produce observation/evidence."
                verification_status = VerificationStatus.PASSED
            else:
                status = ExecutionStatus.SUCCEEDED
                error = None
                verification_status = VerificationStatus.PASSED

            output_refs: list[str] = []
            if calculation is not None:
                output_refs.append(calculation.calculation_id)
            if verification is not None:
                output_refs.append(verification.verification_id)
            if evidence_item is not None:
                output_refs.append(evidence_item.evidence_id)

            task.status = status
            task.verification_status = verification_status.value
            task.output_refs = output_refs
            task.status_reason = error
            task_results.append(RuntimeTaskResult(
                execution_task_id=task.task_id,
                company_id=task.company_id,
                company_name=task.company_name,
                period=task.period,
                metric_id=task.metric_id,
                status=status,
                verification_status=verification_status,
                calculation_id=(
                    calculation.calculation_id if calculation is not None else None
                ),
                verification_id=(
                    verification.verification_id if verification is not None else None
                ),
                evidence_id=(
                    evidence_item.evidence_id if evidence_item is not None else None
                ),
                status_reason=error,
                error=error,
                output_refs=output_refs,
            ))

            if status == ExecutionStatus.SUCCEEDED:
                succeeded_count += 1
            elif status == ExecutionStatus.UNAVAILABLE:
                unavailable_count += 1
            else:
                failed_count += 1

        if succeeded_count == len(plan.tasks):
            runtime_status = RuntimeExecutionStatus.SUCCEEDED
            plan_status = ExecutionPlanStatus.SUCCEEDED
        elif succeeded_count:
            runtime_status = RuntimeExecutionStatus.PARTIAL
            plan_status = ExecutionPlanStatus.PARTIAL
        else:
            runtime_status = RuntimeExecutionStatus.FAILED
            plan_status = ExecutionPlanStatus.FAILED

        plan.status = plan_status
        plan.finished_at = _utc_now()
        result = RuntimeExecutionResult(
            execution_id=f"runtime_{plan.execution_id}",
            plan_id=plan.plan_id,
            query_id=plan.query_id,
            status=runtime_status,
            task_results=task_results,
            succeeded_count=succeeded_count,
            unavailable_count=unavailable_count,
            failed_count=failed_count,
            analysis_result=analysis_result,
            report_markdown=analysis_result.report_markdown,
            metadata={
                "workflow_task_type": analysis_result.task_type,
                "workflow_metric_count": len(analysis_result.metrics),
                "evidence_count": len(analysis_result.evidence),
                "finding_count": len(analysis_result.findings),
                "comparison_count": len(analysis_result.comparisons),
            },
        )
        result.finished_at = _utc_now()
        return result

    @staticmethod
    def _index(items, key_function) -> dict:
        indexed: dict = defaultdict(list)
        for item in items:
            indexed[key_function(item)].append(item)
        return {key: values[0] for key, values in indexed.items()}

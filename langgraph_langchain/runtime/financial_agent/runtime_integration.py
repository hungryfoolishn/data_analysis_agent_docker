"""Runtime integration between V10 execution plans and the V9.3 workflow.

The batched V9.3 workflow is exposed as a registered executable tool.  A plan
is executed once through the registry; per-task results are then mapped back
without duplicating financial formulas.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from langgraph_langchain.runtime.financial.models import (
    FinancialAnalysisResult,
    FinancialQuery,
)

from .execution_plan import ExecutionPlanBuilder
from .execution_models import (
    ExecutionPlan,
    ExecutionPlanStatus,
    ExecutionStatus,
    RuntimeExecutionResult,
    RuntimeExecutionStatus,
    RuntimeTaskResult,
)
from .models import FinancialToolSpec, VerificationStatus
from .tool_execution import (
    ExecutableFinancialTool,
    FinancialToolExecutionRegistry,
    ToolExecutionResult,
    ToolExecutionStatus,
    UnknownFinancialToolError,
)


WORKFLOW_TOOL_ID = "financial_workflow_runtime"


class RuntimeIntegrationError(ValueError):
    """Raised when an execution plan cannot be run against V9.3."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _failure_task_results(plan: ExecutionPlan, reason: str) -> tuple[list[RuntimeTaskResult], int, int]:
    """Materialize a result for every task during a workflow-level failure.

    Previously unavailable tasks keep their UNAVAILABLE semantics; every other
    task is explicitly marked FAILED instead of silently retaining PENDING.
    """
    task_results: list[RuntimeTaskResult] = []
    unavailable_count = 0
    failed_count = 0

    for task in plan.tasks:
        if task.status == ExecutionStatus.UNAVAILABLE:
            status = ExecutionStatus.UNAVAILABLE
            verification_status = VerificationStatus.NOT_REQUIRED
            status_reason = task.status_reason or "data_unavailable"
            unavailable_count += 1
        else:
            status = ExecutionStatus.FAILED
            verification_status = VerificationStatus.FAILED
            status_reason = reason
            task.status = status
            task.verification_status = verification_status.value
            task.status_reason = status_reason
            failed_count += 1

        task_results.append(RuntimeTaskResult(
            execution_task_id=task.task_id,
            company_id=task.company_id,
            company_name=task.company_name,
            period=task.period,
            metric_id=task.metric_id,
            status=status,
            verification_status=verification_status,
            status_reason=status_reason,
            error=status_reason,
            output_refs=list(task.output_refs),
            metadata={"source": "workflow_failure"},
        ))

    return task_results, unavailable_count, failed_count


def _failed_result(
    plan: ExecutionPlan,
    error: str,
    *,
    started_at: str,
    task_results=None,
    analysis_result=None,
    report_markdown: str = "",
    metadata: dict | None = None,
) -> RuntimeExecutionResult:
    if task_results is None:
        task_results, unavailable_count, failed_count = _failure_task_results(
            plan,
            error,
        )
    else:
        unavailable_count = sum(
            item.status == ExecutionStatus.UNAVAILABLE
            for item in task_results
        )
        failed_count = sum(
            item.status == ExecutionStatus.FAILED
            for item in task_results
        )
    finished_at = _utc_now()
    plan.status = ExecutionPlanStatus.FAILED
    plan.finished_at = finished_at
    return RuntimeExecutionResult(
        execution_id=f"runtime_{plan.execution_id}",
        plan_id=plan.plan_id,
        query_id=plan.query_id,
        status=RuntimeExecutionStatus.FAILED,
        task_results=task_results,
        succeeded_count=0,
        unavailable_count=unavailable_count,
        failed_count=failed_count,
        analysis_result=analysis_result,
        report_markdown=report_markdown,
        started_at=started_at,
        finished_at=finished_at,
        metadata={
            "workflow_tool_id": WORKFLOW_TOOL_ID,
            "error": error,
            **(metadata or {}),
        },
    )


class FinancialRuntimeExecutor:
    """Execute a validated V10.2 plan through the registered V9.3 workflow."""

    def __init__(
        self,
        workflow,
        *,
        tool_registry: FinancialToolExecutionRegistry | None = None,
    ) -> None:
        self.workflow = workflow
        self.tool_registry = tool_registry or self._build_registry()

    def _build_registry(self) -> FinancialToolExecutionRegistry:
        def run_workflow(payload: dict[str, Any]) -> FinancialAnalysisResult:
            query = FinancialQuery.model_validate(payload["query"])
            return self.workflow.run(query)

        spec = FinancialToolSpec(
            tool_id=WORKFLOW_TOOL_ID,
            name="Financial Analysis Workflow Runtime",
            version="v10.2.1",
            input_schema="financial_workflow_runtime_input_v1",
            output_schema="financial_workflow_runtime_output_v1",
            capabilities=["financial_analysis", "verification", "evidence"],
            supported_task_types=[],
            deterministic=True,
            requires_verification=False,
        )
        return FinancialToolExecutionRegistry([
            ExecutableFinancialTool(spec=spec, executor=run_workflow),
        ])

    def execute_plan(
        self,
        plan: ExecutionPlan,
        *,
        understanding,
    ) -> RuntimeExecutionResult:
        if plan.status != ExecutionPlanStatus.PENDING:
            raise RuntimeIntegrationError(
                f"Execution plan must be PENDING before execution, got "
                f"{plan.status.value}."
            )

        validation_errors = self.validate_plan(plan)
        if validation_errors:
            error = "Invalid execution plan: " + "; ".join(validation_errors)
            plan.status = ExecutionPlanStatus.FAILED
            plan.finished_at = _utc_now()
            raise RuntimeIntegrationError(error)

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
        started_at = plan.started_at
        registry_metadata = {
            "plan_id": plan.plan_id,
            "query_id": plan.query_id,
            "task_count": len(plan.tasks),
        }

        try:
            tool_result: ToolExecutionResult = self.tool_registry.execute(
                WORKFLOW_TOOL_ID,
                {"query": query.model_dump(mode="json")},
                metadata=registry_metadata,
            )
        except UnknownFinancialToolError as exc:
            plan.status = ExecutionPlanStatus.FAILED
            plan.finished_at = _utc_now()
            _failure_task_results(plan, str(exc))
            raise RuntimeIntegrationError(str(exc)) from exc

        if tool_result.status != ToolExecutionStatus.SUCCEEDED:
            error = tool_result.error or "workflow tool failed"
            result = _failed_result(
                plan,
                "workflow tool failed",
                started_at=started_at,
                metadata={
                    "workflow_tool_id": WORKFLOW_TOOL_ID,
                    "tool_request_id": tool_result.request_id,
                    "tool_duration_ms": tool_result.duration_ms,
                    "tool_status": tool_result.status.value,
                    "tool_error": tool_result.error,
                },
            )
            return result

        analysis_result = tool_result.output
        if not isinstance(analysis_result, FinancialAnalysisResult):
            plan.status = ExecutionPlanStatus.FAILED
            plan.finished_at = _utc_now()
            raise RuntimeIntegrationError(
                "Registered workflow tool returned an unexpected result type."
            )

        analysis_result.query = query

        try:
            calculations = self._index(
                analysis_result.calculations,
                lambda item: (item.company_id, item.period, item.metric_id),
                item_type="calculation",
            )
            verifications = self._index(
                analysis_result.verifications,
                lambda item: (item.company_id, item.period, item.metric_id),
                item_type="verification",
            )
            observations = self._index(
                analysis_result.observations,
                lambda item: (item.company_id, item.period, item.metric_id),
                item_type="observation",
            )
            evidence = self._index(
                analysis_result.evidence,
                lambda item: (item.company_id, item.period, item.metric_id),
                item_type="evidence",
            )
        except RuntimeIntegrationError as exc:
            error = str(exc)
            plan.status = ExecutionPlanStatus.FAILED
            plan.finished_at = _utc_now()
            return _failed_result(
                plan,
                error,
                started_at=started_at,
                analysis_result=analysis_result,
                report_markdown=analysis_result.report_markdown,
                metadata={
                    "workflow_tool_id": WORKFLOW_TOOL_ID,
                    "tool_request_id": tool_result.request_id,
                },
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

        if failed_count:
            runtime_status = RuntimeExecutionStatus.FAILED
            plan_status = ExecutionPlanStatus.FAILED
        elif succeeded_count == len(plan.tasks):
            runtime_status = RuntimeExecutionStatus.SUCCEEDED
            plan_status = ExecutionPlanStatus.SUCCEEDED
        elif succeeded_count:
            runtime_status = RuntimeExecutionStatus.PARTIAL
            plan_status = ExecutionPlanStatus.PARTIAL
        else:
            runtime_status = RuntimeExecutionStatus.BLOCKED
            plan_status = ExecutionPlanStatus.BLOCKED

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
            started_at=started_at,
            finished_at=plan.finished_at,
            metadata={
                "workflow_tool_id": WORKFLOW_TOOL_ID,
                "tool_request_id": tool_result.request_id,
                "tool_duration_ms": tool_result.duration_ms,
                "workflow_task_type": analysis_result.task_type,
                "workflow_metric_count": len(analysis_result.metrics),
                "evidence_count": len(analysis_result.evidence),
                "finding_count": len(analysis_result.findings),
                "comparison_count": len(analysis_result.comparisons),
            },
        )
        return result

    def validate_plan(self, plan: ExecutionPlan) -> list[str]:
        return ExecutionPlanBuilder.validate(plan)

    @staticmethod
    def _index(items, key_function, *, item_type: str) -> dict:
        indexed: dict = {}
        for item in items:
            key = key_function(item)
            if key in indexed:
                raise RuntimeIntegrationError(
                    f"Duplicate {item_type} result for {key[0]}/{key[1]}/{key[2]}."
                )
            indexed[key] = item
        return indexed

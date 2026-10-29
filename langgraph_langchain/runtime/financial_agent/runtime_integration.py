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
from langgraph_langchain.runtime.financial.period import (
    PeriodBasisType,
    PeriodComparisonType,
    PeriodNormalizationError,
    PeriodNormalizer,
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
from .scheduler import DeterministicDAGScheduler
from .task_executor import (
    TaskLevelFinancialExecutor,
    TaskOutputStore,
)
from .tool_execution import (
    ExecutableFinancialTool,
    FinancialToolExecutionRegistry,
    ToolExecutionResult,
    ToolExecutionStatus,
    UnknownFinancialToolError,
)


WORKFLOW_TOOL_ID = "financial_workflow_runtime"
_PERIOD_NORMALIZER = PeriodNormalizer()


def _years_from_tasks(tasks) -> list[int]:
    """Extract fiscal years for annual, quarterly, and half-year periods."""
    return [
        _PERIOD_NORMALIZER.parse(task.period).year
        for task in tasks
    ]


def _periods_from_tasks(tasks) -> list[str]:
    """Extract exact canonical task periods in stable plan order.

    Unparseable periods (for example TTM) are skipped so the analysis-query
    layer never silently widens them into fiscal years.
    """
    periods: list[str] = []
    for task in tasks:
        try:
            normalized = _PERIOD_NORMALIZER.parse(task.period).normalized_period
        except PeriodNormalizationError:
            continue
        if normalized not in periods:
            periods.append(normalized)
    return periods


def _growth_comparison_type(plan: ExecutionPlan) -> PeriodComparisonType:
    """Resolve the plan-level growth comparison contract."""
    return PeriodComparisonType(
        plan.metadata.get(
            "growth_comparison_type",
            PeriodComparisonType.YEAR_OVER_YEAR,
        )
    )


def _growth_period_basis(plan: ExecutionPlan) -> PeriodBasisType:
    """Resolve the plan-level source statement basis."""
    return PeriodBasisType(
        plan.metadata.get("growth_period_basis", PeriodBasisType.CUMULATIVE)
    )


class RuntimeIntegrationError(ValueError):
    """Raised for caller contract violations before plan execution starts.

    Once a plan is RUNNING, workflow-level failures are represented by a
    structured ``RuntimeExecutionResult`` so task details remain recoverable.
    """


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _failure_task_results(
    plan: ExecutionPlan,
    reason: str,
) -> tuple[list[RuntimeTaskResult], int, int]:
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
    analysis_result=None,
    report_markdown: str = "",
    metadata: dict | None = None,
) -> RuntimeExecutionResult:
    task_results, unavailable_count, failed_count = _failure_task_results(
        plan,
        error,
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
    """Execute a validated plan through task-level DAG or legacy workflow mode.

    V10.3.1 defaults to the deterministic DAG scheduler.  The registered V9.3
    batched workflow remains available with ``execution_mode="workflow"`` for
    compatibility and end-to-end comparison.
    """

    def __init__(
        self,
        workflow,
        *,
        tool_registry: FinancialToolExecutionRegistry | None = None,
        execution_mode: str = "dag",
    ) -> None:
        if execution_mode not in {"dag", "workflow"}:
            raise RuntimeIntegrationError(
                "Execution mode must be 'dag' or 'workflow', got "
                f"{execution_mode!r}."
            )
        self.workflow = workflow
        self.tool_registry = tool_registry or self._build_registry()
        self.execution_mode = execution_mode

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
        if plan.status == ExecutionPlanStatus.BLOCKED:
            # Ambiguous or conflicting plans must surface as a structured
            # BLOCKED result through the whole chain; never fall through to
            # execution or a pseudo-success report.
            return self._blocked_plan_result(plan)

        if self.execution_mode == "workflow":
            return self._execute_plan_with_workflow(
                plan,
                understanding=understanding,
            )

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

        return self._execute_plan_with_scheduler(
            plan,
            understanding=understanding,
        )

    def _blocked_plan_result(self, plan: ExecutionPlan) -> RuntimeExecutionResult:
        """Return a structured BLOCKED result for plans blocked before execution."""
        finished_at = _utc_now()
        plan.finished_at = finished_at
        blocking_diagnostics = list(dict.fromkeys([
            *plan.diagnostics,
            *plan.metadata.get("blocking_diagnostics", []),
        ]))
        return RuntimeExecutionResult(
            execution_id=f"runtime_{plan.execution_id}",
            plan_id=plan.plan_id,
            query_id=plan.query_id,
            status=RuntimeExecutionStatus.BLOCKED,
            task_results=[],
            succeeded_count=0,
            unavailable_count=0,
            failed_count=0,
            blocked_count=0,
            analysis_result=None,
            report_markdown=self._blocked_plan_report(plan, blocking_diagnostics),
            started_at=finished_at,
            finished_at=finished_at,
            metadata={
                "execution_mode": self.execution_mode,
                "blocked_before_execution": True,
                "blocking_diagnostics": blocking_diagnostics,
                "planning_readiness": plan.metadata.get("planning_readiness"),
                "growth_intent_source": plan.metadata.get("growth_intent_source"),
                "growth_intent_diagnostics": list(
                    plan.metadata.get("growth_intent_diagnostics", [])
                ),
                "growth_comparison_type": plan.metadata.get(
                    "growth_comparison_type"
                ),
                "growth_period_basis": plan.metadata.get("growth_period_basis"),
                "growth_intents": list(plan.metadata.get("growth_intents", [])),
            },
        )

    @staticmethod
    def _blocked_plan_report(
        plan: ExecutionPlan,
        blocking_diagnostics: list[str],
    ) -> str:
        """Build a clarification report without any financial conclusion."""
        lines = [
            "# 财务分析已阻断：需要澄清",
            "",
            "本次请求在执行前被阻断，未生成任何财务计算结果或财务结论。",
            "",
            "## 阻断原因",
            "",
        ]
        lines.extend(f"- `{item}`" for item in blocking_diagnostics)
        lines.extend([
            "",
            "## 需要澄清的内容",
            "",
        ])
        if plan.metadata.get("growth_intent_source") in {"conflict", "ambiguous"}:
            lines.extend([
                "- 增长率比较口径存在歧义：请明确使用同比（与上年同期比较）"
                "还是环比（与上一报告期比较）。",
                "- 如需单季度口径，请明确「单季」；如需累计口径，请明确「累计」。",
            ])
        lines.extend([
            "",
            "明确比较口径后重新发起请求，即可继续执行财务分析。",
            "",
        ])
        return "\n".join(lines)

    def _execute_plan_with_scheduler(
        self,
        plan: ExecutionPlan,
        *,
        understanding,
    ) -> RuntimeExecutionResult:
        output_store = TaskOutputStore()
        task_executor = TaskLevelFinancialExecutor(
            self.workflow,
            output_store,
        )
        scheduler = DeterministicDAGScheduler(
            task_executor,
            on_outcome=output_store.record,
        )
        result = scheduler.run(plan)
        analysis_result = self._build_task_analysis_result(
            plan,
            understanding=understanding,
            output_store=output_store,
        )
        result.analysis_result = analysis_result
        result.report_markdown = analysis_result.report_markdown

        metadata = dict(result.metadata)
        metadata.update({
            "execution_mode": "dag",
            "task_execution_adapter": "financial_task_v10_3_1",
            "calculation_count": len(analysis_result.calculations),
            "verification_count": len(analysis_result.verifications),
            "evidence_count": len(analysis_result.evidence),
            "observation_count": len(analysis_result.observations),
            "finding_count": len(analysis_result.findings),
            "comparison_count": len(analysis_result.comparisons),
            "risk_signal_count": len(analysis_result.risk_signals),
            "anomaly_count": len(analysis_result.anomalies),
            "dependency_output_count": sum(
                1
                for task in plan.tasks
                if any(
                    source.startswith("dependency_task:")
                    for source in task.metadata.get("input_sources", {}).values()
                )
            ),
        })
        result.metadata = metadata
        return result

    def _build_task_analysis_result(
        self,
        plan: ExecutionPlan,
        *,
        understanding,
        output_store: TaskOutputStore,
    ):
        outputs = output_store.outputs_in_plan_order(plan.tasks)
        calculations = [item.calculation for item in outputs]
        verifications = [item.verification for item in outputs]
        evidence = [item.evidence for item in outputs]
        observations = [item.observation for item in outputs]
        source_ids = list(dict.fromkeys(
            source_id
            for item in outputs
            for source_id in item.calculation.source_ids
        ))
        years = _years_from_tasks(plan.tasks)
        task_type = (
            understanding.task_type
            or self.workflow.classifier.classify(understanding.raw_question)
        )
        query = FinancialQuery(
            question=understanding.raw_question,
            task_type=task_type,
            company_names=list(dict.fromkeys(
                task.company_name for task in plan.tasks
            )),
            metrics=list(dict.fromkeys(
                task.metric_id for task in plan.tasks
            )),
            periods=_periods_from_tasks(plan.tasks),
            start_year=min(years),
            end_year=max(years),
            growth_comparison_type=_growth_comparison_type(plan),
            growth_period_basis=_growth_period_basis(plan),
        )
        return self.workflow.build_analysis_result(
            query=query,
            task_type=task_type,
            metrics=query.metrics,
            observations=observations,
            calculations=calculations,
            verifications=verifications,
            evidence=evidence,
            source_ids=source_ids,
        )

    def _execute_plan_with_workflow(
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

        if len(plan.metadata.get("growth_intents", [])) > 1:
            plan.status = ExecutionPlanStatus.FAILED
            plan.finished_at = _utc_now()
            return _failed_result(
                plan,
                "Multiple growth comparison intents require DAG execution.",
                started_at=plan.started_at or _utc_now(),
                metadata={
                    "workflow_tool_id": WORKFLOW_TOOL_ID,
                    "growth_intents": plan.metadata.get("growth_intents", []),
                },
            )

        company_names = list(dict.fromkeys(
            task.company_name for task in plan.tasks
        ))
        metric_ids = list(dict.fromkeys(
            task.metric_id for task in plan.tasks
        ))
        years = _years_from_tasks(plan.tasks)
        query = FinancialQuery(
            question=understanding.raw_question,
            task_type=understanding.task_type,
            company_names=company_names,
            metrics=metric_ids,
            periods=_periods_from_tasks(plan.tasks),
            start_year=min(years),
            end_year=max(years),
            growth_comparison_type=_growth_comparison_type(plan),
            growth_period_basis=_growth_period_basis(plan),
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
            return _failed_result(
                plan,
                f"Unknown workflow tool: {WORKFLOW_TOOL_ID}",
                started_at=started_at,
                metadata={
                    "tool_status": "UNKNOWN",
                    "tool_error": str(exc),
                },
            )

        if tool_result.status != ToolExecutionStatus.SUCCEEDED:
            error = tool_result.error or "workflow tool failed"
            result = _failed_result(
                plan,
                error,
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
            return _failed_result(
                plan,
                "Registered workflow tool returned an unexpected result type.",
                started_at=started_at,
                metadata={
                    "tool_request_id": tool_result.request_id,
                    "tool_status": tool_result.status.value,
                },
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
                    metadata={
                        "source": "semantic_coverage",
                        "growth_comparison_type": _growth_comparison_type(plan).value,
                        "growth_period_basis": _growth_period_basis(plan).value,
                    },
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
                metadata={
                    "source": "workflow",
                    "growth_comparison_type": _growth_comparison_type(plan).value,
                    "growth_period_basis": _growth_period_basis(plan).value,
                },
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

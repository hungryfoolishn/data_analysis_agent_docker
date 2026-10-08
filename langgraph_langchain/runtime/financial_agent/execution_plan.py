"""Execution plan construction for Runtime V10.2."""

from __future__ import annotations

from uuid import uuid4

from .execution_models import (
    ExecutionPlan,
    ExecutionPlanStatus,
    ExecutionTask,
)
from .models import ExecutionStatus, FailurePolicy
from .semantic import FinancialSemanticResolver
from .semantic_models import (
    CompanyDataCoverage,
    MetricCoverage,
    MetricDefinitionResolution,
    PeriodCoverage,
    SemanticResolution,
)


class ExecutionPlanBuilder:
    """Expand semantic resolution into company/period/metric execution tasks."""

    def __init__(
        self,
        *,
        semantic_resolver: FinancialSemanticResolver,
        tool_version: str = "v9.3.0",
    ) -> None:
        self.semantic_resolver = semantic_resolver
        self.tool_version = tool_version

    def build(
        self,
        understanding,
        *,
        plan_id: str,
    ) -> ExecutionPlan:
        if not plan_id:
            raise ValueError("plan_id is required to build an execution plan")
        resolution: SemanticResolution = self.semantic_resolver.resolve(understanding)
        fatal_blockers = [
            blocker for blocker in resolution.blockers
            if blocker != "SEMANTIC_DATA_NOT_AVAILABLE"
        ]
        if fatal_blockers:
            raise ValueError(
                "Semantic resolution is not ready: "
                + ", ".join(fatal_blockers)
            )

        metric_by_id = {
            item.metric_id: item
            for item in resolution.metrics.resolved
        }
        company_coverage_by_id = {
            item.company_id: item
            for item in resolution.data_coverage.companies
        }
        period_coverage_by_id = {
            item.company_id: item
            for item in resolution.periods
        }

        tasks: list[ExecutionTask] = []
        for company in sorted(
            resolution.companies.resolved,
            key=lambda item: (item.company_name, item.company_id),
        ):
            company_coverage = company_coverage_by_id[company.company_id]
            period_coverage = period_coverage_by_id[company.company_id]
            all_periods = list(dict.fromkeys([
                *period_coverage.selected_periods,
                *period_coverage.missing_periods,
            ]))
            for period in all_periods:
                for metric in resolution.metrics.resolved:
                    coverage = self._metric_coverage(
                        company_coverage,
                        period,
                        metric.metric_id,
                    )
                    tasks.append(self._make_task(
                        resolution=resolution,
                        plan_id=plan_id,
                        company_id=company.company_id,
                        company_name=company.company_name,
                        period_coverage=period_coverage,
                        metric=metric,
                        coverage=coverage,
                        period=period,
                    ))

        self._assign_ids(tasks)
        task_id_by_key = {
            (task.company_id, task.period, task.metric_id): task.task_id
            for task in tasks
        }
        self._link_dependencies(tasks, task_id_by_key)

        errors = self.validate(ExecutionPlan(
            plan_id=plan_id,
            query_id=understanding.query_id,
            tasks=tasks,
            diagnostics=list(dict.fromkeys(resolution.diagnostics)),
            metadata={
                "semantic_resolution": resolution.model_dump(mode="json"),
                "resolved_company_count": len(resolution.companies.resolved),
                "resolved_metric_count": len(resolution.metrics.resolved),
                "selected_company_period_count": sum(
                    len(item.selected_periods)
                    for item in resolution.periods
                ),
                "missing_company_period_count": sum(
                    len(item.missing_periods)
                    for item in resolution.periods
                ),
                "tool_id": "financial_metric_engine",
                "tool_version": self.tool_version,
            },
        ))
        if errors:
            raise ValueError(
                "Invalid execution plan: " + "; ".join(errors)
            )

        return ExecutionPlan(
            execution_id=f"execution_{uuid4().hex}",
            plan_id=plan_id,
            query_id=understanding.query_id,
            status=ExecutionPlanStatus.PENDING,
            tasks=tasks,
            diagnostics=list(dict.fromkeys(resolution.diagnostics)),
            metadata={
                "semantic_resolution": resolution.model_dump(mode="json"),
                "resolved_company_count": len(resolution.companies.resolved),
                "resolved_metric_count": len(resolution.metrics.resolved),
                "selected_company_period_count": sum(
                    len(item.selected_periods)
                    for item in resolution.periods
                ),
                "missing_company_period_count": sum(
                    len(item.missing_periods)
                    for item in resolution.periods
                ),
                "tool_id": "financial_metric_engine",
                "tool_version": self.tool_version,
            },
        )

    def _make_task(
        self,
        *,
        resolution: SemanticResolution,
        plan_id: str,
        company_id: str,
        company_name: str,
        period_coverage: PeriodCoverage,
        metric: MetricDefinitionResolution,
        coverage: MetricCoverage | None,
        period: str,
    ) -> ExecutionTask:
        executable = coverage is not None and coverage.status == "AVAILABLE"
        previous_period = self._previous_period(period_coverage, period)
        missing_fields = list(coverage.missing_fields) if coverage else []

        return ExecutionTask(
            task_id=f"pending_{uuid4().hex}",
            plan_task_id=None,
            tool_id="financial_metric_engine",
            tool_version=self.tool_version,
            company_id=company_id,
            company_name=company_name,
            period=period,
            metric_id=metric.metric_id,
            metric_name=metric.name,
            dependencies=[],
            required_data=metric.source_fields,
            inputs={
                "company_name": company_name,
                "period": period,
                "metric_id": metric.metric_id,
                "metric_name": metric.name,
                "formula": metric.formula,
                "unit": metric.unit,
                "calculation_type": metric.calculation_type,
                "balance_policy": metric.balance_policy,
                "previous_period": previous_period,
                "source_fields": metric.source_fields,
                "coverage_status": (
                    coverage.status if coverage else "PERIOD_MISSING"
                ),
            },
            status=(
                ExecutionStatus.PENDING
                if executable
                else ExecutionStatus.UNAVAILABLE
            ),
            verification_status="REQUIRED" if executable else "NOT_REQUIRED",
            status_reason=(
                None
                if executable
                else (coverage.status if coverage else "PERIOD_MISSING")
            ),
            missing_fields=missing_fields,
            failure_policy=FailurePolicy.CONTINUE_WITH_WARNING,
            metadata={
                "plan_id": plan_id,
                "metric_id": metric.metric_id,
                "previous_period": previous_period,
                "coverage_status": (
                    coverage.status if coverage else "PERIOD_MISSING"
                ),
                "semantic_diagnostics": list(resolution.diagnostics),
            },
        )

    def _assign_ids(self, tasks: list[ExecutionTask]) -> None:
        for index, task in enumerate(tasks, start=1):
            task.task_id = f"exec_{index:04d}"

    def _link_dependencies(
        self,
        tasks: list[ExecutionTask],
        task_id_by_key: dict[tuple[str, str, str], str],
    ) -> None:
        for task in tasks:
            task.dependencies = []
            metric_id = task.metadata["metric_id"]
            if not metric_id.endswith("_growth"):
                continue
            base_metric_id = metric_id.removesuffix("_growth")
            previous_period = task.metadata.get("previous_period")
            if not previous_period:
                continue
            dependency_id = task_id_by_key.get((
                task.company_id,
                previous_period,
                base_metric_id,
            ))
            if dependency_id:
                task.dependencies.append(dependency_id)

    def _previous_period(
        self,
        period_coverage: PeriodCoverage,
        period: str,
    ) -> str | None:
        parsed = self.semantic_resolver.period_normalizer.parse(period)
        previous = parsed.previous()
        previous_period = previous.normalized_period
        if previous_period in set(period_coverage.available_periods):
            return previous_period
        return None

    @staticmethod
    def _metric_coverage(
        company_coverage: CompanyDataCoverage,
        period: str,
        metric_id: str,
    ) -> MetricCoverage | None:
        return next(
            (
                item for item in company_coverage.metric_coverage
                if item.period == period and item.metric_id == metric_id
            ),
            None,
        )

    @staticmethod
    def validate(plan: ExecutionPlan) -> list[str]:
        errors: list[str] = []
        if not plan.tasks:
            errors.append("Execution plan has no tasks.")

        task_ids = [task.task_id for task in plan.tasks]
        if len(task_ids) != len(set(task_ids)):
            errors.append("Execution plan has duplicate task IDs.")

        keys = [
            (task.company_id, task.period, task.metric_id)
            for task in plan.tasks
        ]
        if len(keys) != len(set(keys)):
            errors.append(
                "Execution plan has duplicate company/period/metric tasks."
            )

        tasks_by_id = {task.task_id: task for task in plan.tasks}
        for task in plan.tasks:
            if task.task_id in task.dependencies:
                errors.append(
                    f"Execution task {task.task_id} self-dependency."
                )
            for dependency in task.dependencies:
                if dependency not in tasks_by_id:
                    errors.append(
                        f"Execution task {task.task_id} unknown dependency "
                        f"{dependency}."
                    )

        errors.extend(ExecutionPlanBuilder._detect_cycles(plan.tasks))
        return list(dict.fromkeys(errors))

    @staticmethod
    def _detect_cycles(tasks: list[ExecutionTask]) -> list[str]:
        graph = {task.task_id: list(task.dependencies) for task in tasks}
        visiting: set[str] = set()
        visited: set[str] = set()
        cycles: list[str] = []

        def visit(task_id: str, path: list[str]) -> None:
            if task_id in visiting:
                start = path.index(task_id) if task_id in path else 0
                cycles.append(
                    "Execution dependency cycle: "
                    + " -> ".join([*path[start:], task_id])
                )
                return
            if task_id in visited:
                return
            visiting.add(task_id)
            for dependency in graph.get(task_id, []):
                visit(dependency, [*path, task_id])
            visiting.remove(task_id)
            visited.add(task_id)

        for task in tasks:
            visit(task.task_id, [])
        return list(dict.fromkeys(cycles))

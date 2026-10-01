"""Financial task planner for Runtime V10.0."""

from __future__ import annotations

import hashlib

from langgraph_langchain.runtime.financial.metrics import financial_metric_registry

from .models import (
    ExecutionStatus,
    FailurePolicy,
    FinancialPlan,
    FinancialPlanTask,
    FinancialPlanTaskType,
    FinancialTaskUnderstanding,
    PlanStatus,
    PlanningReadiness,
    VerificationStatus,
)
from .tools import FINANCIAL_TOOL_REGISTRY, TASK_TOOL_IDS, FinancialToolSpec


_TASK_NAMES = {
    FinancialPlanTaskType.RESOLVE_COMPANIES: "Resolve Companies",
    FinancialPlanTaskType.RESOLVE_PERIODS: "Resolve Periods",
    FinancialPlanTaskType.VALIDATE_DATA_COVERAGE: "Validate Data Coverage",
    FinancialPlanTaskType.REVENUE_TREND: "Revenue Trend",
    FinancialPlanTaskType.PROFIT_TREND: "Net Profit Trend",
    FinancialPlanTaskType.PROFITABILITY_ANALYSIS: "Profitability Analysis",
    FinancialPlanTaskType.CASHFLOW_ANALYSIS: "Cash Flow Analysis",
    FinancialPlanTaskType.SOLVENCY_ANALYSIS: "Solvency Analysis",
    FinancialPlanTaskType.OPERATING_ANALYSIS: "Operating Analysis",
    FinancialPlanTaskType.RISK_DETECTION: "Risk Detection",
    FinancialPlanTaskType.PEER_COMPARISON: "Peer Comparison",
    FinancialPlanTaskType.VERIFY_CALCULATIONS: "Verify Calculations",
    FinancialPlanTaskType.BUILD_FINDINGS: "Build Evidence-backed Findings",
    FinancialPlanTaskType.GENERATE_REPORT: "Generate Report",
}

_TASK_METRICS = {
    FinancialPlanTaskType.REVENUE_TREND: (
        "revenue", "revenue_growth",
    ),
    FinancialPlanTaskType.PROFIT_TREND: (
        "net_profit", "net_profit_growth",
    ),
    FinancialPlanTaskType.PROFITABILITY_ANALYSIS: (
        "gross_margin", "operating_margin", "net_margin", "roe", "roa",
    ),
    FinancialPlanTaskType.CASHFLOW_ANALYSIS: (
        "operating_cash_flow", "free_cash_flow", "ocf_to_net_income",
    ),
    FinancialPlanTaskType.SOLVENCY_ANALYSIS: (
        "debt_to_asset", "current_ratio", "quick_ratio",
    ),
    FinancialPlanTaskType.OPERATING_ANALYSIS: (
        "receivable_turnover", "inventory_turnover", "asset_turnover",
    ),
    FinancialPlanTaskType.RISK_DETECTION: (
        "debt_to_asset", "revenue_growth", "net_profit_growth",
        "ocf_to_net_income",
    ),
    FinancialPlanTaskType.PEER_COMPARISON: (
        "revenue", "revenue_growth", "net_margin",
    ),
}

_OBJECTIVE_TASKS = {
    "revenue_trend": FinancialPlanTaskType.REVENUE_TREND,
    "profit_trend": FinancialPlanTaskType.PROFIT_TREND,
    "profitability": FinancialPlanTaskType.PROFITABILITY_ANALYSIS,
    "cashflow": FinancialPlanTaskType.CASHFLOW_ANALYSIS,
    "solvency": FinancialPlanTaskType.SOLVENCY_ANALYSIS,
    "operating_efficiency": FinancialPlanTaskType.OPERATING_ANALYSIS,
    "risk": FinancialPlanTaskType.RISK_DETECTION,
    "peer_comparison": FinancialPlanTaskType.PEER_COMPARISON,
}

_NUMERIC_TASK_TYPES = {
    FinancialPlanTaskType.REVENUE_TREND,
    FinancialPlanTaskType.PROFIT_TREND,
    FinancialPlanTaskType.PROFITABILITY_ANALYSIS,
    FinancialPlanTaskType.CASHFLOW_ANALYSIS,
    FinancialPlanTaskType.SOLVENCY_ANALYSIS,
    FinancialPlanTaskType.OPERATING_ANALYSIS,
    FinancialPlanTaskType.RISK_DETECTION,
    FinancialPlanTaskType.PEER_COMPARISON,
}


def _plan_id(query_id: str) -> str:
    digest = hashlib.sha256(query_id.encode("utf-8")).hexdigest()[:12]
    return f"plan_{digest}"


class FinancialTaskPlanner:
    """Build and statically validate an executable financial analysis DAG."""

    def __init__(
        self,
        *,
        tool_registry: dict[str, FinancialToolSpec] | None = None,
    ) -> None:
        self.tool_registry = tool_registry or FINANCIAL_TOOL_REGISTRY

    def build(
        self,
        understanding: FinancialTaskUnderstanding,
    ) -> FinancialPlan:
        if understanding.planning_readiness != PlanningReadiness.READY:
            status = (
                PlanStatus.BLOCKED
                if understanding.planning_readiness == PlanningReadiness.NEEDS_CLARIFICATION
                else PlanStatus.FAILED
            )
            return FinancialPlan(
                plan_id=_plan_id(understanding.query_id),
                query_id=understanding.query_id,
                status=status,
                planning_readiness=understanding.planning_readiness,
                planning_diagnostics=list(understanding.planning_diagnostics),
                metadata={
                    "task_type": understanding.task_type,
                    "company_count": len(understanding.companies),
                    "comparison_enabled": understanding.comparison_enabled,
                    "blocked": True,
                    "blocked_reasons": list(understanding.planning_diagnostics),
                },
            )

        tasks: list[FinancialPlanTask] = []
        task_ids: dict[FinancialPlanTaskType, str] = {}

        def add_task(
            task_type: FinancialPlanTaskType,
            *,
            dependencies: list[str],
            metrics: tuple[str, ...] | list[str] = (),
            verification_required: bool = False,
        ) -> str:
            task_id = f"task_{len(tasks) + 1:03d}"
            tool_id = TASK_TOOL_IDS.get(task_type)
            tool = self.tool_registry.get(tool_id) if tool_id else None
            executable = bool(tool_id and tool)

            tasks.append(FinancialPlanTask(
                task_id=task_id,
                task_type=task_type,
                name=_TASK_NAMES[task_type],
                dependencies=list(dict.fromkeys(dependencies)),
                required_data=self._required_data(metrics),
                required_metrics=list(dict.fromkeys(metrics)),
                selected_tool=tool_id if executable else None,
                tool_version=tool.version if tool else None,
                execution_status=(
                    ExecutionStatus.PENDING
                    if executable
                    else ExecutionStatus.NOT_EXECUTABLE
                ),
                verification_status=(
                    VerificationStatus.REQUIRED
                    if verification_required
                    else VerificationStatus.NOT_REQUIRED
                ),
                failure_policy=(
                    FailurePolicy.REPLAN_OR_FAIL
                    if not executable
                    else self._default_failure_policy(task_type)
                ),
            ))
            task_ids[task_type] = task_id
            return task_id

        resolve_companies = add_task(
            FinancialPlanTaskType.RESOLVE_COMPANIES,
            dependencies=[],
        )
        resolve_periods = add_task(
            FinancialPlanTaskType.RESOLVE_PERIODS,
            dependencies=[resolve_companies],
        )

        planned_analysis_types = [
            task_type
            for objective, task_type in _OBJECTIVE_TASKS.items()
            if objective in understanding.objectives
        ]
        analysis_metrics = {
            metric_id
            for task_type in planned_analysis_types
            for metric_id in _TASK_METRICS.get(task_type, ())
        }
        coverage_task = add_task(
            FinancialPlanTaskType.VALIDATE_DATA_COVERAGE,
            dependencies=[resolve_companies, resolve_periods],
            metrics=tuple(dict.fromkeys(analysis_metrics)),
        )

        analysis_tasks: list[str] = []
        for objective, task_type in _OBJECTIVE_TASKS.items():
            if objective not in understanding.objectives:
                continue
            analysis_tasks.append(add_task(
                task_type,
                dependencies=[coverage_task],
                metrics=_TASK_METRICS.get(task_type, ()),
                verification_required=True,
            ))

        verification_task = add_task(
            FinancialPlanTaskType.VERIFY_CALCULATIONS,
            dependencies=analysis_tasks or [coverage_task],
        )
        findings_task = add_task(
            FinancialPlanTaskType.BUILD_FINDINGS,
            dependencies=[verification_task],
        )
        add_task(
            FinancialPlanTaskType.GENERATE_REPORT,
            dependencies=[findings_task],
        )

        plan = FinancialPlan(
            plan_id=_plan_id(understanding.query_id),
            query_id=understanding.query_id,
            tasks=tasks,
            metadata={
                "task_type": understanding.task_type,
                "company_count": len(understanding.companies),
                "comparison_enabled": understanding.comparison_enabled,
            },
        )
        errors = self.validate_plan(plan)
        plan.status = PlanStatus.VALIDATED if not errors else PlanStatus.INVALID
        plan.planning_readiness = (
            PlanningReadiness.READY
            if not errors
            else PlanningReadiness.NOT_EXECUTABLE
        )
        plan.validation_errors = errors
        return plan

    def validate_plan(self, plan: FinancialPlan) -> list[str]:
        errors: list[str] = []
        if not plan.tasks:
            errors.append("Plan has no tasks.")

        task_ids = [task.task_id for task in plan.tasks]
        duplicate_ids = {
            task_id for task_id in task_ids if task_ids.count(task_id) > 1
        }
        if duplicate_ids:
            errors.append(
                f"Duplicate task IDs: {', '.join(sorted(duplicate_ids))}"
            )

        tasks_by_id = {task.task_id: task for task in plan.tasks}
        for task in plan.tasks:
            seen_dependencies: set[str] = set()
            for dependency in task.dependencies:
                if dependency == task.task_id:
                    errors.append(
                        f"Task {task.task_id} has a self-dependency."
                    )
                if dependency in seen_dependencies:
                    errors.append(
                        f"Task {task.task_id} has duplicate dependency "
                        f"{dependency}."
                    )
                seen_dependencies.add(dependency)
                if dependency not in tasks_by_id:
                    errors.append(
                        f"Task {task.task_id} has unknown dependency {dependency}."
                    )

            if not task.selected_tool:
                errors.append(f"Task {task.task_id} has no registered tool.")
                continue

            tool = self.tool_registry.get(task.selected_tool)
            if tool is None:
                errors.append(
                    f"Task {task.task_id} selected unregistered tool "
                    f"{task.selected_tool}."
                )
                continue
            if task.task_type not in tool.supported_task_types:
                errors.append(
                    f"Tool {tool.tool_id} does not support task type "
                    f"{task.task_type.value}."
                )

            for metric_id in task.required_metrics:
                if financial_metric_registry.get_optional(metric_id) is None:
                    errors.append(
                        f"Task {task.task_id} requires unregistered metric "
                        f"{metric_id}."
                    )

            if (
                task.task_type in _NUMERIC_TASK_TYPES
                and task.verification_status != VerificationStatus.REQUIRED
            ):
                errors.append(
                    f"Task {task.task_id} must require verification."
                )

        verification_tasks = [
            task for task in plan.tasks
            if task.task_type == FinancialPlanTaskType.VERIFY_CALCULATIONS
        ]
        if len(verification_tasks) != 1:
            errors.append("Plan must contain exactly one verification task.")

        findings_tasks = [
            task for task in plan.tasks
            if task.task_type == FinancialPlanTaskType.BUILD_FINDINGS
        ]
        if len(findings_tasks) != 1:
            errors.append("Plan must contain exactly one findings task.")
        else:
            findings = findings_tasks[0]
            verification_ids = {
                task.task_id for task in verification_tasks
            }
            if (
                len(findings.dependencies) != 1
                or findings.dependencies[0] not in verification_ids
            ):
                errors.append(
                    "Findings task must depend exactly on verification."
                )

        report_tasks = [
            task for task in plan.tasks
            if task.task_type == FinancialPlanTaskType.GENERATE_REPORT
        ]
        if len(report_tasks) != 1:
            errors.append("Plan must contain exactly one report task.")
        else:
            report = report_tasks[0]
            findings_ids = {
                task.task_id for task in findings_tasks
            }
            if (
                len(report.dependencies) != 1
                or report.dependencies[0] not in findings_ids
            ):
                errors.append("Report task must depend exactly on findings.")

        numeric_task_ids = {
            task.task_id for task in plan.tasks
            if task.task_type in _NUMERIC_TASK_TYPES
        }
        if not numeric_task_ids:
            errors.append("Plan has no analysis tasks.")
        elif len(verification_tasks) == 1:
            verification = verification_tasks[0]
            covered_ids = set(verification.dependencies)
            uncovered_ids = sorted(numeric_task_ids - covered_ids)
            if uncovered_ids:
                errors.append(
                    "Verification task does not cover numeric tasks: "
                    + ", ".join(uncovered_ids)
                )

        if report_tasks:
            reachable_from_report = self._ancestors(
                report_tasks[0].task_id,
                plan.tasks,
            )
            unreachable_ids = sorted(set(task_ids) - reachable_from_report)
            for unreachable_id in unreachable_ids:
                errors.append(
                    f"Task {unreachable_id} is not reachable from report."
                )

        errors.extend(self._detect_cycles(plan.tasks))
        return list(dict.fromkeys(errors))

    @staticmethod
    def _required_data(metrics: tuple[str, ...] | list[str]) -> list[str]:
        required_data: set[str] = set()
        for metric_id in metrics:
            definition = financial_metric_registry.get_optional(metric_id)
            if definition:
                required_data.update(definition.source_fields)
        return sorted(required_data)

    @staticmethod
    def _default_failure_policy(
        task_type: FinancialPlanTaskType,
    ) -> FailurePolicy:
        if task_type in {
            FinancialPlanTaskType.RESOLVE_COMPANIES,
            FinancialPlanTaskType.RESOLVE_PERIODS,
            FinancialPlanTaskType.VALIDATE_DATA_COVERAGE,
            FinancialPlanTaskType.VERIFY_CALCULATIONS,
            FinancialPlanTaskType.BUILD_FINDINGS,
            FinancialPlanTaskType.GENERATE_REPORT,
        }:
            return FailurePolicy.FAIL_FAST
        return FailurePolicy.CONTINUE_WITH_WARNING

    @staticmethod
    def _ancestors(report_task_id: str, tasks: list[FinancialPlanTask]) -> set[str]:
        dependencies = {
            task.task_id: list(task.dependencies)
            for task in tasks
        }
        visited: set[str] = set()
        stack = [report_task_id]
        while stack:
            task_id = stack.pop()
            if task_id in visited:
                continue
            visited.add(task_id)
            stack.extend(dependencies.get(task_id, []))
        return visited

    @staticmethod
    def _detect_cycles(tasks: list[FinancialPlanTask]) -> list[str]:
        graph = {task.task_id: list(task.dependencies) for task in tasks}
        visiting: set[str] = set()
        visited: set[str] = set()
        cycles: list[str] = []

        def visit(task_id: str, path: list[str]) -> None:
            if task_id in visiting:
                cycle_start = path.index(task_id) if task_id in path else 0
                cycles.append(
                    "Dependency cycle detected: "
                    + " -> ".join([*path[cycle_start:], task_id])
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

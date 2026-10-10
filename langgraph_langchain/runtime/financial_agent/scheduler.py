"""Deterministic dependency scheduling for Runtime V10.3.0.

The scheduler deliberately owns only graph state and task dispatch.  It does
not implement financial formulas, verification, evidence generation, retries,
persistence, or concurrency.  Those responsibilities remain with task-level
executors and later runtime phases.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Callable

from pydantic import BaseModel, Field

from .execution_models import (
    ExecutionPlan,
    ExecutionPlanStatus,
    ExecutionTask,
    RuntimeExecutionResult,
    RuntimeExecutionStatus,
    RuntimeTaskResult,
)
from .execution_plan import ExecutionPlanBuilder
from .models import ExecutionStatus, FailurePolicy, VerificationStatus


SCHEDULER_NAME = "deterministic_dag_v10_3_0"
_ALLOWED_OUTCOME_STATUSES = {
    ExecutionStatus.SUCCEEDED,
    ExecutionStatus.FAILED,
    ExecutionStatus.UNAVAILABLE,
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SchedulerValidationError(ValueError):
    """Raised when an execution plan cannot enter the DAG scheduler."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = list(errors)
        detail = "; ".join(self.errors)
        super().__init__(f"Invalid execution plan for DAG scheduling: {detail}")


class TaskExecutionOutcome(BaseModel):
    """Terminal outcome returned by a task executor."""

    status: ExecutionStatus = ExecutionStatus.SUCCEEDED
    status_reason: str | None = None
    error: str | None = None
    output_refs: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


TaskExecutor = Callable[[ExecutionTask], TaskExecutionOutcome]


class DeterministicDAGScheduler:
    """Execute an ``ExecutionPlan`` one ready task at a time.

    V10.3.0 is intentionally single-process and sequential.  Ready tasks are
    selected in plan order, which makes execution deterministic and keeps the
    dependency semantics easy to audit before concurrency or persistence are
    introduced.
    """

    def __init__(self, executor: TaskExecutor) -> None:
        if not callable(executor):
            raise ValueError("DAG task executor must be callable.")
        self._executor = executor

    def run(self, plan: ExecutionPlan) -> RuntimeExecutionResult:
        """Schedule ``plan`` and return one result for every task."""
        self._validate(plan)

        started_at = _utc_now()
        plan.status = ExecutionPlanStatus.RUNNING
        plan.started_at = started_at

        try:
            return self._schedule(plan, started_at=started_at)
        except Exception as exc:  # noqa: BLE001 - scheduler failure boundary
            return self._internal_failure_result(
                plan,
                started_at=started_at,
                exc=exc,
            )

    def _schedule(
        self,
        plan: ExecutionPlan,
        *,
        started_at: str,
    ) -> RuntimeExecutionResult:
        tasks_by_id = {task.task_id: task for task in plan.tasks}
        order_by_id = {
            task.task_id: index for index, task in enumerate(plan.tasks)
        }
        downstream: dict[str, list[str]] = defaultdict(list)
        for task in plan.tasks:
            for dependency_id in dict.fromkeys(task.dependencies):
                downstream[dependency_id].append(task.task_id)

        execution_order: list[str] = []
        blocked_task_ids: set[str] = set()
        pending_task_ids = {
            task.task_id
            for task in plan.tasks
            if task.status == ExecutionStatus.PENDING
        }
        failure_policy_stop: dict[str, str] | None = None
        unsupported_failure_policies: list[str] = []

        # Initial UNAVAILABLE tasks are already terminal and must propagate
        # dependency blocking before any ready task is selected.
        for task in plan.tasks:
            if task.status != ExecutionStatus.UNAVAILABLE:
                continue
            self._block_dependents(
                task_id=task.task_id,
                reason=f"dependency {task.task_id} unavailable",
                tasks_by_id=tasks_by_id,
                downstream=downstream,
                order_by_id=order_by_id,
                blocked_task_ids=blocked_task_ids,
                pending_task_ids=pending_task_ids,
            )
            pending_task_ids.discard(task.task_id)

        while pending_task_ids:
            ready_task_ids = [
                task_id
                for task_id in sorted(pending_task_ids, key=order_by_id.__getitem__)
                if self._dependencies_succeeded(
                    tasks_by_id[task_id],
                    tasks_by_id,
                )
            ]
            if not ready_task_ids:
                # A validated acyclic graph should not reach this state.  Keep
                # the failure structured rather than leaving PENDING tasks.
                reason = "no ready execution task is available"
                for task_id in sorted(pending_task_ids, key=order_by_id.__getitem__):
                    self._mark_blocked(
                        tasks_by_id[task_id],
                        reason=reason,
                        blocked_task_ids=blocked_task_ids,
                    )
                break

            for task_id in ready_task_ids:
                pending_task_ids.discard(task_id)
                task = tasks_by_id[task_id]
                task.status = ExecutionStatus.READY
                task.status = ExecutionStatus.RUNNING

                outcome = self._execute_task(task)
                self._apply_outcome(task, outcome)
                execution_order.append(task.task_id)

                if task.status != ExecutionStatus.SUCCEEDED:
                    self._block_dependents(
                        task_id=task.task_id,
                        reason=f"dependency {task.task_id} {task.status.value.lower()}",
                        tasks_by_id=tasks_by_id,
                        downstream=downstream,
                        order_by_id=order_by_id,
                        blocked_task_ids=blocked_task_ids,
                        pending_task_ids=pending_task_ids,
                    )

                if task.status != ExecutionStatus.FAILED:
                    continue

                failure_policy_stop = self._stop_for_failure_policy(
                    task=task,
                    tasks_by_id=tasks_by_id,
                    order_by_id=order_by_id,
                    blocked_task_ids=blocked_task_ids,
                    pending_task_ids=pending_task_ids,
                )
                if failure_policy_stop is not None:
                    if (
                        task.failure_policy
                        == FailurePolicy.REPLAN_OR_FAIL
                    ):
                        unsupported_failure_policies.append(task.task_id)
                    break

            if failure_policy_stop is not None:
                break

        finished_at = _utc_now()
        plan.finished_at = finished_at
        task_results = [
            self._task_result(task, execution_index=index)
            for index, task in enumerate(plan.tasks)
        ]
        succeeded_count = sum(
            item.status == ExecutionStatus.SUCCEEDED for item in task_results
        )
        unavailable_count = sum(
            item.status == ExecutionStatus.UNAVAILABLE for item in task_results
        )
        failed_count = sum(
            item.status == ExecutionStatus.FAILED for item in task_results
        )
        blocked_count = sum(
            item.status == ExecutionStatus.BLOCKED for item in task_results
        )
        runtime_status = self._runtime_status(
            succeeded_count=succeeded_count,
            unavailable_count=unavailable_count,
            failed_count=failed_count,
            blocked_count=blocked_count,
            task_count=len(plan.tasks),
        )
        plan.status = self._plan_status(runtime_status)

        metadata: dict[str, Any] = {
            "scheduler": SCHEDULER_NAME,
            "execution_order": execution_order,
            "blocked_task_ids": [
                task_id
                for task_id in sorted(blocked_task_ids, key=order_by_id.__getitem__)
            ],
        }
        if failure_policy_stop is not None:
            metadata["failure_policy_stop"] = failure_policy_stop
        if unsupported_failure_policies:
            metadata["unsupported_failure_policies"] = unsupported_failure_policies

        return RuntimeExecutionResult(
            execution_id=f"runtime_{plan.execution_id}",
            plan_id=plan.plan_id,
            query_id=plan.query_id,
            status=runtime_status,
            task_results=task_results,
            succeeded_count=succeeded_count,
            unavailable_count=unavailable_count,
            failed_count=failed_count,
            blocked_count=blocked_count,
            started_at=started_at,
            finished_at=finished_at,
            metadata=metadata,
        )

    def _stop_for_failure_policy(
        self,
        *,
        task: ExecutionTask,
        tasks_by_id: dict[str, ExecutionTask],
        order_by_id: dict[str, int],
        blocked_task_ids: set[str],
        pending_task_ids: set[str],
    ) -> dict[str, str] | None:
        if task.failure_policy == FailurePolicy.FAIL_FAST:
            reason = f"fail-fast: task {task.task_id} failed"
        elif task.failure_policy == FailurePolicy.REPLAN_OR_FAIL:
            original_reason = task.status_reason or "task failed"
            task.status_reason = (
                "REPLAN_OR_FAIL is not supported by deterministic DAG scheduler: "
                f"{original_reason}"
            )
            task.metadata["failure_policy"] = task.failure_policy.value
            task.metadata["replan_supported"] = False
            reason = (
                "REPLAN_OR_FAIL is not supported by deterministic DAG scheduler"
            )
        else:
            return None

        self._block_remaining_pending(
            reason=reason,
            tasks_by_id=tasks_by_id,
            order_by_id=order_by_id,
            blocked_task_ids=blocked_task_ids,
            pending_task_ids=pending_task_ids,
        )
        return {
            "task_id": task.task_id,
            "policy": task.failure_policy.value,
            "reason": reason,
        }

    def _block_remaining_pending(
        self,
        *,
        reason: str,
        tasks_by_id: dict[str, ExecutionTask],
        order_by_id: dict[str, int],
        blocked_task_ids: set[str],
        pending_task_ids: set[str],
    ) -> None:
        for task_id in sorted(pending_task_ids, key=order_by_id.__getitem__):
            task = tasks_by_id[task_id]
            if task.status not in {
                ExecutionStatus.PENDING,
                ExecutionStatus.READY,
            }:
                continue
            self._mark_blocked(
                task,
                reason=reason,
                blocked_task_ids=blocked_task_ids,
            )
        pending_task_ids.clear()

    def _internal_failure_result(
        self,
        plan: ExecutionPlan,
        *,
        started_at: str,
        exc: Exception,
    ) -> RuntimeExecutionResult:
        error = f"{type(exc).__name__}: {exc}"
        for task in plan.tasks:
            if task.status == ExecutionStatus.RUNNING:
                task.status = ExecutionStatus.FAILED
                task.status_reason = error
                task.verification_status = VerificationStatus.FAILED.value
            elif task.status in {
                ExecutionStatus.PENDING,
                ExecutionStatus.READY,
            }:
                task.status = ExecutionStatus.BLOCKED
                task.status_reason = f"scheduler internal error: {error}"
                task.verification_status = VerificationStatus.NOT_REQUIRED.value

        finished_at = _utc_now()
        plan.finished_at = finished_at
        plan.status = ExecutionPlanStatus.FAILED
        task_results = [
            self._task_result(task, execution_index=index)
            for index, task in enumerate(plan.tasks)
        ]
        succeeded_count = sum(
            item.status == ExecutionStatus.SUCCEEDED for item in task_results
        )
        unavailable_count = sum(
            item.status == ExecutionStatus.UNAVAILABLE for item in task_results
        )
        failed_count = sum(
            item.status == ExecutionStatus.FAILED for item in task_results
        )
        blocked_count = sum(
            item.status == ExecutionStatus.BLOCKED for item in task_results
        )

        return RuntimeExecutionResult(
            execution_id=f"runtime_{plan.execution_id}",
            plan_id=plan.plan_id,
            query_id=plan.query_id,
            status=RuntimeExecutionStatus.FAILED,
            task_results=task_results,
            succeeded_count=succeeded_count,
            unavailable_count=unavailable_count,
            failed_count=failed_count,
            blocked_count=blocked_count,
            started_at=started_at,
            finished_at=finished_at,
            metadata={
                "scheduler": SCHEDULER_NAME,
                "error": error,
                "scheduler_internal_error": True,
            },
        )

    def _validate(self, plan: ExecutionPlan) -> None:
        errors: list[str] = []
        if plan.status != ExecutionPlanStatus.PENDING:
            errors.append(
                "Execution plan must be PENDING before DAG scheduling, got "
                f"{plan.status.value}."
            )
        errors.extend(ExecutionPlanBuilder.validate(plan))
        for task in plan.tasks:
            if task.status not in {
                ExecutionStatus.PENDING,
                ExecutionStatus.UNAVAILABLE,
            }:
                errors.append(
                    f"Execution task {task.task_id} has non-startable status "
                    f"{task.status.value}."
                )
        if errors:
            raise SchedulerValidationError(errors)

    def _execute_task(self, task: ExecutionTask) -> TaskExecutionOutcome:
        try:
            outcome = self._executor(task)
        except Exception as exc:  # noqa: BLE001 - task execution boundary
            error = f"{type(exc).__name__}: {exc}"
            return TaskExecutionOutcome(
                status=ExecutionStatus.FAILED,
                status_reason=error,
                error=error,
            )

        if not isinstance(outcome, TaskExecutionOutcome):
            error = (
                "Invalid task executor return type: expected TaskExecutionOutcome, "
                f"got {type(outcome).__name__}"
            )
            return TaskExecutionOutcome(
                status=ExecutionStatus.FAILED,
                status_reason=error,
                error=error,
            )
        return outcome

    def _apply_outcome(
        self,
        task: ExecutionTask,
        outcome: TaskExecutionOutcome,
    ) -> None:
        if outcome.status not in _ALLOWED_OUTCOME_STATUSES:
            error = f"Invalid task execution status: {outcome.status.value}"
            outcome = TaskExecutionOutcome(
                status=ExecutionStatus.FAILED,
                status_reason=error,
                error=error,
                metadata=outcome.metadata,
            )

        task.status = outcome.status
        task.status_reason = (
            outcome.status_reason
            or outcome.error
            or self._default_reason(outcome.status)
        )
        task.output_refs = list(outcome.output_refs)
        task.metadata.update(outcome.metadata)
        task.verification_status = self._verification_status(
            outcome.status
        ).value

    def _dependencies_succeeded(
        self,
        task: ExecutionTask,
        tasks_by_id: dict[str, ExecutionTask],
    ) -> bool:
        return all(
            tasks_by_id[dependency_id].status == ExecutionStatus.SUCCEEDED
            for dependency_id in task.dependencies
        )

    def _block_dependents(
        self,
        *,
        task_id: str,
        reason: str,
        tasks_by_id: dict[str, ExecutionTask],
        downstream: dict[str, list[str]],
        order_by_id: dict[str, int],
        blocked_task_ids: set[str],
        pending_task_ids: set[str],
    ) -> None:
        for dependent_id in sorted(
            downstream.get(task_id, []),
            key=order_by_id.__getitem__,
        ):
            dependent = tasks_by_id[dependent_id]
            if dependent.status not in {
                ExecutionStatus.PENDING,
                ExecutionStatus.READY,
            }:
                continue
            self._mark_blocked(
                dependent,
                reason=reason,
                blocked_task_ids=blocked_task_ids,
            )
            pending_task_ids.discard(dependent_id)
            self._block_dependents(
                task_id=dependent_id,
                reason=reason,
                tasks_by_id=tasks_by_id,
                downstream=downstream,
                order_by_id=order_by_id,
                blocked_task_ids=blocked_task_ids,
                pending_task_ids=pending_task_ids,
            )

    def _mark_blocked(
        self,
        task: ExecutionTask,
        *,
        reason: str,
        blocked_task_ids: set[str],
    ) -> None:
        task.status = ExecutionStatus.BLOCKED
        task.status_reason = reason
        task.output_refs = []
        task.verification_status = VerificationStatus.NOT_REQUIRED.value
        blocked_task_ids.add(task.task_id)

    def _task_result(
        self,
        task: ExecutionTask,
        *,
        execution_index: int,
    ) -> RuntimeTaskResult:
        return RuntimeTaskResult(
            execution_task_id=task.task_id,
            company_id=task.company_id,
            company_name=task.company_name,
            period=task.period,
            metric_id=task.metric_id,
            status=task.status,
            verification_status=self._verification_status(task.status),
            status_reason=task.status_reason,
            error=(
                None
                if task.status == ExecutionStatus.SUCCEEDED
                else task.status_reason
            ),
            output_refs=list(task.output_refs),
            metadata={
                "scheduler": SCHEDULER_NAME,
                "execution_index": execution_index,
            },
        )

    @staticmethod
    def _default_reason(status: ExecutionStatus) -> str:
        if status == ExecutionStatus.SUCCEEDED:
            return "task succeeded"
        if status == ExecutionStatus.UNAVAILABLE:
            return "task unavailable"
        return "task failed"

    @staticmethod
    def _verification_status(status: ExecutionStatus) -> VerificationStatus:
        if status == ExecutionStatus.SUCCEEDED:
            return VerificationStatus.PASSED
        if status == ExecutionStatus.FAILED:
            return VerificationStatus.FAILED
        return VerificationStatus.NOT_REQUIRED

    @staticmethod
    def _runtime_status(
        *,
        succeeded_count: int,
        unavailable_count: int,
        failed_count: int,
        blocked_count: int,
        task_count: int,
    ) -> RuntimeExecutionStatus:
        if failed_count:
            return RuntimeExecutionStatus.FAILED
        if succeeded_count == task_count:
            return RuntimeExecutionStatus.SUCCEEDED
        if succeeded_count:
            return RuntimeExecutionStatus.PARTIAL
        if unavailable_count or blocked_count:
            return RuntimeExecutionStatus.BLOCKED
        return RuntimeExecutionStatus.BLOCKED

    @staticmethod
    def _plan_status(
        runtime_status: RuntimeExecutionStatus,
    ) -> ExecutionPlanStatus:
        if runtime_status == RuntimeExecutionStatus.SUCCEEDED:
            return ExecutionPlanStatus.SUCCEEDED
        if runtime_status == RuntimeExecutionStatus.PARTIAL:
            return ExecutionPlanStatus.PARTIAL
        if runtime_status == RuntimeExecutionStatus.FAILED:
            return ExecutionPlanStatus.FAILED
        return ExecutionPlanStatus.BLOCKED

"""Deterministic task scheduling for Runtime V2.

The scheduler is intentionally independent from LangGraph and the existing
``AnalysisRuntime``.  The first integration commit will consume this class from
the graph layer; keeping it isolated makes the dependency rules testable before
any execution behavior changes.
"""

from __future__ import annotations

from collections import defaultdict
from heapq import heappop, heappush
from typing import Iterable, Optional

from langgraph_langchain.runtime.models import RuntimePlanStep
from langgraph_langchain.runtime.plans import AnalysisPlan
from langgraph_langchain.schemas import AnalysisTask, ExecutorType, TaskType


class SchedulerValidationError(ValueError):
    """Raised when tasks cannot form a valid acyclic graph."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = list(errors)
        detail = "; ".join(self.errors)
        super().__init__(f"Invalid task graph: {detail}")


def _infer_task_type(method: str) -> TaskType:
    value = method.lower()
    if value == "load_data":
        return "schema"
    if value == "eda_profile":
        return "profile"
    if any(token in value for token in ("trend", "time_trend")):
        return "trend"
    if any(token in value for token in ("compare", "comparison", "period")):
        return "comparison"
    if any(token in value for token in ("breakdown", "segment")):
        return "breakdown"
    if "contribution" in value or "decompose" in value:
        return "contribution"
    if "anomaly" in value or "outlier" in value:
        return "anomaly"
    if "correlation" in value:
        return "correlation"
    if "statistical" in value or "_test" in value:
        return "statistical_test"
    if "root_cause" in value or "rootcause" in value:
        return "root_cause"
    if any(token in value for token in ("chart", "plot", "visual")):
        return "visualization"
    if "finding" in value or "report" in value:
        return "report"
    return "metric"


def _infer_executor_type(method: str) -> ExecutorType:
    value = method.lower()
    if value == "python_repl":
        return "python"
    if value.startswith("react_"):
        return "react"
    return "structured"


def _runtime_status_to_task_status(status: str) -> str:
    if status == "succeeded":
        return "succeeded"
    if status == "failed":
        return "failed"
    if status == "skipped":
        return "skipped"
    return "pending"


def analysis_tasks_from_plan(
    plan: AnalysisPlan,
    *,
    session_id: str,
    argument_defaults_by_method: Optional[dict[str, dict[str, Any]]] = None,
) -> list[AnalysisTask]:
    """Convert a validated legacy RuntimePlanStep plan into V2 tasks.

    ``argument_defaults_by_method`` is used for deterministic structural
    arguments that are known before execution, such as the session's source
    path for ``load_data``.  LLM-decided arguments are not supplied here.
    """
    defaults = argument_defaults_by_method or {}
    tasks: list[AnalysisTask] = []
    for step in plan.steps:
        constraints = {
            "plan_id": plan.plan_id,
            "plan_version": plan.version,
            "strict_execution": plan.strict_execution,
        }
        method_defaults = defaults.get(step.method)
        if method_defaults:
            constraints["arguments"] = dict(method_defaults)
        tasks.append(
            AnalysisTask(
                task_id=step.step_id,
                session_id=session_id,
                question=step.objective,
                task_type=_infer_task_type(step.method),
                executor_type=_infer_executor_type(step.method),
                method=step.method,
                plan_step_id=step.step_id,
                status=_runtime_status_to_task_status(step.status),
                depends_on=step.depends_on,
                required_inputs=step.required_inputs,
                expected_outputs=step.expected_outputs,
                input_asset_ids=plan.input_asset_ids,
                constraints=constraints,
            )
        )
    return tasks


class TaskScheduler:
    """Schedule schema-level AnalysisTask objects by their dependencies.

    The scheduler is stateful but deliberately small.  It does not execute
    tools, does not call an LLM, and does not create artifacts.  Those remain
    executor responsibilities.
    """

    def __init__(
        self,
        tasks: Iterable[AnalysisTask],
        *,
        max_running_tasks: int = 1,
    ) -> None:
        if max_running_tasks < 1:
            raise ValueError("max_running_tasks must be at least 1")

        self._tasks: dict[str, AnalysisTask] = {}
        self._order: dict[str, int] = {}
        self._errors: dict[str, str] = {}
        self._max_running_tasks = max_running_tasks
        validation_errors: list[str] = []

        for index, candidate in enumerate(tasks):
            task = candidate.model_copy(deep=True)
            if task.task_id in self._tasks:
                validation_errors.append(f"Duplicate task id: {task.task_id}")
                continue
            self._tasks[task.task_id] = task
            self._order[task.task_id] = index

        for task in self._tasks.values():
            if task.task_id in task.depends_on:
                validation_errors.append(
                    f"Task '{task.task_id}' cannot depend on itself"
                )
                continue
            for dependency_id in task.depends_on:
                if dependency_id not in self._tasks:
                    validation_errors.append(
                        f"Task '{task.task_id}' has unknown dependency: {dependency_id}"
                    )

        validation_errors.extend(self._find_cycles())
        if validation_errors:
            raise SchedulerValidationError(validation_errors)

    @classmethod
    def from_plan(
        cls,
        plan: AnalysisPlan,
        *,
        session_id: str,
        max_running_tasks: int = 1,
        argument_defaults_by_method: Optional[dict[str, dict[str, Any]]] = None,
    ) -> "TaskScheduler":
        return cls(
            analysis_tasks_from_plan(
                plan,
                session_id=session_id,
                argument_defaults_by_method=argument_defaults_by_method,
            ),
            max_running_tasks=max_running_tasks,
        )

    def _find_cycles(self) -> list[str]:
        dependencies: dict[str, set[str]] = {
            task_id: set(task.depends_on) for task_id, task in self._tasks.items()
        }
        dependents: dict[str, list[str]] = defaultdict(list)
        indegree: dict[str, int] = {}
        ready: list[tuple[int, str]] = []

        for task_id, deps in dependencies.items():
            indegree[task_id] = len(deps)
            for dependency_id in deps:
                dependents[dependency_id].append(task_id)
            if not deps:
                heappush(ready, (self._order[task_id], task_id))

        visited: set[str] = set()
        while ready:
            _, task_id = heappop(ready)
            visited.add(task_id)
            for dependent_id in dependents[task_id]:
                indegree[dependent_id] -= 1
                if indegree[dependent_id] == 0:
                    heappush(ready, (self._order[dependent_id], dependent_id))

        if len(visited) == len(self._tasks):
            return []

        cyclic = sorted(
            (self._order[task_id], task_id)
            for task_id in self._tasks
            if task_id not in visited
        )
        cycle_ids = ", ".join(task_id for _, task_id in cyclic)
        return [f"Cyclic dependencies detected among tasks: {cycle_ids}"]

    @property
    def tasks(self) -> tuple[AnalysisTask, ...]:
        return tuple(
            self._tasks[task_id]
            for task_id in sorted(self._order, key=self._order.get)  # type: ignore[arg-type]
        )

    @property
    def max_running_tasks(self) -> int:
        return self._max_running_tasks

    def get_task(self, task_id: str) -> AnalysisTask:
        try:
            return self._tasks[task_id]
        except KeyError as exc:
            raise KeyError(f"Unknown task id: {task_id}") from exc

    def execution_queue(self) -> tuple[str, ...]:
        """Return all task ids in a deterministic topological order."""
        dependencies: dict[str, set[str]] = {
            task_id: set(task.depends_on) for task_id, task in self._tasks.items()
        }
        dependents: dict[str, list[str]] = defaultdict(list)
        indegree: dict[str, int] = {}
        ready: list[tuple[int, str]] = []

        for task_id, deps in dependencies.items():
            indegree[task_id] = len(deps)
            for dependency_id in deps:
                dependents[dependency_id].append(task_id)
            if not deps:
                heappush(ready, (self._order[task_id], task_id))

        ordered: list[str] = []
        while ready:
            _, task_id = heappop(ready)
            ordered.append(task_id)
            for dependent_id in dependents[task_id]:
                indegree[dependent_id] -= 1
                if indegree[dependent_id] == 0:
                    heappush(ready, (self._order[dependent_id], dependent_id))
        return tuple(ordered)

    def _dependency_statuses(self, task: AnalysisTask) -> list[str]:
        return [self._tasks[dependency_id].status for dependency_id in task.depends_on]

    def is_ready(self, task_id: str) -> bool:
        task = self.get_task(task_id)
        return task.status == "pending" and all(
            dependency_id_status == "succeeded"
            for dependency_id_status in self._dependency_statuses(task)
        )

    def _queue_rank(self) -> dict[str, int]:
        """Return task-id positions in deterministic topological order."""
        return {
            task_id: index
            for index, task_id in enumerate(self.execution_queue())
        }

    def ready_tasks(self) -> tuple[AnalysisTask, ...]:
        ready = [task for task in self._tasks.values() if self.is_ready(task.task_id)]
        queue_rank = self._queue_rank()
        ready.sort(key=lambda task: queue_rank[task.task_id])
        return tuple(ready)

    def blocked_tasks(self) -> tuple[AnalysisTask, ...]:
        blocked = [
            task
            for task in self._tasks.values()
            if task.status == "pending"
            and any(
                status != "succeeded"
                for status in self._dependency_statuses(task)
            )
        ]
        queue_rank = self._queue_rank()
        blocked.sort(key=lambda task: queue_rank[task.task_id])
        return tuple(blocked)

    def running_tasks(self) -> tuple[AnalysisTask, ...]:
        running = [
            task for task in self._tasks.values() if task.status == "running"
        ]
        queue_rank = self._queue_rank()
        running.sort(key=lambda task: queue_rank[task.task_id])
        return tuple(running)

    def next_task(self) -> Optional[AnalysisTask]:
        ready = self.ready_tasks()
        if not ready or len(self.running_tasks()) >= self._max_running_tasks:
            return None
        return ready[0]

    def _require_task(self, task_id: str) -> AnalysisTask:
        if task_id not in self._tasks:
            raise KeyError(f"Unknown task id: {task_id}")
        return self._tasks[task_id]

    def mark_running(self, task_id: str) -> AnalysisTask:
        task = self._require_task(task_id)
        if task.status != "pending":
            raise ValueError(
                f"Task '{task_id}' must be pending to run, got '{task.status}'"
            )
        if not self.is_ready(task_id):
            raise ValueError(
                f"Task '{task_id}' is not ready because its dependencies are not succeeded"
            )
        if len(self.running_tasks()) >= self._max_running_tasks:
            raise ValueError("Task scheduler is already at its running-task limit")
        task.status = "running"
        return task

    def mark_succeeded(self, task_id: str) -> AnalysisTask:
        task = self._require_task(task_id)
        if task.status != "running":
            raise ValueError(
                f"Task '{task_id}' must be running to succeed, got '{task.status}'"
            )
        task.status = "succeeded"
        return task

    def mark_failed(self, task_id: str, *, error: Optional[str] = None) -> AnalysisTask:
        task = self._require_task(task_id)
        if task.status != "running":
            raise ValueError(
                f"Task '{task_id}' must be running to fail, got '{task.status}'"
            )
        task.status = "failed"
        if error:
            self._errors[task_id] = error
        return task

    def mark_cancelled(self, task_id: str) -> AnalysisTask:
        task = self._require_task(task_id)
        if task.status not in {"pending", "running"}:
            raise ValueError(
                f"Task '{task_id}' cannot be cancelled from status '{task.status}'"
            )
        task.status = "cancelled"
        return task

    def mark_skipped(self, task_id: str) -> AnalysisTask:
        task = self._require_task(task_id)
        if task.status not in {"pending", "running"}:
            raise ValueError(
                f"Task '{task_id}' cannot be skipped from status '{task.status}'"
            )
        task.status = "skipped"
        return task

    def error(self, task_id: str) -> Optional[str]:
        return self._errors.get(task_id)


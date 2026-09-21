"""Runtime V2 task runner.

The runner is the first Runtime-controlled execution loop.  It deliberately
does not know about LangGraph, LLMs, APIs, or the UI.  Its only contract is:

    ready task -> executor request -> ExecutionResult -> scheduler state

The graph layer can later call ``execute_next`` one node at a time, while
offline and API callers may use ``run`` for a complete deterministic loop.
"""

from __future__ import annotations

from typing import Callable, Iterable, Literal, Optional, Sequence

from pydantic import BaseModel, Field

from langgraph_langchain.execution.task_models import TaskExecutionRequest
from langgraph_langchain.runtime.executor import TaskExecutor
from langgraph_langchain.runtime.models import ExecutionResult
from langgraph_langchain.runtime.scheduler import TaskScheduler
from langgraph_langchain.schemas import AnalysisTask, EvidenceItem, VerificationResult


class TaskRunnerStep(BaseModel):
    """Outcome of one runner iteration."""

    action: Literal[
        "executed",
        "completed",
        "failed",
        "blocked",
        "incomplete",
        "limit_reached",
    ]
    result: Optional[ExecutionResult] = None
    reason: Optional[str] = None

    @property
    def task_id(self) -> Optional[str]:
        return self.result.task_id if self.result is not None else None


class TaskRunSummary(BaseModel):
    """State summary produced by a complete runner loop."""

    run_id: str
    status: Literal[
        "completed",
        "failed",
        "blocked",
        "incomplete",
        "limit_reached",
    ]
    executed_task_ids: list[str] = Field(default_factory=list)
    failed_task_ids: list[str] = Field(default_factory=list)
    cancelled_task_ids: list[str] = Field(default_factory=list)
    blocked_task_ids: list[str] = Field(default_factory=list)
    results: list[ExecutionResult] = Field(default_factory=list)
    reason: Optional[str] = None

    @property
    def succeeded(self) -> bool:
        return self.status == "completed"

    @property
    def result_by_task_id(self) -> dict[str, ExecutionResult]:
        return {result.task_id: result for result in self.results if result.task_id}


TaskRequestFactory = Callable[[AnalysisTask], TaskExecutionRequest]


def _terminal_tasks(scheduler: TaskScheduler) -> list[AnalysisTask]:
    return [
        task
        for task in scheduler.tasks
        if task.status in {"succeeded", "failed", "skipped", "cancelled"}
    ]


class TaskRunner:
    """Run scheduled tasks through an executor and update scheduler state."""

    def __init__(
        self,
        *,
        scheduler: TaskScheduler,
        executor: TaskExecutor,
        verifier: Optional[Callable[[ExecutionResult], Sequence[VerificationResult]]] = None,
        evidence_factory: Optional[
            Callable[[ExecutionResult, Sequence[VerificationResult]], EvidenceItem]
        ] = None,
    ) -> None:
        self.scheduler = scheduler
        self.executor = executor
        self.verifier = verifier
        self.evidence_factory = evidence_factory

    def _review_result(
        self,
        result: ExecutionResult,
    ) -> tuple[list[VerificationResult], Optional[EvidenceItem], list[str]]:
        """Run optional deterministic verification and evidence generation."""
        errors: list[str] = []
        verifications: list[VerificationResult] = []
        evidence: Optional[EvidenceItem] = None

        if self.verifier is not None:
            try:
                verifications = list(self.verifier(result))
            except Exception as exc:
                return [], None, [f"Verification hook failed: {exc}"]

            failed = [
                verification
                for verification in verifications
                if verification.status != "passed" or verification.passed is not True
            ]
            if failed:
                errors.extend(
                    f"Verification failed: {verification.message or verification.verification_id}"
                    for verification in failed
                )

        # Evidence is only attempted when verification has not failed and the
        # execution produced artifacts.  A task may legitimately return a
        # scalar/status-only result with no artifact.
        if (
            self.evidence_factory is not None
            and result.output_artifact_ids
            and not errors
        ):
            try:
                evidence = self.evidence_factory(result, verifications)
            except Exception as exc:
                errors.append(f"Evidence generation failed: {exc}")

        return verifications, evidence, errors

    def _finish_step(
        self,
        result: ExecutionResult,
    ) -> TaskRunnerStep:
        task_id = result.task_id
        if task_id is None:
            return TaskRunnerStep(
                action="failed",
                result=result,
                reason="Executor returned an execution result without task_id",
            )

        if result.status == "succeeded":
            verifications, evidence, review_errors = self._review_result(result)
            result.verification_results = verifications
            result.evidence = evidence
            if review_errors:
                result.status = "failed"
                result.error = {
                    "type": "RuntimeVerificationError",
                    "message": "; ".join(review_errors),
                }
                self.scheduler.mark_failed(task_id, error="; ".join(review_errors))
                return TaskRunnerStep(
                    action="failed",
                    result=result,
                    reason="; ".join(review_errors),
                )
            self.scheduler.mark_succeeded(task_id)
            return TaskRunnerStep(action="executed", result=result)

        if result.status == "cancelled":
            self.scheduler.mark_cancelled(task_id)
            return TaskRunnerStep(action="executed", result=result)

        message = "Task execution failed"
        if result.error and result.error.get("message"):
            message = str(result.error["message"])
        self.scheduler.mark_failed(task_id, error=message)
        return TaskRunnerStep(action="failed", result=result, reason=message)

    def execute_next(
        self,
        *,
        run_id: str,
        request_factory: TaskRequestFactory,
    ) -> TaskRunnerStep:
        """Execute one ready task, or explain why execution cannot proceed."""
        task = self.scheduler.next_task()
        if task is None:
            pending = [item for item in self.scheduler.tasks if item.status == "pending"]
            failed = [item for item in self.scheduler.tasks if item.status == "failed"]
            if not pending:
                return TaskRunnerStep(action="completed")
            if failed:
                failed_ids = ", ".join(item.task_id for item in failed)
                return TaskRunnerStep(
                    action="failed",
                    reason=f"Execution stopped because tasks failed: {failed_ids}",
                )
            blocked = [item.task_id for item in self.scheduler.blocked_tasks()]
            return TaskRunnerStep(
                action="blocked" if blocked else "incomplete",
                reason=(
                    f"No ready task; blocked tasks: {', '.join(blocked)}"
                    if blocked
                    else "No ready task; a dependency is still running"
                ),
            )

        self.scheduler.mark_running(task.task_id)
        request = request_factory(task)
        if not isinstance(request, TaskExecutionRequest):
            raise TypeError("request_factory must return TaskExecutionRequest")
        if request.task.task_id != task.task_id:
            raise ValueError(
                "request_factory returned a request for task "
                f"'{request.task.task_id}' while scheduling '{task.task_id}'"
            )
        if request.run_id != run_id:
            raise ValueError(
                f"request_factory returned run_id '{request.run_id}', expected '{run_id}'"
            )

        result = self.executor.execute(request)
        return self._finish_step(result)

    def run(
        self,
        *,
        run_id: str,
        request_factory: TaskRequestFactory,
        max_steps: Optional[int] = None,
        stop_on_failure: bool = True,
    ) -> TaskRunSummary:
        """Run until completion, failure, blockage, or the configured limit."""
        if max_steps is not None and max_steps < 1:
            raise ValueError("max_steps must be at least 1")

        executed_task_ids: list[str] = []
        results: list[ExecutionResult] = []
        reason: Optional[str] = None
        status: Optional[str] = None
        remaining_steps = max_steps

        while status is None:
            if remaining_steps is not None and remaining_steps <= 0:
                status = "limit_reached"
                reason = f"Task runner reached max_steps={max_steps}"
                break

            step = self.execute_next(
                run_id=run_id,
                request_factory=request_factory,
            )

            if step.action == "completed":
                status = "completed"
                break

            if step.action in {"blocked", "incomplete", "limit_reached"}:
                status = step.action
                reason = step.reason
                break

            result = step.result
            if result is None:
                # A failed step without a result means the runner could not
                # proceed because one or more dependency tasks already failed.
                status = "failed"
                reason = step.reason or "Task execution failed"
                break

            if result.task_id is not None:
                executed_task_ids.append(result.task_id)
            results.append(result)

            if remaining_steps is not None:
                remaining_steps -= 1

            if (
                step.action == "failed"
                and stop_on_failure
                and result.status in {"failed", "cancelled"}
            ):
                status = "failed"
                reason = step.reason
                break

        failed_ids = [
            task.task_id
            for task in self.scheduler.tasks
            if task.status == "failed"
        ]
        cancelled_ids = [
            task.task_id
            for task in self.scheduler.tasks
            if task.status == "cancelled"
        ]

        summary = TaskRunSummary(
            run_id=run_id,
            status=status or "incomplete",  # type: ignore[arg-type]
            executed_task_ids=executed_task_ids,
            failed_task_ids=failed_ids,
            cancelled_task_ids=cancelled_ids,
            blocked_task_ids=[
                task.task_id for task in self.scheduler.blocked_tasks()
            ],
            results=results,
            reason=reason,
        )
        return summary


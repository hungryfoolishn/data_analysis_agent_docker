"""Runtime V10.3.0 deterministic DAG scheduler tests."""

from __future__ import annotations

import pytest

from langgraph_langchain.runtime.financial_agent import (
    DeterministicDAGScheduler,
    ExecutionPlan,
    ExecutionPlanStatus,
    ExecutionStatus,
    ExecutionTask,
    FailurePolicy,
    RuntimeExecutionStatus,
    SchedulerValidationError,
    TaskExecutionOutcome,
)


def _task(
    task_id: str,
    *,
    dependencies: tuple[str, ...] = (),
    status: ExecutionStatus = ExecutionStatus.PENDING,
    failure_policy: FailurePolicy = FailurePolicy.CONTINUE_WITH_WARNING,
) -> ExecutionTask:
    return ExecutionTask(
        task_id=task_id,
        tool_id="fake_task_tool",
        tool_version="v10.3.0",
        company_id="company_1",
        company_name="Company One",
        period="2024",
        metric_id=task_id,
        metric_name=task_id,
        dependencies=list(dependencies),
        status=status,
        status_reason="data_unavailable" if status == ExecutionStatus.UNAVAILABLE else None,
        failure_policy=failure_policy,
    )


def _plan(*tasks: ExecutionTask) -> ExecutionPlan:
    return ExecutionPlan(
        plan_id="plan_dag_test",
        query_id="query_dag_test",
        tasks=list(tasks),
    )


def _success(task: ExecutionTask) -> TaskExecutionOutcome:
    return TaskExecutionOutcome(
        status=ExecutionStatus.SUCCEEDED,
        status_reason="task succeeded",
        output_refs=[f"{task.task_id}_output"],
        metadata={"executor": "fake"},
    )


def test_scheduler_executes_tasks_in_deterministic_topological_order():
    calls: list[str] = []
    scheduler = DeterministicDAGScheduler(
        lambda task: (calls.append(task.task_id), _success(task))[1]
    )
    plan = _plan(
        _task("task_c", dependencies=("task_b",)),
        _task("task_b", dependencies=("task_a",)),
        _task("task_a"),
        _task("task_d"),
    )

    result = scheduler.run(plan)

    assert calls == ["task_a", "task_d", "task_b", "task_c"]
    assert result.metadata["execution_order"] == calls
    assert result.status == RuntimeExecutionStatus.SUCCEEDED
    assert plan.status == ExecutionPlanStatus.SUCCEEDED
    assert result.succeeded_count == 4
    assert result.unavailable_count == 0
    assert result.failed_count == 0
    assert result.blocked_count == 0
    assert all(
        item.status == ExecutionStatus.SUCCEEDED
        for item in result.task_results
    )


def test_scheduler_executes_multiple_independent_tasks():
    calls: list[str] = []
    scheduler = DeterministicDAGScheduler(
        lambda task: (calls.append(task.task_id), _success(task))[1]
    )
    plan = _plan(_task("task_a"), _task("task_b"), _task("task_c"))

    result = scheduler.run(plan)

    assert calls == ["task_a", "task_b", "task_c"]
    assert result.status == RuntimeExecutionStatus.SUCCEEDED
    assert result.succeeded_count == 3
    assert result.blocked_count == 0


def test_scheduler_blocks_downstream_after_dependency_failure():
    calls: list[str] = []

    def execute(task: ExecutionTask) -> TaskExecutionOutcome:
        calls.append(task.task_id)
        if task.task_id == "task_a":
            return TaskExecutionOutcome(
                status=ExecutionStatus.FAILED,
                status_reason="RuntimeError: fake failure",
                error="RuntimeError: fake failure",
            )
        return _success(task)

    scheduler = DeterministicDAGScheduler(execute)
    plan = _plan(
        _task("task_a"),
        _task("task_b", dependencies=("task_a",)),
        _task("task_c"),
    )

    result = scheduler.run(plan)

    assert calls == ["task_a", "task_c"]
    assert result.status == RuntimeExecutionStatus.FAILED
    assert plan.status == ExecutionPlanStatus.FAILED
    assert result.succeeded_count == 1
    assert result.failed_count == 1
    assert result.blocked_count == 1
    assert result.metadata["blocked_task_ids"] == ["task_b"]
    assert result.task_results[1].status == ExecutionStatus.BLOCKED
    assert result.task_results[1].status_reason == "dependency task_a failed"
    assert result.task_results[1].error == "dependency task_a failed"


def test_scheduler_blocks_downstream_after_dependency_unavailable():
    calls: list[str] = []
    scheduler = DeterministicDAGScheduler(
        lambda task: (calls.append(task.task_id), _success(task))[1]
    )
    plan = _plan(
        _task("task_a", status=ExecutionStatus.UNAVAILABLE),
        _task("task_b", dependencies=("task_a",)),
        _task("task_c"),
    )

    result = scheduler.run(plan)

    assert calls == ["task_c"]
    assert result.status == RuntimeExecutionStatus.PARTIAL
    assert plan.status == ExecutionPlanStatus.PARTIAL
    assert result.succeeded_count == 1
    assert result.unavailable_count == 1
    assert result.failed_count == 0
    assert result.blocked_count == 1
    assert result.metadata["blocked_task_ids"] == ["task_b"]


def test_scheduler_returns_blocked_result_when_no_task_is_ready():
    calls: list[str] = []
    scheduler = DeterministicDAGScheduler(
        lambda task: (calls.append(task.task_id), _success(task))[1]
    )
    plan = _plan(
        _task("task_a", status=ExecutionStatus.UNAVAILABLE),
        _task("task_b", dependencies=("task_a",)),
    )

    result = scheduler.run(plan)

    assert calls == []
    assert result.status == RuntimeExecutionStatus.BLOCKED
    assert plan.status == ExecutionPlanStatus.BLOCKED
    assert result.succeeded_count == 0
    assert result.unavailable_count == 1
    assert result.failed_count == 0
    assert result.blocked_count == 1
    assert result.metadata["blocked_task_ids"] == ["task_b"]


def test_scheduler_converts_executor_exception_to_failure():
    calls: list[str] = []

    def execute(task: ExecutionTask) -> TaskExecutionOutcome:
        calls.append(task.task_id)
        if task.task_id == "task_a":
            raise RuntimeError("fake executor crashed")
        return _success(task)

    scheduler = DeterministicDAGScheduler(execute)
    plan = _plan(
        _task("task_a"),
        _task("task_b", dependencies=("task_a",)),
    )

    result = scheduler.run(plan)

    assert calls == ["task_a"]
    assert result.status == RuntimeExecutionStatus.FAILED
    assert result.failed_count == 1
    assert result.blocked_count == 1
    assert result.task_results[0].error == "RuntimeError: fake executor crashed"


def test_scheduler_rejects_dependency_cycle_before_execution():
    calls: list[str] = []
    scheduler = DeterministicDAGScheduler(
        lambda task: (calls.append(task.task_id), _success(task))[1]
    )
    plan = _plan(
        _task("task_a", dependencies=("task_b",)),
        _task("task_b", dependencies=("task_a",)),
    )

    with pytest.raises(SchedulerValidationError, match="dependency cycle"):
        scheduler.run(plan)

    assert calls == []
    assert plan.status == ExecutionPlanStatus.PENDING


def test_scheduler_rejects_unknown_dependency_before_execution():
    scheduler = DeterministicDAGScheduler(_success)
    plan = _plan(_task("task_a", dependencies=("missing_task",)))

    with pytest.raises(SchedulerValidationError, match="unknown dependency"):
        scheduler.run(plan)


def test_scheduler_rejects_non_pending_plan():
    scheduler = DeterministicDAGScheduler(_success)
    plan = _plan(_task("task_a"))
    plan.status = ExecutionPlanStatus.RUNNING

    with pytest.raises(SchedulerValidationError, match="must be PENDING"):
        scheduler.run(plan)


def test_scheduler_rejects_non_startable_initial_task_status():
    scheduler = DeterministicDAGScheduler(_success)
    plan = _plan(_task("task_a", status=ExecutionStatus.SUCCEEDED))

    with pytest.raises(SchedulerValidationError, match="non-startable status"):
        scheduler.run(plan)


def test_scheduler_does_not_count_a_task_more_than_once():
    calls: list[str] = []
    scheduler = DeterministicDAGScheduler(
        lambda task: (calls.append(task.task_id), _success(task))[1]
    )
    plan = _plan(
        _task("task_a"),
        _task("task_b", dependencies=("task_a",)),
    )

    result = scheduler.run(plan)

    assert len(calls) == len(set(calls)) == 2
    assert len(result.task_results) == len(plan.tasks)
    assert (
        result.succeeded_count
        + result.unavailable_count
        + result.failed_count
        + result.blocked_count
        == len(result.task_results)
    )


@pytest.mark.parametrize(
    "invalid_return",
    [None, {"status": "SUCCEEDED"}],
    ids=["none", "dict"],
)
def test_scheduler_converts_invalid_executor_return_type_to_failure(invalid_return):
    calls: list[str] = []

    def execute(task: ExecutionTask):
        calls.append(task.task_id)
        return invalid_return

    scheduler = DeterministicDAGScheduler(execute)
    plan = _plan(
        _task("task_a"),
        _task("task_b", dependencies=("task_a",)),
    )

    result = scheduler.run(plan)

    expected_type = type(invalid_return).__name__
    assert calls == ["task_a"]
    assert result.status == RuntimeExecutionStatus.FAILED
    assert plan.status == ExecutionPlanStatus.FAILED
    assert plan.finished_at is not None
    assert result.failed_count == 1
    assert result.blocked_count == 1
    assert result.task_results[0].error == (
        "Invalid task executor return type: expected TaskExecutionOutcome, "
        f"got {expected_type}"
    )
    assert result.task_results[1].status == ExecutionStatus.BLOCKED


def test_scheduler_converts_invalid_outcome_status_to_failure():
    scheduler = DeterministicDAGScheduler(
        lambda task: TaskExecutionOutcome(status=ExecutionStatus.READY)
    )
    plan = _plan(_task("task_a"))

    result = scheduler.run(plan)

    assert result.status == RuntimeExecutionStatus.FAILED
    assert plan.status == ExecutionPlanStatus.FAILED
    assert plan.finished_at is not None
    assert result.failed_count == 1
    assert result.task_results[0].error == "Invalid task execution status: READY"


def test_scheduler_internal_exception_returns_structured_failure(monkeypatch):
    scheduler = DeterministicDAGScheduler(_success)

    def apply_outcome(task: ExecutionTask, outcome: TaskExecutionOutcome):
        raise RuntimeError("scheduler state update crashed")

    monkeypatch.setattr(scheduler, "_apply_outcome", apply_outcome)
    plan = _plan(
        _task("task_a"),
        _task("task_b"),
    )

    result = scheduler.run(plan)

    assert result.status == RuntimeExecutionStatus.FAILED
    assert plan.status == ExecutionPlanStatus.FAILED
    assert plan.finished_at is not None
    assert result.metadata["scheduler_internal_error"] is True
    assert result.metadata["error"] == "RuntimeError: scheduler state update crashed"
    assert result.task_results[0].status == ExecutionStatus.FAILED
    assert result.task_results[0].error == "RuntimeError: scheduler state update crashed"
    assert result.task_results[1].status == ExecutionStatus.BLOCKED
    assert result.task_results[1].error == (
        "scheduler internal error: RuntimeError: scheduler state update crashed"
    )
    assert result.failed_count == 1
    assert result.blocked_count == 1


def test_fail_fast_policy_stops_independent_tasks(monkeypatch):
    calls: list[str] = []

    def execute(task: ExecutionTask) -> TaskExecutionOutcome:
        calls.append(task.task_id)
        if task.task_id == "task_a":
            return TaskExecutionOutcome(
                status=ExecutionStatus.FAILED,
                status_reason="fake failure",
                error="fake failure",
            )
        return _success(task)

    scheduler = DeterministicDAGScheduler(execute)
    plan = _plan(
        _task("task_a", failure_policy=FailurePolicy.FAIL_FAST),
        _task("task_b"),
        _task("task_c", dependencies=("task_a",)),
    )

    result = scheduler.run(plan)

    assert calls == ["task_a"]
    assert result.status == RuntimeExecutionStatus.FAILED
    assert plan.status == ExecutionPlanStatus.FAILED
    assert result.failed_count == 1
    assert result.blocked_count == 2
    assert result.metadata["failure_policy_stop"] == {
        "task_id": "task_a",
        "policy": "FAIL_FAST",
        "reason": "fail-fast: task task_a failed",
    }
    assert result.task_results[1].status_reason == "fail-fast: task task_a failed"
    assert result.task_results[2].status_reason == "dependency task_a failed"


def test_continue_with_warning_policy_executes_independent_tasks():
    calls: list[str] = []

    def execute(task: ExecutionTask) -> TaskExecutionOutcome:
        calls.append(task.task_id)
        if task.task_id == "task_a":
            return TaskExecutionOutcome(
                status=ExecutionStatus.FAILED,
                status_reason="fake failure",
                error="fake failure",
            )
        return _success(task)

    scheduler = DeterministicDAGScheduler(execute)
    plan = _plan(
        _task(
            "task_a",
            failure_policy=FailurePolicy.CONTINUE_WITH_WARNING,
        ),
        _task("task_b"),
        _task("task_c", dependencies=("task_a",)),
    )

    result = scheduler.run(plan)

    assert calls == ["task_a", "task_b"]
    assert result.status == RuntimeExecutionStatus.FAILED
    assert result.succeeded_count == 1
    assert result.failed_count == 1
    assert result.blocked_count == 1
    assert "failure_policy_stop" not in result.metadata


def test_replan_or_fail_policy_returns_explicit_unsupported_failure():
    calls: list[str] = []

    def execute(task: ExecutionTask) -> TaskExecutionOutcome:
        calls.append(task.task_id)
        if task.task_id == "task_a":
            return TaskExecutionOutcome(
                status=ExecutionStatus.FAILED,
                status_reason="fake failure",
                error="fake failure",
            )
        return _success(task)

    scheduler = DeterministicDAGScheduler(execute)
    plan = _plan(
        _task(
            "task_a",
            failure_policy=FailurePolicy.REPLAN_OR_FAIL,
        ),
        _task("task_b"),
    )

    result = scheduler.run(plan)

    assert calls == ["task_a"]
    assert result.status == RuntimeExecutionStatus.FAILED
    assert result.failed_count == 1
    assert result.blocked_count == 1
    assert result.metadata["unsupported_failure_policies"] == ["task_a"]
    assert result.task_results[0].status_reason == (
        "REPLAN_OR_FAIL is not supported by deterministic DAG scheduler: "
        "fake failure"
    )
    assert result.task_results[1].status_reason == (
        "REPLAN_OR_FAIL is not supported by deterministic DAG scheduler"
    )


def test_scheduler_blocks_all_descendants_after_ancestor_failure():
    calls: list[str] = []

    def execute(task: ExecutionTask) -> TaskExecutionOutcome:
        calls.append(task.task_id)
        if task.task_id == "task_a":
            return TaskExecutionOutcome(
                status=ExecutionStatus.FAILED,
                status_reason="fake failure",
                error="fake failure",
            )
        return _success(task)

    scheduler = DeterministicDAGScheduler(execute)
    plan = _plan(
        _task("task_a"),
        _task("task_b", dependencies=("task_a",)),
        _task("task_c", dependencies=("task_b",)),
        _task("task_d"),
    )

    result = scheduler.run(plan)

    assert calls == ["task_a", "task_d"]
    assert result.succeeded_count == 1
    assert result.failed_count == 1
    assert result.blocked_count == 2
    assert result.metadata["blocked_task_ids"] == ["task_b", "task_c"]


def test_duplicate_dependency_declarations_do_not_duplicate_execution():
    calls: list[str] = []
    scheduler = DeterministicDAGScheduler(
        lambda task: (calls.append(task.task_id), _success(task))[1]
    )
    plan = _plan(
        _task("task_a"),
        _task("task_b", dependencies=("task_a", "task_a")),
    )

    result = scheduler.run(plan)

    assert calls == ["task_a", "task_b"]
    assert len(calls) == len(set(calls)) == 2
    assert len(result.task_results) == 2
    assert result.succeeded_count == 2


def test_all_unavailable_tasks_return_blocked_result():
    calls: list[str] = []
    scheduler = DeterministicDAGScheduler(
        lambda task: (calls.append(task.task_id), _success(task))[1]
    )
    plan = _plan(
        _task("task_a", status=ExecutionStatus.UNAVAILABLE),
        _task("task_b", status=ExecutionStatus.UNAVAILABLE),
    )

    result = scheduler.run(plan)

    assert calls == []
    assert result.status == RuntimeExecutionStatus.BLOCKED
    assert plan.status == ExecutionPlanStatus.BLOCKED
    assert result.unavailable_count == 2
    assert result.blocked_count == 0
    assert result.succeeded_count == 0
    assert result.failed_count == 0


def test_rejected_plan_is_not_mutated_by_scheduler():
    scheduler = DeterministicDAGScheduler(_success)
    plan = _plan(_task("task_a"))
    plan.status = ExecutionPlanStatus.RUNNING

    with pytest.raises(SchedulerValidationError):
        scheduler.run(plan)

    assert plan.status == ExecutionPlanStatus.RUNNING
    assert plan.started_at is None
    assert plan.finished_at is None
    assert plan.tasks[0].status == ExecutionStatus.PENDING

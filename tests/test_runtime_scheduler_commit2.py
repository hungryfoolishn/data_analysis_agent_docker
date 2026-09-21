from __future__ import annotations

import pytest

from langgraph_langchain.runtime.plans import default_analysis_plan
from langgraph_langchain.runtime.scheduler import (
    SchedulerValidationError,
    TaskScheduler,
    analysis_tasks_from_plan,
)
from langgraph_langchain.schemas import AnalysisTask


def make_task(
    task_id: str,
    *,
    depends_on: tuple[str, ...] = (),
    method: str = "python_repl",
    session_id: str = "session_1",
) -> AnalysisTask:
    return AnalysisTask(
        task_id=task_id,
        session_id=session_id,
        question=f"Run {task_id}",
        task_type="metric",
        executor_type="python" if method == "python_repl" else "structured",
        method=method,
        depends_on=list(depends_on),
    )


def test_empty_task_graph_is_valid():
    scheduler = TaskScheduler([])

    assert scheduler.tasks == ()
    assert scheduler.ready_tasks() == ()
    assert scheduler.blocked_tasks() == ()
    assert scheduler.execution_queue() == ()
    assert scheduler.next_task() is None


def test_single_task_lifecycle():
    scheduler = TaskScheduler([make_task("task_a")])

    assert scheduler.is_ready("task_a")
    assert scheduler.next_task().task_id == "task_a"

    scheduler.mark_running("task_a")
    assert scheduler.next_task() is None
    assert scheduler.ready_tasks() == ()

    scheduler.mark_succeeded("task_a")
    assert scheduler.get_task("task_a").status == "succeeded"
    assert scheduler.ready_tasks() == ()
    assert scheduler.next_task() is None


def test_execution_queue_respects_dependencies_regardless_of_input_order():
    task_c = make_task("task_c", depends_on=("task_b",))
    task_b = make_task("task_b", depends_on=("task_a",))
    task_a = make_task("task_a")
    scheduler = TaskScheduler([task_c, task_b, task_a])

    assert scheduler.execution_queue() == ("task_a", "task_b", "task_c")
    assert [task.task_id for task in scheduler.ready_tasks()] == ["task_a"]
    assert [task.task_id for task in scheduler.blocked_tasks()] == ["task_b", "task_c"]

    scheduler.mark_running("task_a")
    scheduler.mark_succeeded("task_a")
    assert scheduler.next_task().task_id == "task_b"


def test_failed_dependency_blocks_downstream_task():
    scheduler = TaskScheduler(
        [
            make_task("task_a"),
            make_task("task_b", depends_on=("task_a",)),
        ]
    )

    scheduler.mark_running("task_a")
    scheduler.mark_failed("task_a", error="invalid column")

    assert scheduler.get_task("task_a").status == "failed"
    assert scheduler.error("task_a") == "invalid column"
    assert scheduler.ready_tasks() == ()
    assert [task.task_id for task in scheduler.blocked_tasks()] == ["task_b"]


def test_duplicate_task_id_is_rejected():
    with pytest.raises(SchedulerValidationError, match="Duplicate task id"):
        TaskScheduler(
            [
                make_task("task_a"),
                make_task("task_a"),
            ]
        )


def test_unknown_dependency_is_rejected():
    with pytest.raises(SchedulerValidationError, match="unknown dependency"):
        TaskScheduler([make_task("task_a", depends_on=("missing_task",))])


def test_self_dependency_is_rejected():
    with pytest.raises(SchedulerValidationError, match="cannot depend on itself"):
        TaskScheduler([make_task("task_a", depends_on=("task_a",))])


def test_cycle_is_rejected():
    with pytest.raises(SchedulerValidationError, match="Cyclic dependencies"):
        TaskScheduler(
            [
                make_task("task_a", depends_on=("task_b",)),
                make_task("task_b", depends_on=("task_a",)),
            ]
        )


def test_from_plan_converts_runtime_steps_to_tasks():
    plan = default_analysis_plan("Analyze department revenue")
    tasks = analysis_tasks_from_plan(plan, session_id="session_plan")
    scheduler = TaskScheduler(tasks)

    assert [task.task_id for task in scheduler.tasks] == [
        step.step_id for step in plan.steps
    ]
    assert scheduler.tasks[0].session_id == "session_plan"
    assert scheduler.tasks[0].task_type == "schema"
    assert scheduler.tasks[0].executor_type == "structured"
    assert scheduler.tasks[0].method == "load_data"
    assert scheduler.tasks[0].plan_step_id == plan.steps[0].step_id
    assert scheduler.execution_queue() == tuple(step.step_id for step in plan.steps)
    assert scheduler.next_task().task_id == plan.steps[0].step_id


def test_max_running_tasks_limit_is_enforced():
    scheduler = TaskScheduler(
        [
            make_task("task_a"),
            make_task("task_b"),
            make_task("task_c"),
        ],
        max_running_tasks=2,
    )

    scheduler.mark_running("task_a")
    scheduler.mark_running("task_b")
    assert len(scheduler.running_tasks()) == 2
    assert scheduler.next_task() is None

    with pytest.raises(ValueError, match="running-task limit"):
        scheduler.mark_running("task_c")

    scheduler.mark_succeeded("task_a")
    scheduler.mark_running("task_c")
    assert [task.task_id for task in scheduler.running_tasks()] == [
        "task_b",
        "task_c",
    ]


def test_analysis_tasks_from_plan_injects_structural_argument_defaults():
    plan = default_analysis_plan("Analyze revenue")
    tasks = analysis_tasks_from_plan(
        plan,
        session_id="session_defaults",
        argument_defaults_by_method={
            "load_data": {"file_path": "/tmp/source.csv", "sheet_name": ""}
        },
    )

    load_task = next(task for task in tasks if task.method == "load_data")
    profile_task = next(task for task in tasks if task.method == "eda_profile")

    assert load_task.constraints["arguments"] == {
        "file_path": "/tmp/source.csv",
        "sheet_name": "",
    }
    assert "arguments" not in profile_task.constraints
    assert load_task.plan_step_id == load_task.task_id


def test_scheduler_from_plan_passes_argument_defaults():
    plan = default_analysis_plan("Analyze revenue")
    scheduler = TaskScheduler.from_plan(
        plan,
        session_id="session_defaults",
        argument_defaults_by_method={
            "load_data": {"file_path": "/tmp/source.csv"}
        },
    )

    load_task = next(task for task in scheduler.tasks if task.method == "load_data")
    assert load_task.constraints["arguments"] == {"file_path": "/tmp/source.csv"}

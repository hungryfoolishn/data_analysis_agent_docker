from __future__ import annotations

import json
from typing import Any

import pytest

from langgraph_langchain.execution.structured_executor import StructuredTaskExecutor
from langgraph_langchain.execution.task_models import TaskExecutionRequest
from langgraph_langchain.runtime.executor import TaskExecutor
from langgraph_langchain.runtime.runner import TaskRunner
from langgraph_langchain.runtime.scheduler import TaskScheduler
from langgraph_langchain.schemas import AnalysisTask


class FakeTool:
    def __init__(self, result: Any) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    def invoke(self, arguments: dict[str, Any]) -> Any:
        self.calls.append(arguments)
        return self.result


class FailingTool:
    def __init__(self, error: Exception) -> None:
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def invoke(self, arguments: dict[str, Any]) -> Any:
        self.calls.append(arguments)
        raise self.error


def make_task(
    task_id: str,
    *,
    method: str,
    depends_on: tuple[str, ...] = (),
) -> AnalysisTask:
    return AnalysisTask(
        task_id=task_id,
        session_id="session_runner",
        question=f"Run {task_id}",
        task_type="comparison",
        executor_type="structured",
        method=method,
        depends_on=list(depends_on),
    )


def build_runner(
    tools: dict[str, Any],
    tasks: list[AnalysisTask],
) -> tuple[TaskRunner, TaskScheduler]:
    scheduler = TaskScheduler(tasks)
    executor = TaskExecutor(
        structured=StructuredTaskExecutor(tools=tools)
    )
    return TaskRunner(scheduler=scheduler, executor=executor), scheduler


def request_factory(run_id: str, arguments_by_task: dict[str, dict[str, Any]]):
    def factory(task: AnalysisTask) -> TaskExecutionRequest:
        return TaskExecutionRequest(
            task=task,
            run_id=run_id,
            arguments=arguments_by_task.get(task.task_id, {}),
        )

    return factory


def test_task_runner_completes_full_dependency_chain():
    tools = {
        "load_data": FakeTool(
            json.dumps({"status": "ok", "artifact_id": "artifact_data"})
        ),
        "compare_groups": FakeTool(
            json.dumps({"status": "ok", "artifact_id": "artifact_compare"})
        ),
        "finish_report": FakeTool(
            json.dumps({"status": "ok", "artifact_id": "artifact_report"})
        ),
    }
    runner, scheduler = build_runner(
        tools,
        [
            make_task("task_load", method="load_data"),
            make_task(
                "task_compare",
                method="compare_groups",
                depends_on=("task_load",),
            ),
            make_task(
                "task_report",
                method="finish_report",
                depends_on=("task_compare",),
            ),
        ],
    )

    summary = runner.run(
        run_id="run_full",
        request_factory=request_factory(
            "run_full",
            {
                "task_compare": {"dimension": "department"},
            },
        ),
    )

    assert summary.succeeded is True
    assert summary.status == "completed"
    assert summary.executed_task_ids == [
        "task_load",
        "task_compare",
        "task_report",
    ]
    assert summary.failed_task_ids == []
    assert summary.blocked_task_ids == []
    assert [result.task_id for result in summary.results] == [
        "task_load",
        "task_compare",
        "task_report",
    ]
    assert [result.output_artifact_ids for result in summary.results] == [
        ["artifact_data"],
        ["artifact_compare"],
        ["artifact_report"],
    ]
    assert [task.status for task in scheduler.tasks] == [
        "succeeded",
        "succeeded",
        "succeeded",
    ]


def test_task_runner_stops_on_failure_and_blocks_downstream():
    tools = {
        "load_data": FailingTool(RuntimeError("bad source"))
    }
    runner, scheduler = build_runner(
        tools,
        [
            make_task("task_load", method="load_data"),
            make_task(
                "task_compare",
                method="compare_groups",
                depends_on=("task_load",),
            ),
        ],
    )

    summary = runner.run(
        run_id="run_failure",
        request_factory=request_factory("run_failure", {}),
    )

    assert summary.status == "failed"
    assert summary.reason == "bad source"
    assert summary.executed_task_ids == ["task_load"]
    assert summary.failed_task_ids == ["task_load"]
    assert summary.blocked_task_ids == ["task_compare"]
    assert scheduler.get_task("task_load").status == "failed"
    assert scheduler.get_task("task_compare").status == "pending"


def test_task_runner_without_stop_on_failure_reports_blocked_state():
    tools = {
        "load_data": FailingTool(RuntimeError("bad source"))
    }
    runner, scheduler = build_runner(
        tools,
        [
            make_task("task_load", method="load_data"),
            make_task(
                "task_compare",
                method="compare_groups",
                depends_on=("task_load",),
            ),
        ],
    )

    summary = runner.run(
        run_id="run_no_stop",
        request_factory=request_factory("run_no_stop", {}),
        stop_on_failure=False,
    )

    assert summary.status == "failed"
    assert summary.reason == "Execution stopped because tasks failed: task_load"
    assert summary.failed_task_ids == ["task_load"]
    assert summary.blocked_task_ids == ["task_compare"]
    assert scheduler.get_task("task_load").status == "failed"


def test_task_runner_respects_max_steps():
    tools = {
        "compare_groups": FakeTool("task one done"),
    }
    runner, scheduler = build_runner(
        tools,
        [
            make_task("task_a", method="compare_groups"),
            make_task("task_b", method="compare_groups"),
        ],
    )

    summary = runner.run(
        run_id="run_limit",
        request_factory=request_factory("run_limit", {}),
        max_steps=1,
    )

    assert summary.status == "limit_reached"
    assert summary.reason == "Task runner reached max_steps=1"
    assert summary.executed_task_ids == ["task_a"]
    assert len(summary.results) == 1
    assert scheduler.get_task("task_a").status == "succeeded"
    assert scheduler.get_task("task_b").status == "pending"


def test_execute_next_returns_completed_when_all_tasks_are_terminal():
    runner, scheduler = build_runner(
        {"compare_groups": FakeTool("ok")},
        [make_task("task_a", method="compare_groups")],
    )
    scheduler.mark_running("task_a")
    scheduler.mark_succeeded("task_a")

    step = runner.execute_next(
        run_id="run_done",
        request_factory=request_factory("run_done", {}),
    )

    assert step.action == "completed"
    assert step.result is None
    assert step.task_id is None


def test_execute_next_returns_blocked_when_dependency_is_running():
    runner, scheduler = build_runner(
        {"compare_groups": FakeTool("ok")},
        [
            make_task("task_a", method="compare_groups"),
            make_task(
                "task_b",
                method="compare_groups",
                depends_on=("task_a",),
            ),
        ],
    )
    scheduler.mark_running("task_a")

    step = runner.execute_next(
        run_id="run_partial",
        request_factory=request_factory("run_partial", {}),
    )

    assert step.action == "blocked"
    assert "blocked tasks: task_b" in step.reason
    assert step.result is None


def test_request_factory_task_mismatch_is_rejected():
    runner, _ = build_runner(
        {"compare_groups": FakeTool("ok")},
        [make_task("task_a", method="compare_groups")],
    )

    def wrong_factory(task: AnalysisTask) -> TaskExecutionRequest:
        return TaskExecutionRequest(
            task=make_task("task_wrong", method="compare_groups"),
            run_id="run_mismatch",
        )

    with pytest.raises(ValueError, match="request_factory returned a request"):
        runner.execute_next(
            run_id="run_mismatch",
            request_factory=wrong_factory,
        )


def test_request_factory_run_id_mismatch_is_rejected():
    runner, _ = build_runner(
        {"compare_groups": FakeTool("ok")},
        [make_task("task_a", method="compare_groups")],
    )

    with pytest.raises(ValueError, match="request_factory returned run_id"):
        runner.execute_next(
            run_id="run_expected",
            request_factory=request_factory("run_actual", {}),
        )


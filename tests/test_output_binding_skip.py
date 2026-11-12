"""B11.2.1 v3 §9: failure propagation — failed / skipped / blocked."""

from __future__ import annotations

import pytest

from langgraph_langchain.execution.structured_executor import StructuredTaskExecutor
from langgraph_langchain.runtime.executor import TaskExecutor
from langgraph_langchain.runtime.models import ExecutionResult
from langgraph_langchain.runtime.runner import TaskRunner
from langgraph_langchain.execution.task_models import TaskExecutionRequest
from langgraph_langchain.runtime.scheduler import TaskScheduler
from langgraph_langchain.schemas import AnalysisTask

from tests.output_binding_common import STEP_A, STEP_B, STEP_C, build_plan


def _task(step_id, depends=None):
    return AnalysisTask(
        task_id=step_id,
        session_id="s",
        question="q",
        depends_on=list(depends or []),
        status="pending",
    )


def test_skip_dependents_cascades_transitive_pending():
    scheduler = TaskScheduler(
        [
            _task(STEP_A),
            _task(STEP_B, [STEP_A]),
            _task(STEP_C, [STEP_B]),
        ],
        max_running_tasks=1,
    )
    scheduler.mark_running(STEP_A)
    scheduler.mark_failed(STEP_A, error="boom")
    skipped = scheduler.skip_dependents(STEP_A)
    assert skipped == [STEP_B, STEP_C]
    assert scheduler.get_task(STEP_B).status == "skipped"
    assert scheduler.get_task(STEP_C).status == "skipped"


def test_skip_dependents_leaves_terminal_tasks_untouched():
    scheduler = TaskScheduler(
        [
            _task(STEP_A),
            _task(STEP_B, [STEP_A]),
        ],
        max_running_tasks=1,
    )
    scheduler.mark_running(STEP_A)
    scheduler.mark_succeeded(STEP_A)
    assert scheduler.skip_dependents(STEP_A) == []


def test_demote_to_failed_requires_succeeded_state():
    scheduler = TaskScheduler([_task(STEP_A)], max_running_tasks=1)
    with pytest.raises(ValueError):
        scheduler.demote_to_failed(STEP_A, error="x")
    scheduler.mark_running(STEP_A)
    scheduler.mark_succeeded(STEP_A)
    task = scheduler.demote_to_failed(STEP_A, error="contract")
    assert task.status == "failed"
    assert scheduler.error(STEP_A) == "contract"


class _FailingExecutor:
    def execute(self, request):
        return ExecutionResult(
            run_id=request.run_id,
            task_id=request.task.task_id,
            step_id=request.task.plan_step_id,
            tool_name=request.task.method,
            status="failed",
            input_asset_ids=[],
            error={"type": "ToolExecutionError", "message": "boom"},
        )


def test_runner_cascades_skip_on_failure():
    scheduler = TaskScheduler(
        [
            _task(STEP_A),
            _task(STEP_B, [STEP_A]),
            _task(STEP_C, [STEP_B]),
        ],
        max_running_tasks=1,
    )
    runner = TaskRunner(
        scheduler=scheduler,
        executor=TaskExecutor(structured=_FailingExecutor()),
    )
    summary = runner.run(
        run_id="run_1",
        request_factory=lambda task: TaskExecutionRequest(task=task, run_id="run_1"),
    )
    assert summary.status == "failed"
    assert scheduler.get_task(STEP_A).status == "failed"
    assert scheduler.get_task(STEP_B).status == "skipped"
    assert scheduler.get_task(STEP_C).status == "skipped"

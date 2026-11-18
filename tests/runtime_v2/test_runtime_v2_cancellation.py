from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from langgraph_langchain.execution.python_executor import IsolatedPythonExecutor
from langgraph_langchain.execution.python_task_executor import PythonTaskExecutor
from langgraph_langchain.execution.task_models import TaskExecutionRequest
from langgraph_langchain.langgraph_agent import (
    _make_tools,
    _run_runtime_v2_graph_stream,
    _Session,
)
from langgraph_langchain.runtime.models import RuntimePlanStep
from langgraph_langchain.runtime.plans import AnalysisPlan
from langgraph_langchain.schemas import AnalysisTask


def write_sales_csv(workspace: Path) -> Path:
    source = workspace / "sales.csv"
    source.write_text(
        "date,department,revenue\n"
        "2026-01-05,East,100\n"
        "2026-01-12,West,80\n",
        encoding="utf-8",
    )
    return source


def build_cancel_plan(sleep_seconds: float = 0.0) -> AnalysisPlan:
    first_code = (
        f"import time; time.sleep({sleep_seconds}); print('step1 done')"
        if sleep_seconds
        else "print('step1 done')"
    )
    first = RuntimePlanStep(
        objective="Run the first python step",
        method="python_repl",
        expected_outputs=["analysis_output"],
    )
    second = RuntimePlanStep(
        objective="Run the second python step",
        method="python_repl",
        expected_outputs=["analysis_output"],
        depends_on=[first.step_id],
    )
    plan = AnalysisPlan(
        goal="Cancel propagation E2E",
        success_criteria=["Both python steps finish"],
        metrics=[],
        dimensions=[],
        steps=[first, second],
    )
    plan.steps[0].parameters["code"] = first_code
    plan.steps[1].parameters["code"] = "print('step2 done')"
    return plan


def make_session(workspace: Path, source: Path, sleep_seconds: float = 0.0) -> _Session:
    session = _Session(
        workspace_dir=str(workspace),
        source_path=str(source),
        session_id="runtime_v2_cancel_e2e",
        user_question="Cancel propagation check",
    )
    runtime = session.analysis_runtime
    assert runtime is not None
    runtime.initialize_plan(build_cancel_plan(sleep_seconds), max_running_tasks=1)
    return session


def consume_stream(session: _Session, tools, source: Path, delay: float | None = None) -> list[str]:
    async def run() -> list[str]:
        if delay is not None:
            asyncio.get_running_loop().call_later(delay, session.cancel_event.set)
        chunks = []
        async for chunk, artifacts in _run_runtime_v2_graph_stream(
            session,
            tools=tools,
            source_path=str(source),
            react_agent=None,
        ):
            chunks.append(chunk)
        return chunks

    return asyncio.run(run())


def test_v2_cancel_before_graph_start(tmp_path: Path):
    workspace = tmp_path / "cancel_before"
    workspace.mkdir(parents=True)
    source = write_sales_csv(workspace)
    session = make_session(workspace, source)
    runtime = session.analysis_runtime
    session.cancel_event.set()
    session.cancel_event.set()  # repeated cancel requests must be idempotent

    chunks = consume_stream(session, _make_tools(session), source)

    assert any("Analysis cancelled" in chunk for chunk in chunks)
    statuses = [task.status for task in runtime.scheduler.tasks]
    assert statuses == ["cancelled", "cancelled"]
    assert runtime.run.status == "cancelled"
    assert session.runtime_outcome.status == "cancelled"
    assert session.runtime_outcome.error_code == "runtime_cancelled"
    assert session.runtime_v2_cancelled is True
    assert session.runtime_v2_completed is False
    assert session.report is None


def test_v2_cancel_during_python_task_terminates_worker(tmp_path: Path):
    workspace = tmp_path / "cancel_during"
    workspace.mkdir(parents=True)
    source = write_sales_csv(workspace)
    session = make_session(workspace, source, sleep_seconds=3.0)
    runtime = session.analysis_runtime

    started = time.monotonic()
    chunks = consume_stream(session, _make_tools(session), source, delay=0.4)
    elapsed = time.monotonic() - started

    assert any("Analysis cancelled" in chunk for chunk in chunks)
    statuses = [task.status for task in runtime.scheduler.tasks]
    assert statuses == ["cancelled", "cancelled"]
    assert runtime.run.status == "cancelled"
    assert session.runtime_outcome.status == "cancelled"
    assert session.runtime_v2_completed is False
    assert session.report is None
    # The sleeping worker must be terminated well before its 10s timeout and
    # the second step must never run.
    assert elapsed < 8.0, f"stream took {elapsed:.2f}s"
    assert runtime.executions
    first_execution = runtime.executions[0]
    assert first_execution.status == "cancelled"
    assert first_execution.duration_ms < 2500


def test_v2_cancel_race_with_task_completion_yields_cancelled_state(tmp_path: Path, monkeypatch):
    workspace = tmp_path / "cancel_race"
    workspace.mkdir(parents=True)
    source = write_sales_csv(workspace)
    session = make_session(workspace, source)
    runtime = session.analysis_runtime
    loop_holder: dict = {}

    original_execute = IsolatedPythonExecutor.execute

    def execute_then_cancel(self, request, *, cancel_event=None):
        result = original_execute(self, request, cancel_event=cancel_event)
        # Cancel arrives in the same instant the task completes; signal it on
        # the stream's own event loop from the worker thread.
        loop = loop_holder.get("loop")
        if loop is not None:
            loop.call_soon_threadsafe(session.cancel_event.set)
        return result

    monkeypatch.setattr(IsolatedPythonExecutor, "execute", execute_then_cancel)

    async def run() -> list[str]:
        loop_holder["loop"] = asyncio.get_running_loop()
        chunks = []
        async for chunk, artifacts in _run_runtime_v2_graph_stream(
            session,
            tools=_make_tools(session),
            source_path=str(source),
            react_agent=None,
        ):
            chunks.append(chunk)
        return chunks

    chunks = asyncio.run(run())

    assert any("Analysis cancelled" in chunk for chunk in chunks)
    statuses = {task.status for task in runtime.scheduler.tasks}
    assert statuses == {"succeeded", "cancelled"}
    assert runtime.run.status == "cancelled"
    assert session.runtime_outcome.status == "cancelled"
    assert session.runtime_v2_completed is False
    assert session.report is None


def test_python_task_executor_forwards_cancel_and_terminates_worker(tmp_path: Path):
    workspace = tmp_path / "worker_cancel"
    workspace.mkdir(parents=True)
    source = write_sales_csv(workspace)
    cancel_event = threading.Event()
    threading.Timer(0.2, cancel_event.set).start()
    executor = PythonTaskExecutor(
        workspace_dir=workspace,
        source_path=source,
        timeout_seconds=10.0,
        cancel_event_provider=lambda: cancel_event,
    )
    task = AnalysisTask(
        task_id="task_cancel_worker",
        session_id="runtime_v2_cancel_worker",
        question="Run a blocking computation",
        task_type="metric",
        executor_type="python",
        method="python_repl",
        plan_step_id="step_cancel_worker",
        status="pending",
        constraints={"code": "while True: pass", "workspace_dir": str(workspace)},
    )
    request = TaskExecutionRequest(task=task, run_id="run_cancel_worker", timeout_seconds=10.0)

    started = time.monotonic()
    result = executor.execute(request)
    elapsed = time.monotonic() - started

    assert result.status == "cancelled"
    assert result.error is not None
    assert result.error.get("type") == "CancelledError"
    # The worker must be terminated promptly, not run to the full timeout.
    assert elapsed < 5.0, f"cancel took {elapsed:.2f}s"

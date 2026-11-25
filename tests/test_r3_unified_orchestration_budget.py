"""R3 unified orchestration budget tests.

Locks the single accounting point in ``runtime.executor.TaskExecutor``: every
dispatched task — structured tool call, react step, python_repl execution — is
counted against one shared per-run budget and fails closed before side effects.
"""
from __future__ import annotations

from langgraph_langchain.execution.task_models import TaskExecutionRequest, build_execution_result
from langgraph_langchain.runtime.capability_adapter import ToolCallBudget
from langgraph_langchain.runtime.executor import TaskExecutor
from langgraph_langchain.schemas import AnalysisTask


def _request(method: str | None, executor_type: str) -> TaskExecutionRequest:
    return TaskExecutionRequest(
        task=AnalysisTask(
            session_id="session",
            question="budget probe",
            method=method,
            task_type="profile",
            executor_type=executor_type,
        ),
        run_id="run_1",
        arguments={},
    )


class RecordingStructured:
    def __init__(self):
        self.executed = 0

    def execute(self, request):
        self.executed += 1
        return build_execution_result(
            request,
            status="succeeded",
            tool_name=request.task.method or request.task.task_type,
            stdout_preview="ok",
            duration_ms=0.0,
        )


class RecordingReact:
    def __init__(self):
        self.executed = 0

    def execute(self, request):
        self.executed += 1
        return build_execution_result(
            request,
            status="succeeded",
            tool_name=request.task.method or f"{request.task.executor_type}_task",
            stdout_preview="react ok",
            duration_ms=0.0,
        )


class RecordingPython:
    def __init__(self):
        self.executed = 0

    def execute(self, request):
        self.executed += 1
        return build_execution_result(
            request,
            status="succeeded",
            tool_name=request.task.method or f"{request.task.executor_type}_task",
            stdout_preview="python ok",
            duration_ms=0.0,
        )


def test_unified_budget_counts_all_three_executor_types():
    budget = ToolCallBudget(max_calls=3)
    structured = RecordingStructured()
    react = RecordingReact()
    python = RecordingPython()
    dispatcher = TaskExecutor(
        structured=structured,
        react=react,
        python=python,
        call_budget=budget,
    )

    assert dispatcher.execute(_request("load_data", "structured")).status == "succeeded"
    assert dispatcher.execute(_request("react_deep_dive", "react")).status == "succeeded"
    assert dispatcher.execute(_request("python_repl", "python")).status == "succeeded"

    assert structured.executed == 1
    assert react.executed == 1
    assert python.executed == 1
    snapshot = budget.snapshot()
    assert snapshot["used_calls"] == 3
    assert snapshot["remaining_calls"] == 0
    assert [item["tool_name"] for item in snapshot["history"]] == [
        "load_data",
        "react_deep_dive",
        "python_repl",
    ]


def test_budget_exhaustion_blocks_python_side_effect_and_records_failure():
    budget = ToolCallBudget(max_calls=1)
    budget.consume("load_data")
    python = RecordingPython()
    dispatcher = TaskExecutor(
        structured=RecordingStructured(),
        react=RecordingReact(),
        python=python,
        call_budget=budget,
    )
    result = dispatcher.execute(_request("python_repl", "python"))
    assert result.status == "failed"
    assert result.error["type"] == "CapabilityBudgetExceededError"
    assert "budget exhausted" in result.error["message"]
    assert python.executed == 0


def test_surface_restriction_blocks_non_allowlisted_executor_call():
    budget = ToolCallBudget(max_calls=5, allowed_tools=frozenset({"load_data"}))
    react = RecordingReact()
    dispatcher = TaskExecutor(
        structured=RecordingStructured(),
        react=react,
        python=RecordingPython(),
        call_budget=budget,
    )
    result = dispatcher.execute(_request("react_deep_dive", "react"))
    assert result.status == "failed"
    assert result.error["type"] == "CapabilityBudgetExceededError"
    assert "not enabled" in result.error["message"]
    assert react.executed == 0


def test_unified_budget_failure_does_not_count_against_budget():
    """A blocked call must not consume budget; state stays auditable."""
    budget = ToolCallBudget(max_calls=1, allowed_tools=frozenset({"load_data"}))
    dispatcher = TaskExecutor(
        structured=RecordingStructured(),
        react=RecordingReact(),
        python=RecordingPython(),
        call_budget=budget,
    )
    blocked = dispatcher.execute(_request("react_deep_dive", "react"))
    allowed = dispatcher.execute(_request("load_data", "structured"))
    assert blocked.status == "failed"
    assert allowed.status == "succeeded"
    assert budget.snapshot()["calls_by_tool"] == {"load_data": 1}

def test_concurrent_consumers_cannot_over_admit():
    """F-1 regression: atomic check-and-increment under concurrent consumers.

    Without the internal lock, N threads racing consume() on a budget of size
    M can admit more than M calls (check-then-increment race). With the lock,
    exactly M calls succeed regardless of scheduling.
    """
    import threading

    budget = ToolCallBudget(max_calls=50)
    admitted: list[int] = []
    errors: list[str] = []
    barrier = threading.Barrier(8)

    def worker():
        barrier.wait()
        while True:
            try:
                budget.consume("load_data")
                admitted.append(1)
            except Exception as exc:  # noqa: BLE001 - test collects all outcomes
                errors.append(type(exc).__name__)
                return

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(admitted) == 50
    assert len(errors) >= 1  # over-budget consumers were rejected, not admitted
    snapshot = budget.snapshot()
    assert snapshot["used_calls"] == 50
    assert snapshot["remaining_calls"] == 0
    assert snapshot["calls_by_tool"] == {"load_data": 50}
    assert len(snapshot["history"]) == 50

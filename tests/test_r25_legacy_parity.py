from __future__ import annotations

import pytest

from langgraph_langchain.runtime.capability_adapter import (
    CapabilityBudgetExceededError,
    ToolCallBudget,
)
from langgraph_langchain.execution.task_models import TaskExecutionRequest
from langgraph_langchain.execution.structured_executor import StructuredTaskExecutor
from langgraph_langchain.schemas import AnalysisTask


class FakeTool:
    name = "fake_tool"

    def invoke(self, arguments):
        return {"ok": True, "arguments": arguments}


def _request(method: str = "fake_tool") -> TaskExecutionRequest:
    return TaskExecutionRequest(
        task=AnalysisTask(session_id="session", question="parity probe", method=method),
        run_id="run_1",
        arguments={"value": 1},
    )


def test_tool_budget_tracks_history_and_fails_closed():
    budget = ToolCallBudget(max_calls=2)
    assert budget.consume("load_data") == 1
    assert budget.consume("python_repl") == 1
    with pytest.raises(CapabilityBudgetExceededError, match="budget exhausted"):
        budget.consume("compare_groups")
    snapshot = budget.snapshot()
    assert snapshot["used_calls"] == 2
    assert snapshot["remaining_calls"] == 0
    assert [item["tool_name"] for item in snapshot["history"]] == [
        "load_data",
        "python_repl",
    ]


def test_tool_budget_can_restrict_capability_surface():
    budget = ToolCallBudget(max_calls=4, allowed_tools=frozenset({"load_data"}))
    budget.consume("load_data")
    with pytest.raises(CapabilityBudgetExceededError, match="not enabled"):
        budget.consume("python_repl")


def test_structured_executor_rejects_budget_exhaustion_before_tool_side_effect():
    calls = []

    class CountingTool(FakeTool):
        def invoke(self, arguments):
            calls.append(arguments)
            return super().invoke(arguments)

    budget = ToolCallBudget(max_calls=1)
    budget.consume("fake_tool")
    executor = StructuredTaskExecutor(tools={"fake_tool": CountingTool()}, call_budget=budget)
    with pytest.raises(CapabilityBudgetExceededError):
        executor.execute(_request())
    assert calls == []


def test_structured_executor_records_each_legacy_tool_call():
    budget = ToolCallBudget(max_calls=3)
    executor = StructuredTaskExecutor(tools={"fake_tool": FakeTool()}, call_budget=budget)
    first = executor.execute(_request())
    second = executor.execute(_request())
    assert first.status == "succeeded"
    assert second.status == "succeeded"
    assert budget.snapshot()["calls_by_tool"] == {"fake_tool": 2}
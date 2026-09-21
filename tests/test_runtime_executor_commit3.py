from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest
from pydantic import ValidationError

from langgraph_langchain.execution.python_task_executor import PythonTaskExecutor
from langgraph_langchain.execution.react_executor import ReactTaskExecutor
from langgraph_langchain.execution.structured_executor import StructuredTaskExecutor
from langgraph_langchain.execution.task_models import TaskExecutionRequest
from langgraph_langchain.runtime.executor import TaskExecutor
from langgraph_langchain.runtime.models import ExecutionResult
from langgraph_langchain.schemas import AnalysisTask


def make_task(
    task_id: str = "task_1",
    *,
    executor_type: str = "structured",
    method: str | None = "compare_groups",
    question: str = "Compare groups",
) -> AnalysisTask:
    return AnalysisTask(
        task_id=task_id,
        session_id="session_1",
        question=question,
        task_type="comparison",
        executor_type=executor_type,  # type: ignore[arg-type]
        method=method,
    )


def make_request(
    task: AnalysisTask,
    *,
    run_id: str = "run_1",
    **kwargs: Any,
) -> TaskExecutionRequest:
    return TaskExecutionRequest(task=task, run_id=run_id, **kwargs)


class FakeTool:
    def __init__(self, result: Any) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    def invoke(self, arguments: dict[str, Any]) -> Any:
        self.calls.append(arguments)
        return self.result


@dataclass
class FakeRawPythonResult:
    status: str = "succeeded"
    stdout: str = "done"
    stderr: str = ""
    error_type: str | None = None
    error_message: str | None = None
    duration_ms: float = 2.0
    namespace_updates: dict[str, Any] = field(default_factory=dict)
    artifacts: list[dict[str, Any]] = field(default_factory=list)

    @property
    def output(self) -> str:
        return self.stdout


class FakeRawPythonExecutor:
    def __init__(self, result: FakeRawPythonResult) -> None:
        self.result = result
        self.requests = []

    def execute(self, request: Any) -> FakeRawPythonResult:
        self.requests.append(request)
        return self.result


class FakeAgent:
    def invoke(self, value: dict[str, Any], config: dict[str, Any] | None = None):
        self.value = value
        self.config = config
        return {
            "messages": [
                type("FakeMessage", (), {"content": "react complete"})()
            ]
        }


def test_execution_result_supports_task_id_and_round_trip():
    result = ExecutionResult(
        run_id="run_1",
        task_id="task_1",
        tool_name="compare_groups",
        status="succeeded",
    )

    payload = result.model_dump()
    restored = ExecutionResult.model_validate(payload)

    assert restored == result
    assert restored.task_id == "task_1"


def test_structured_executor_invokes_tool_and_extracts_artifact():
    tool = FakeTool(
        json.dumps(
            {
                "status": "ok",
                "artifact_id": "artifact_1",
                "summary": "North is lower than South",
            }
        )
    )
    executor = StructuredTaskExecutor(tools={"compare_groups": tool})
    task = make_task()
    request = make_request(task, arguments={"dimension": "department"})
    result = executor.execute(request)

    assert result.status == "succeeded"
    assert result.task_id == "task_1"
    assert result.tool_name == "compare_groups"
    assert result.output_artifact_ids == ["artifact_1"]
    assert "North is lower than South" in result.stdout_preview
    assert tool.calls == [{"dimension": "department"}]
    assert ExecutionResult.model_validate(result.model_dump()) == result


def test_structured_executor_returns_failed_for_unknown_tool():
    executor = StructuredTaskExecutor(tools={})
    result = executor.execute(make_request(make_task(method="missing_tool")))

    assert result.status == "failed"
    assert result.error == {
        "type": "ValueError",
        "message": "Unknown structured tool: missing_tool",
    }


def test_structured_executor_returns_failed_when_tool_raises():
    def failing_tool(name: str):
        def invoke(arguments: dict[str, Any]) -> Any:
            raise RuntimeError("bad column")

        return type("FailingTool", (), {"invoke": staticmethod(invoke)})()

    executor = StructuredTaskExecutor(
        tool_resolver=lambda name: failing_tool(name)
    )
    result = executor.execute(make_request(make_task()))

    assert result.status == "failed"
    assert result.error is not None
    assert result.error["message"] == "bad column"


def test_react_executor_wraps_existing_agent():
    agent = FakeAgent()
    executor = ReactTaskExecutor(agent=agent)
    task = make_task(executor_type="react", method=None)
    request = make_request(task, config={"recursion_limit": 10})
    result = executor.execute(request)

    assert result.status == "succeeded"
    assert result.tool_name == "react_task"
    assert result.stdout_preview == "react complete"
    assert agent.config == {"recursion_limit": 10}
    assert agent.value["messages"][0].content == "Compare groups"


def test_react_executor_returns_failed_when_agent_raises():
    class FailingAgent:
        def invoke(self, value: dict[str, Any], config=None):
            raise RuntimeError("model unavailable")

    executor = ReactTaskExecutor(agent=FailingAgent())
    result = executor.execute(make_request(make_task(executor_type="react")))

    assert result.status == "failed"
    assert result.error is not None
    assert result.error["message"] == "model unavailable"


def test_python_task_executor_wraps_isolated_backend(tmp_path):
    raw = FakeRawPythonResult(
        artifacts=[{"name": "result.csv", "path": str(tmp_path / "result.csv")}]
    )
    backend = FakeRawPythonExecutor(raw)
    recorded: list[dict[str, Any]] = []

    def recorder(metadata: dict[str, Any]) -> dict[str, Any]:
        recorded.append(metadata)
        return {"artifact_id": "artifact_python"}

    executor = PythonTaskExecutor(
        executor=backend,
        workspace_dir=tmp_path,
        artifact_recorder=recorder,
    )
    task = make_task(
        executor_type="python",
        method=None,
    )
    task.constraints["code"] = "print('done')"
    request = make_request(task, timeout_seconds=10)
    result = executor.execute(request)

    assert result.status == "succeeded"
    assert result.output_artifact_ids == ["artifact_python"]
    assert result.duration_ms == 2.0
    assert recorded == [raw.artifacts[0]]
    assert backend.requests[0].code == "print('done')"
    assert backend.requests[0].workspace_dir == str(tmp_path)


def test_python_task_executor_requires_code(tmp_path):
    executor = PythonTaskExecutor(executor=FakeRawPythonExecutor(FakeRawPythonResult()))
    result = executor.execute(make_request(make_task(executor_type="python")))

    assert result.status == "failed"
    assert result.error is not None
    assert result.error["message"] == "Python task has no code"


def test_task_executor_dispatches_by_executor_type():
    structured = StructuredTaskExecutor(
        tools={"compare_groups": FakeTool("structured ok")}
    )
    react = ReactTaskExecutor(
        agent_runner=lambda request: {"output": "react ok"}
    )
    python = PythonTaskExecutor(
        executor=FakeRawPythonExecutor(FakeRawPythonResult()),
        workspace_dir="/tmp",
    )

    dispatcher = TaskExecutor(structured=structured, react=react, python=python)

    structured_result = dispatcher.execute(make_request(make_task()))
    react_result = dispatcher.execute(make_request(make_task(executor_type="react", method=None)))
    python_task = make_task(executor_type="python", method=None)
    python_task.constraints["code"] = "print('ok')"
    python_result = dispatcher.execute(make_request(python_task))

    assert structured_result.status == "succeeded"
    assert structured_result.stdout_preview == "structured ok"
    assert react_result.status == "succeeded"
    assert react_result.stdout_preview == "react ok"
    assert python_result.status == "succeeded"
    assert python_result.stdout_preview == "done"


def test_analysis_task_rejects_unknown_executor_before_dispatch():
    with pytest.raises(ValidationError):
        make_task(executor_type="unknown")


def test_task_executor_returns_failed_when_backend_is_missing():
    dispatcher = TaskExecutor()
    result = dispatcher.execute(make_request(make_task()))

    assert result.status == "failed"
    assert result.error is not None
    assert result.error["message"] == "Structured executor is not configured"


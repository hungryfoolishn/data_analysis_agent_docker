from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from langgraph_langchain.execution.react_executor import ReactTaskExecutor
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


class StreamingFakeAgent:
    """Deterministic fake ReAct agent that streams tokens exactly once."""

    def __init__(self) -> None:
        self.astream_calls = 0
        self.invoke_calls = 0

    def astream_events(self, invoke_kwargs, **kwargs):
        self.astream_calls += 1
        assert kwargs.get("version") == "v2"

        async def _gen():
            yield {
                "event": "on_chat_model_stream",
                "data": {"chunk": SimpleNamespace(content="Hello")},
            }
            yield {
                "event": "on_chat_model_stream",
                "data": {"chunk": SimpleNamespace(content=" world")},
            }
            yield {
                "event": "on_chat_model_end",
                "data": {"chunk": SimpleNamespace(content="Hello world")},
            }

        return _gen()


class BrokenStreamingFakeAgent(StreamingFakeAgent):
    def astream_events(self, invoke_kwargs, **kwargs):
        self.astream_calls += 1

        async def _gen():
            yield {
                "event": "on_chat_model_stream",
                "data": {"chunk": SimpleNamespace(content="partial")},
            }
            raise RuntimeError("stream boom")

        return _gen()


class FakeReactTool:
    """Placeholder tool entry so the react-prefixed plan method validates."""

    name = "react_demo"



class FakePythonTool:
    """Placeholder python_repl entry for plan validation (execution uses the
    real PythonTaskExecutor)."""

    name = "python_repl"


def build_streaming_plan() -> AnalysisPlan:
    react_step = RuntimePlanStep(
        objective="Stream a react analysis",
        method="react_demo",
        expected_outputs=["react_result"],
    )
    python_step = RuntimePlanStep(
        objective="Run a python step",
        method="python_repl",
        expected_outputs=["analysis_output"],
        depends_on=[react_step.step_id],
    )
    plan = AnalysisPlan(
        goal="SSE streaming E2E",
        success_criteria=["React tokens stream incrementally"],
        metrics=[],
        dimensions=[],
        steps=[react_step, python_step],
    )
    plan.steps[1].parameters["code"] = "print('python step done')"
    return plan


def make_session(workspace: Path, source: Path) -> _Session:
    session = _Session(
        workspace_dir=str(workspace),
        source_path=str(source),
        session_id="runtime_v2_sse_e2e",
        user_question="SSE streaming check",
    )
    runtime = session.analysis_runtime
    assert runtime is not None
    runtime.initialize_plan(build_streaming_plan(), max_running_tasks=1)
    return session


def consume_stream(session: _Session, source: Path, agent) -> list[str]:
    async def run() -> list[str]:
        chunks = []
        async for chunk, artifacts in _run_runtime_v2_graph_stream(
            session,
            tools=[FakeReactTool(), FakePythonTool()],
            source_path=str(source),
            react_agent=agent,
        ):
            chunks.append(chunk)
        return chunks

    return asyncio.run(run())


def test_react_tokens_stream_incrementally_in_order(tmp_path: Path):
    workspace = tmp_path / "sse_stream"
    workspace.mkdir(parents=True)
    source = write_sales_csv(workspace)
    session = make_session(workspace, source)
    agent = StreamingFakeAgent()

    chunks = consume_stream(session, source, agent)

    # Token deltas arrive as separate stream events, in order.
    assert "Hello" in chunks
    assert " world" in chunks
    assert chunks.index("Hello") < chunks.index(" world")
    # Tokens precede the follow-up python task preview.
    assert any("python_repl done" in chunk for chunk in chunks)
    assert chunks.index(" world") < next(
        index for index, chunk in enumerate(chunks) if "python_repl done" in chunk
    )
    # Exactly one agent graph execution: no duplicate model/tool runs.
    assert agent.astream_calls == 1
    # The streamed react task must not repeat its output as a preview.
    assert not any("`react_demo done`" in chunk for chunk in chunks)
    # Tokens are recorded in the session process log (V1 parity).
    assert "Hello" in session.process_log
    assert " world" in session.process_log


def test_non_streamed_tasks_keep_their_preview(tmp_path: Path):
    workspace = tmp_path / "sse_preview"
    workspace.mkdir(parents=True)
    source = write_sales_csv(workspace)
    session = make_session(workspace, source)
    agent = StreamingFakeAgent()

    chunks = consume_stream(session, source, agent)

    # The python task did not stream tokens, so its preview is preserved.
    preview_chunks = [chunk for chunk in chunks if "python_repl done" in chunk]
    assert preview_chunks
    assert "python step done" in preview_chunks[0]


def test_react_executor_streaming_unit_contract():
    agent = StreamingFakeAgent()
    received: list[str] = []
    executor = ReactTaskExecutor(
        agent=agent,
        token_callback=received.append,
        prompt_builder=lambda request: "prompt",
    )
    task = AnalysisTask(
        task_id="react_stream_task",
        session_id="react_stream_session",
        question="stream",
        task_type="metric",
        executor_type="react",
        method="react_demo",
        plan_step_id="react_stream_step",
        status="pending",
    )
    request = TaskExecutionRequest(task=task, run_id="run_react_stream")
    result = executor.execute(request)

    assert received == ["Hello", " world"]
    assert executor.streamed_output is True
    assert result.status == "succeeded"
    assert result.stdout_preview == "Hello world"
    assert agent.astream_calls == 1
    assert agent.invoke_calls == 0


def test_react_executor_streaming_error_is_reported_not_completed(tmp_path: Path):
    workspace = tmp_path / "sse_error"
    workspace.mkdir(parents=True)
    source = write_sales_csv(workspace)
    session = make_session(workspace, source)
    agent = BrokenStreamingFakeAgent()

    chunks = consume_stream(session, source, agent)

    assert any("`react_demo failed`" in chunk for chunk in chunks)
    assert any("stream boom" in chunk for chunk in chunks)
    assert not any("Analysis cancelled" in chunk for chunk in chunks)
    statuses = [task.status for task in session.analysis_runtime.scheduler.tasks]
    assert statuses[0] == "failed"

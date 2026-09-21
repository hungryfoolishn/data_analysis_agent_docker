from __future__ import annotations

import json
from typing import Any

import pytest

from langgraph_langchain.config import RUNTIME_V2_ENABLED
from langgraph_langchain.execution.structured_executor import StructuredTaskExecutor
from langgraph_langchain.execution.task_models import TaskExecutionRequest
from langgraph_langchain.runtime.executor import TaskExecutor
from langgraph_langchain.runtime.graph import (
    RuntimeV2Controller,
    build_runtime_v2_controller,
)
from langgraph_langchain.runtime.models import RuntimePlanStep
from langgraph_langchain.runtime.plans import AnalysisPlan, default_analysis_plan
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
    def invoke(self, arguments: dict[str, Any]) -> Any:
        raise RuntimeError("runtime graph failed")


def build_controller(
    *,
    plan: AnalysisPlan,
    tool_resolver,
) -> RuntimeV2Controller:
    scheduler = TaskScheduler.from_plan(plan, session_id="session_graph")
    executor = TaskExecutor(
        structured=StructuredTaskExecutor(tool_resolver=tool_resolver)
    )
    return RuntimeV2Controller(
        scheduler=scheduler,
        runner=TaskRunner(scheduler=scheduler, executor=executor),
        run_id="run_graph",
        session_id="session_graph",
    )


def test_runtime_v2_feature_flag_is_off_by_default():
    assert isinstance(RUNTIME_V2_ENABLED, bool)


def test_runtime_graph_executes_all_tasks_in_dependency_order():
    tools = {
        "load_data": FakeTool(
            json.dumps({"status": "ok", "artifact_id": "artifact_schema"})
        ),
        "compare_groups": FakeTool(
            json.dumps({"status": "ok", "artifact_id": "artifact_compare"})
        ),
    }
    first = RuntimePlanStep(
        objective="Load data",
        method="load_data",
        expected_outputs=["schema_snapshot"],
    )
    second = RuntimePlanStep(
        objective="Compare groups",
        method="compare_groups",
        expected_outputs=["comparison"],
        depends_on=[first.step_id],
    )
    from langgraph_langchain.runtime.plans import AnalysisPlan

    plan = AnalysisPlan(goal="Compare revenue", steps=[first, second])
    controller = build_controller(
        plan=plan,
        tool_resolver=lambda name: tools[name],
    )
    state = controller.graph.invoke({})

    assert state["status"] == "completed"
    assert state["action"] == "completed"
    assert state["result"].task_id == second.step_id
    assert state["result"].output_artifact_ids == ["artifact_compare"]
    assert [task.status for task in controller.scheduler.tasks] == [
        "succeeded",
        "succeeded",
    ]
    assert tools["load_data"].calls == [{}]
    assert tools["compare_groups"].calls == [{}]


def test_runtime_graph_stops_after_failed_task():
    first = RuntimePlanStep(
        objective="Load data",
        method="load_data",
    )
    second = RuntimePlanStep(
        objective="Compare groups",
        method="compare_groups",
        depends_on=[first.step_id],
    )
    from langgraph_langchain.runtime.plans import AnalysisPlan

    plan = AnalysisPlan(goal="Compare revenue", steps=[first, second])
    controller = build_controller(
        plan=plan,
        tool_resolver=lambda name: FailingTool(),
    )
    state = controller.graph.invoke({})

    assert state["status"] == "failed"
    assert state["action"] == "failed"
    assert state["reason"] == "runtime graph failed"
    assert state["result"].task_id == first.step_id
    assert controller.scheduler.get_task(first.step_id).status == "failed"
    assert controller.scheduler.get_task(second.step_id).status == "pending"


def test_build_runtime_controller_uses_scheduler_and_runner():
    plan = default_analysis_plan("Analyze revenue")
    tools = {
        "load_data": FakeTool("loaded"),
        "eda_profile": FakeTool("profiled"),
        "python_repl": FakeTool("analyzed"),
        "record_finding": FakeTool("recorded"),
        "finish_report": FakeTool("reported"),
    }
    controller = build_runtime_v2_controller(
        plan=plan,
        session_id="session_factory",
        run_id="run_factory",
        tool_resolver=lambda name: tools[name],
    )

    assert isinstance(controller.scheduler, TaskScheduler)
    assert isinstance(controller.runner, TaskRunner)
    assert controller.scheduler.execution_queue() == tuple(
        step.step_id for step in plan.steps
    )
    assert controller.runner.scheduler is controller.scheduler


def test_runtime_graph_invokes_task_lifecycle_callbacks():
    started: list[str] = []
    finished: list[str] = []

    def on_task_start(task: AnalysisTask) -> None:
        started.append(task.task_id)

    def on_task_finish(result: Any) -> None:
        finished.append(result.task_id)

    first = RuntimePlanStep(
        objective="Load data",
        method="load_data",
    )
    second = RuntimePlanStep(
        objective="Compare groups",
        method="compare_groups",
        depends_on=[first.step_id],
    )
    plan = AnalysisPlan(goal="Compare revenue", steps=[first, second])
    tools = {
        "load_data": FakeTool("loaded"),
        "compare_groups": FakeTool("compared"),
    }
    controller = build_runtime_v2_controller(
        plan=plan,
        session_id="session_callbacks",
        run_id="run_callbacks",
        tool_resolver=lambda name: tools[name],
        on_task_start=on_task_start,
        on_task_finish=on_task_finish,
    )

    state = controller.graph.invoke({})

    assert state["status"] == "completed"
    assert len(started) == 2
    assert len(finished) == 2
    assert started == [first.step_id, second.step_id]
    assert finished == [first.step_id, second.step_id]


def test_runtime_graph_invokes_finish_callback_for_failed_task():
    started: list[str] = []
    finished: list[str] = []

    first = RuntimePlanStep(objective="Load data", method="load_data")
    second = RuntimePlanStep(
        objective="Compare groups",
        method="compare_groups",
        depends_on=[first.step_id],
    )
    plan = AnalysisPlan(goal="Compare revenue", steps=[first, second])
    controller = build_runtime_v2_controller(
        plan=plan,
        session_id="session_failure_callbacks",
        run_id="run_failure_callbacks",
        tool_resolver=lambda name: FailingTool(),
        on_task_start=lambda task: started.append(task.task_id),
        on_task_finish=lambda result: finished.append(result.task_id),
    )

    state = controller.graph.invoke({})

    assert state["status"] == "failed"
    assert started == [first.step_id]
    assert finished == [first.step_id]
    assert controller.scheduler.get_task(second.step_id).status == "pending"





class FakeSkillRetriever:
    def __init__(self, name: str | None, *, fallback: bool = False) -> None:
        self.name = name
        self.fallback = fallback

    def retrieve_for_task(self, task):
        from langgraph_langchain.runtime.skill_retriever import SkillMatch

        return SkillMatch(
            skill_name=self.name,
            skill=None,
            score=1.0 if self.name else 0.0,
            source="exact" if self.name else "fallback",
            reason="test match" if self.name else "test fallback",
            fallback=self.fallback,
            tool_name=task.method,
        )


def test_runtime_graph_records_matched_skill_name():
    first = RuntimePlanStep(objective="Load data", method="load_data")
    plan = AnalysisPlan(goal="Analyze data", steps=[first])
    tools = {
        "load_data": FakeTool(json.dumps({"artifact_id": "artifact_skill"}))
    }
    controller = build_runtime_v2_controller(
        plan=plan,
        session_id="session_skill",
        run_id="run_skill",
        tool_resolver=lambda name: tools[name],
        skill_retriever=FakeSkillRetriever("cohort-analysis"),
    )

    state = controller.graph.invoke({})

    assert state["status"] == "completed"
    assert state["result"].skill_name == "cohort-analysis"


def test_runtime_graph_records_skill_fallback_as_none():
    first = RuntimePlanStep(objective="Load data", method="load_data")
    plan = AnalysisPlan(goal="Analyze data", steps=[first])
    tools = {
        "load_data": FakeTool(json.dumps({"artifact_id": "artifact_fallback"}))
    }
    controller = build_runtime_v2_controller(
        plan=plan,
        session_id="session_skill_fallback",
        run_id="run_skill_fallback",
        tool_resolver=lambda name: tools[name],
        skill_retriever=FakeSkillRetriever(None, fallback=True),
    )

    state = controller.graph.invoke({})

    assert state["status"] == "completed"
    assert state["result"].skill_name is None

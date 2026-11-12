"""B11.2.1 v3 §7.1/§7.2: runtime input resolution and failure semantics."""

from __future__ import annotations

import pytest

from langgraph_langchain.runtime.context import BindingResolutionError
from langgraph_langchain.runtime.executor import TaskExecutor
from langgraph_langchain.runtime.graph import RuntimeV2Controller
from langgraph_langchain.runtime.models import OutputAssetBinding
from langgraph_langchain.runtime.runner import TaskRunner
from langgraph_langchain.execution.structured_executor import StructuredTaskExecutor
from langgraph_langchain.runtime.scheduler import TaskScheduler

from tests.output_binding_common import (
    STEP_A,
    STEP_B,
    STEP_C,
    build_plan,
    make_result,
    make_runtime,
    make_step,
    register_asset,
)


class _RecordingExecutor:
    def __init__(self):
        self.calls = []

    def execute(self, request):
        self.calls.append(request.task.task_id)
        raise AssertionError("tool must not be executed on binding resolution failure")


def _runtime_with_chain(tmp_path, name):
    runtime = make_runtime(tmp_path, name=name)
    step_a = make_step(STEP_A, method="load_data", expected=["output"])
    step_b = make_step(
        STEP_B,
        method="load_data",
        expected=["output"],
        depends=[STEP_A],
        required_inputs=[f"{STEP_A}.output"],
        parameters={"asset_id": f"{STEP_A}.output"},
    )
    runtime.initialize_plan(build_plan([step_a, step_b]))
    return runtime, step_a, step_b


def _publish_step_a(runtime, step_a, tmp_path):
    asset = register_asset(runtime, tmp_path, execution_id=None)
    result = make_result(
        runtime,
        step_a,
        input_asset_ids=[asset.asset_id],
    )
    # load_data fallback path binds the execution-linked asset.
    asset.source_metadata["execution_id"] = result.execution_id
    adjudicated = runtime.record_execution_result(result)
    assert adjudicated.status == "succeeded"
    binding = runtime.output_bindings.resolve(
        run_id=runtime.run.run_id,
        step_id=STEP_A,
        output_name="output",
        attempt=1,
    )
    return asset, binding


def test_logical_references_resolve_to_physical_assets(tmp_path):
    runtime, step_a, step_b = _runtime_with_chain(tmp_path, "req1")
    asset, binding = _publish_step_a(runtime, step_a, tmp_path)
    task_b = runtime.scheduler.get_task(STEP_B)
    request = runtime.build_execution_request(task_b)
    assert request.arguments["asset_id"] == binding.asset_id
    assert binding.asset_id in request.resolved_inputs.asset_ids
    assert len(request.resolved_inputs.logical_refs) == 2  # required_inputs + argument
    ref = request.resolved_inputs.logical_refs[0]
    assert ref.reference == f"{STEP_A}.output"
    assert ref.binding.asset_id == binding.asset_id


def test_missing_binding_fails_before_tool_execution(tmp_path):
    runtime, step_a, step_b = _runtime_with_chain(tmp_path, "req2")
    executor = _RecordingExecutor()
    runtime.configure_executor(TaskExecutor(structured=executor))
    # Step A succeeded without ever publishing its output binding.
    runtime.scheduler.mark_running(STEP_A)
    runtime.scheduler.mark_succeeded(STEP_A)
    current_asset_before = None

    result = runtime.execute_next_task()
    assert result.status == "failed"
    assert result.error["type"] == "BindingResolutionError"
    assert executor.calls == []
    task_b = runtime.scheduler.get_task(STEP_B)
    assert task_b.status == "failed"
    assert runtime.status == "failed"
    assert runtime.run.status == "failed"


def test_resolution_error_propagates_as_binding_resolution_error(tmp_path):
    runtime, step_a, step_b = _runtime_with_chain(tmp_path, "req3")
    task_b = runtime.scheduler.get_task(STEP_B)
    with pytest.raises(BindingResolutionError):
        runtime.build_execution_request(task_b)


def test_runtime_rejects_reference_outside_dependency_closure(tmp_path):
    runtime = make_runtime(tmp_path, name="req4")
    step_a = make_step(STEP_A, method="load_data", expected=["output"])
    step_c = make_step(
        STEP_C,
        method="load_data",
        expected=["output"],
        depends=[],
        required_inputs=[f"{STEP_A}.output"],
    )
    # Plan-time validation would reject this; exercise the runtime check by
    # wiring run steps directly.
    runtime.run.steps = [step_a, step_c]
    from langgraph_langchain.schemas import AnalysisTask as _Task

    task_c = _Task(
        task_id=STEP_C,
        session_id=runtime.session_id,
        question="q",
        depends_on=[],
        required_inputs=[f"{STEP_A}.output"],
    )
    with pytest.raises(BindingResolutionError):
        runtime._resolve_task_inputs(task_c)


def test_controller_request_factory_preserves_runtime_metadata(tmp_path):
    runtime = make_runtime(tmp_path, name="req5")
    step = make_step(STEP_A, expected=["output"])
    runtime.initialize_plan(build_plan([step]))

    class _StubRuntime:
        def build_execution_request(self, task):
            request = runtime.build_execution_request(task)
            request.metadata["asset_resolution_error"] = {
                "type": "AssetResolutionError",
                "message": "boom",
            }
            return request

    scheduler = runtime.scheduler
    runner = TaskRunner(
        scheduler=scheduler,
        executor=TaskExecutor(
            structured=StructuredTaskExecutor(tool_resolver=lambda name: None)
        ),
    )
    controller = RuntimeV2Controller(
        scheduler=scheduler,
        runner=runner,
        run_id=runtime.run.run_id,
        session_id=runtime.session_id,
        analysis_runtime=_StubRuntime(),
    )
    task = scheduler.get_task(STEP_A)
    request = controller._request_factory(task)
    assert "asset_resolution_error" in request.metadata

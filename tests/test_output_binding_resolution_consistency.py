"""B11.2.1.1: logical reference resolution consistency across entry points."""

from __future__ import annotations

import json

from langgraph_langchain.execution.structured_executor import StructuredTaskExecutor
from langgraph_langchain.runtime.executor import TaskExecutor

from tests.output_binding_common import (
    STEP_A,
    STEP_B,
    build_plan,
    make_result,
    make_runtime,
    make_step,
    register_asset,
)


class _RecordingTool:
    def __init__(self):
        self.calls = []

    def invoke(self, arguments):
        self.calls.append(dict(arguments))
        return "ok"


def _runtime_with_chain(tmp_path, name, *, step_b_params):
    runtime = make_runtime(tmp_path, name=name)
    step_a = make_step(STEP_A, method="load_data", expected=["output"])
    step_b = make_step(
        STEP_B,
        method="load_data",
        expected=["output"],
        depends=[STEP_A],
        required_inputs=[f"{STEP_A}.output"],
        parameters=step_b_params,
    )
    plan = build_plan([step_a, step_b])
    plan.input_asset_ids = []
    runtime.initialize_plan(plan)
    return runtime, step_a, step_b


def _publish_step_a(runtime, step_a, tmp_path):
    """Register a derived output asset distinct from any run-level input."""
    output_asset = register_asset(runtime, tmp_path, execution_id=None)
    result = make_result(runtime, step_a, input_asset_ids=[])
    output_asset.source_metadata["execution_id"] = result.execution_id
    adjudicated = runtime.record_execution_result(result)
    assert adjudicated.status == "succeeded"
    binding = runtime.output_bindings.resolve(
        run_id=runtime.run.run_id,
        step_id=STEP_A,
        output_name="output",
        attempt=1,
    )
    return output_asset, binding


def test_parameters_json_logical_ref_resolved_in_request(tmp_path):
    runtime, step_a, step_b = _runtime_with_chain(
        tmp_path,
        "cons1",
        step_b_params={
            "parameters_json": json.dumps({"asset_id": f"{STEP_A}.output"})
        },
    )
    output_asset, binding = _publish_step_a(runtime, step_a, tmp_path)
    task_b = runtime.scheduler.get_task(STEP_B)
    request = runtime.build_execution_request(task_b)

    rewritten = json.loads(request.arguments["parameters_json"])
    assert rewritten["asset_id"] == binding.asset_id
    assert binding.asset_id in request.resolved_inputs.asset_ids


def test_missing_binding_in_parameters_json_fails_before_tool(tmp_path):
    runtime, step_a, step_b = _runtime_with_chain(
        tmp_path,
        "cons2",
        step_b_params={
            "parameters_json": json.dumps({"asset_id": f"{STEP_A}.output"})
        },
    )
    tool = _RecordingTool()
    runtime.configure_executor(
        TaskExecutor(structured=StructuredTaskExecutor(tool_resolver=lambda n: tool))
    )
    runtime.scheduler.mark_running(STEP_A)
    runtime.scheduler.mark_succeeded(STEP_A)

    result = runtime.execute_next_task()

    assert result.status == "failed"
    assert result.error["type"] == "BindingResolutionError"
    assert tool.calls == []


def test_downstream_lineage_excludes_unconsumed_initial_assets(tmp_path):
    runtime, step_a, step_b = _runtime_with_chain(
        tmp_path,
        "cons3",
        step_b_params={"asset_id": f"{STEP_A}.output"},
    )
    output_asset, binding = _publish_step_a(runtime, step_a, tmp_path)
    task_b = runtime.scheduler.get_task(STEP_B)
    request = runtime.build_execution_request(task_b)
    assert request.resolved_inputs.asset_ids == [binding.asset_id]

    result = make_result(runtime, step_b, input_asset_ids=request.input_asset_ids)
    adjudicated = runtime.record_execution_result(result)
    # The step consumed only the upstream output; the run-level initial asset
    # (if any) must not leak into ExecutionResult.input_asset_ids.
    assert adjudicated.input_asset_ids == [output_asset.asset_id]


def test_step_without_declared_inputs_inherits_run_level_assets(tmp_path):
    runtime = make_runtime(tmp_path, name="cons4")
    step_a = make_step(STEP_A, method="load_data", expected=["output"])
    plan = build_plan([step_a])
    source_asset = register_asset(runtime, tmp_path, execution_id=None)
    plan.input_asset_ids = [source_asset.asset_id]
    runtime.initialize_plan(plan)
    task_a = runtime.scheduler.get_task(STEP_A)
    request = runtime.build_execution_request(task_a)
    assert request.resolved_inputs.asset_ids == [source_asset.asset_id]

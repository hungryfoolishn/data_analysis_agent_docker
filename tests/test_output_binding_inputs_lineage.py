"""B11.2.1 v3 §7.4/§5.1: input lineage recorded and persisted via ExecutionResult."""

from __future__ import annotations

from langgraph_langchain.runtime.context import AnalysisRuntime
from langgraph_langchain.runtime.models import OutputAssetBinding

from tests.output_binding_common import (
    STEP_A,
    STEP_B,
    build_plan,
    make_result,
    make_runtime,
    make_step,
    register_asset,
)


def _runtime_with_published_output(tmp_path, name):
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
    asset = register_asset(runtime, tmp_path, execution_id=None)
    result_a = make_result(runtime, step_a, input_asset_ids=[asset.asset_id])
    asset.source_metadata["execution_id"] = result_a.execution_id
    adjudicated = runtime.record_execution_result(result_a)
    assert adjudicated.status == "succeeded"
    return runtime, asset


def test_result_records_resolved_inputs_and_bindings(tmp_path):
    runtime, asset = _runtime_with_published_output(tmp_path, "lineage1")
    task_b = runtime.scheduler.get_task(STEP_B)
    request = runtime.build_execution_request(task_b)
    assert request.resolved_inputs.asset_ids[0] == asset.asset_id

    from langgraph_langchain.execution.structured_executor import (
        StructuredTaskExecutor,
    )

    class _PassthroughTool:
        name = "fake_passthrough"

        def invoke(self, arguments):
            return "ok"

    executor = StructuredTaskExecutor(tool_resolver=lambda name: _PassthroughTool())
    result = executor.execute(request)
    # Resolved physical input (source asset) is authoritative, not the plan
    # template; the logical reference binding is preserved for audit.
    assert result.input_asset_ids == [asset.asset_id]
    assert len(result.input_bindings) == 2
    assert {ref.reference for ref in result.input_bindings} == {f"{STEP_A}.output"}
    assert result.input_bindings[0].binding.asset_id == asset.asset_id


def test_snapshot_persists_bindings_and_restore_roundtrip(tmp_path):
    runtime, asset = _runtime_with_published_output(tmp_path, "lineage2")
    raw = runtime.snapshot()
    assert raw["version"] == 2
    assert len(raw["output_bindings"]) == 1
    restored = AnalysisRuntime(
        workspace_dir=runtime.workspace_dir,
        session_id=runtime.session_id,
        question=runtime.task.question,
    )
    assert len(restored.output_bindings) == 1
    binding = restored.output_bindings.resolve(
        run_id=runtime.run.run_id,
        step_id=STEP_A,
        output_name="output",
        attempt=1,
    )
    assert binding.asset_id == asset.asset_id


def test_restore_drops_orphan_bindings(tmp_path):
    runtime, asset = _runtime_with_published_output(tmp_path, "lineage3")
    raw = runtime.snapshot()
    # Simulate a crash where the execution record lost its succeeded status.
    raw["executions"][0]["status"] = "failed"
    runtime.workspace_dir.joinpath(".analysis_runtime.json").write_text(
        __import__("json").dumps(raw, ensure_ascii=False), encoding="utf-8"
    )
    restored = AnalysisRuntime(
        workspace_dir=runtime.workspace_dir,
        session_id=runtime.session_id,
        question=runtime.task.question,
    )
    assert len(restored.output_bindings) == 0

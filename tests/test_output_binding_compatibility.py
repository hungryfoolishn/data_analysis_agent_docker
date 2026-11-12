"""B11.2.1 v3 §10: snapshot compatibility and legacy behavior."""

from __future__ import annotations

import json

from langgraph_langchain.runtime.context import AnalysisRuntime

from tests.output_binding_common import (
    STEP_A,
    build_plan,
    make_result,
    make_runtime,
    make_step,
    register_asset,
)


def _runtime_with_binding(tmp_path, name):
    runtime = make_runtime(tmp_path, name=name)
    step = make_step(STEP_A, method="load_data", expected=["output"])
    runtime.initialize_plan(build_plan([step]))
    asset = register_asset(runtime, tmp_path, execution_id=None)
    result = make_result(runtime, step, input_asset_ids=[asset.asset_id])
    asset.source_metadata["execution_id"] = result.execution_id
    adjudicated = runtime.record_execution_result(result)
    assert adjudicated.status == "succeeded"
    return runtime, asset, adjudicated


def test_legacy_v1_snapshot_without_bindings_restores(tmp_path):
    runtime, asset, result = _runtime_with_binding(tmp_path, "compat1")
    raw = runtime.snapshot()
    raw["version"] = 1
    raw.pop("output_bindings")
    runtime.workspace_dir.joinpath(".analysis_runtime.json").write_text(
        json.dumps(raw, ensure_ascii=False), encoding="utf-8"
    )
    restored = AnalysisRuntime(
        workspace_dir=runtime.workspace_dir,
        session_id=runtime.session_id,
        question=runtime.task.question,
    )
    assert len(restored.output_bindings) == 0
    assert restored.run.run_id == runtime.run.run_id


def test_v2_snapshot_restores_bindings(tmp_path):
    runtime, asset, result = _runtime_with_binding(tmp_path, "compat2")
    raw = runtime.snapshot()
    assert raw["version"] == 2
    runtime.workspace_dir.joinpath(".analysis_runtime.json").write_text(
        json.dumps(raw, ensure_ascii=False), encoding="utf-8"
    )
    restored = AnalysisRuntime(
        workspace_dir=runtime.workspace_dir,
        session_id=runtime.session_id,
        question=runtime.task.question,
    )
    binding = restored.output_bindings.resolve(
        run_id=runtime.run.run_id,
        step_id=STEP_A,
        output_name="output",
        attempt=1,
    )
    assert binding.asset_id == asset.asset_id


def test_legacy_tools_without_execution_id_field_unchanged(tmp_path):
    from langgraph_langchain.execution.structured_executor import (
        StructuredTaskExecutor,
    )
    from pydantic import BaseModel

    class _Args(BaseModel):
        value: str = ""

    class _Tool:
        args_schema = _Args

        def invoke(self, arguments):
            return "done"

    request_runtime = make_runtime(tmp_path, name="compat3")
    step = make_step(STEP_A, expected=["output"])
    request_runtime.initialize_plan(build_plan([step]))
    task = request_runtime.scheduler.get_task(STEP_A)
    request = request_runtime.build_execution_request(task)
    executor = StructuredTaskExecutor(tool_resolver=lambda name: _Tool())
    result = executor.execute(request)
    assert result.status == "succeeded"
    assert result.execution_id_source == "legacy"

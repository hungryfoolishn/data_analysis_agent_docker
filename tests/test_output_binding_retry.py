"""B11.2.1 v3 §8: retry/attempt isolation semantics."""

from __future__ import annotations

from langgraph_langchain.runtime.context import AnalysisRuntime
from langgraph_langchain.runtime.models import OutputAssetBinding

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
    step_a = make_step(STEP_A, method="load_data", expected=["output"])
    runtime.initialize_plan(build_plan([step_a]))
    asset = register_asset(runtime, tmp_path, execution_id=None)
    result = make_result(runtime, step_a, input_asset_ids=[asset.asset_id])
    asset.source_metadata["execution_id"] = result.execution_id
    adjudicated = runtime.record_execution_result(result)
    assert adjudicated.status == "succeeded"
    return runtime, asset, adjudicated


def test_retry_run_starts_with_empty_registry(tmp_path):
    runtime, asset, result = _runtime_with_binding(tmp_path, "retry1")
    parent_run_id = runtime.run.run_id
    retry = AnalysisRuntime(
        workspace_dir=runtime.workspace_dir,
        session_id=runtime.session_id,
        question=runtime.task.question,
        force_new_run=True,
        parent_run_id=parent_run_id,
        retry_of_step_id=STEP_A,
    )
    assert retry.run.attempt == 2
    assert retry.run.parent_run_id == parent_run_id
    assert len(retry.output_bindings) == 0


def test_parent_binding_archive_is_immutable_after_retry(tmp_path):
    runtime, asset, result = _runtime_with_binding(tmp_path, "retry2")
    parent_run_id = runtime.run.run_id
    AnalysisRuntime(
        workspace_dir=runtime.workspace_dir,
        session_id=runtime.session_id,
        question=runtime.task.question,
        force_new_run=True,
        parent_run_id=parent_run_id,
        retry_of_step_id=STEP_A,
    )
    archive = runtime.workspace_dir / ".analysis_runs" / f"{parent_run_id}.json"
    assert archive.exists()
    import json

    raw = json.loads(archive.read_text(encoding="utf-8"))
    assert len(raw["output_bindings"]) == 1
    assert raw["output_bindings"][0]["asset_id"] == asset.asset_id


def test_retry_bindings_use_current_attempt(tmp_path):
    runtime, asset, result = _runtime_with_binding(tmp_path, "retry3")
    retry = AnalysisRuntime(
        workspace_dir=runtime.workspace_dir,
        session_id=runtime.session_id,
        question=runtime.task.question,
        force_new_run=True,
        parent_run_id=runtime.run.run_id,
        retry_of_step_id=STEP_A,
    )
    step_a = make_step(STEP_A, method="load_data", expected=["output"])
    retry.initialize_plan(build_plan([step_a]))
    new_asset = register_asset(retry, tmp_path, execution_id=None)
    retry_result = make_result(retry, step_a, input_asset_ids=[new_asset.asset_id])
    new_asset.source_metadata["execution_id"] = retry_result.execution_id
    adjudicated = retry.record_execution_result(retry_result)
    assert adjudicated.status == "succeeded"
    binding = retry.output_bindings.resolve(
        run_id=retry.run.run_id,
        step_id=STEP_A,
        output_name="output",
        attempt=2,
    )
    assert binding.asset_id == new_asset.asset_id
    assert binding.execution_id == adjudicated.execution_id
    # Parent archive untouched.
    import json

    raw = json.loads(
        (retry.workspace_dir / ".analysis_runs" / f"{runtime.run.run_id}.json").read_text(
            encoding="utf-8"
        )
    )
    assert raw["output_bindings"][0]["attempt"] == 1

"""B11.2.1 v3 §5.3/§5.4/§5.6: named output extraction and adjudication."""

from __future__ import annotations

import json

import pytest

from langgraph_langchain.execution.task_models import (
    TaskExecutionRequest,
    build_execution_result,
    extract_output_assets,
)
from langgraph_langchain.runtime.models import ExecutionOutputAsset
from langgraph_langchain.schemas import AnalysisTask

from tests.output_binding_common import (
    STEP_A,
    build_plan,
    make_result,
    make_runtime,
    make_step,
    register_asset,
)


def _named(name: str, asset_id: str) -> ExecutionOutputAsset:
    return ExecutionOutputAsset(output_name=name, asset_id=asset_id)


def test_extract_output_assets_named_mapping():
    items = extract_output_assets(
        json.dumps({"output_assets": {"grouped": "asset_1", "summary": "asset_2"}})
    )
    assert [(item.output_name, item.asset_id) for item in items] == [
        ("grouped", "asset_1"),
        ("summary", "asset_2"),
    ]


def test_extract_output_assets_single_value_is_unnamed():
    items = extract_output_assets(json.dumps({"output_asset_id": "asset_1"}))
    assert items == [_named("", "asset_1")]


def test_extract_output_assets_consistent_single_value_ok():
    items = extract_output_assets(
        json.dumps({"output_asset_id": "asset_1", "output_assets": {"output": "asset_1"}})
    )
    assert items == [_named("output", "asset_1")]


def test_extract_output_assets_conflicting_single_value_raises():
    with pytest.raises(ValueError):
        extract_output_assets(
            json.dumps(
                {"output_asset_id": "asset_9", "output_assets": {"output": "asset_1"}}
            )
        )


def test_extract_output_assets_empty_for_text_output():
    assert extract_output_assets("plain text output") == []
    assert extract_output_assets("{}") == []


def test_build_execution_result_keeps_asset_and_artifact_ids_separate():
    request = TaskExecutionRequest(
        task=AnalysisTask(session_id="s", question="q"), run_id="run_1"
    )
    result = build_execution_result(
        request,
        status="succeeded",
        tool_name="run_analysis_operation",
        output_artifact_ids=["artifact_1"],
        output_assets=[_named("output", "asset_1")],
    )
    assert result.output_asset_ids == ["asset_1"]
    assert result.output_artifact_ids == ["artifact_1"]


def _runtime_with_step(tmp_path, name, expected, method="load_data"):
    runtime = make_runtime(tmp_path, name=name)
    step = make_step(STEP_A, method=method, expected=expected)
    runtime.initialize_plan(build_plan([step]))
    return runtime, step


def test_named_mapping_registers_bindings(tmp_path):
    runtime, step = _runtime_with_step(tmp_path, "named1", ["grouped", "summary"])
    asset_1 = register_asset(runtime, tmp_path, execution_id=None)
    asset_2 = register_asset(runtime, tmp_path, execution_id=None)
    result = make_result(
        runtime,
        step,
        output_assets=[_named("grouped", asset_1.asset_id), _named("summary", asset_2.asset_id)],
    )
    adjudicated = runtime.record_execution_result(result)
    assert adjudicated.status == "succeeded"
    binding_a = runtime.output_bindings.resolve(
        run_id=runtime.run.run_id,
        step_id=step.step_id,
        output_name="grouped",
        attempt=1,
    )
    binding_b = runtime.output_bindings.resolve(
        run_id=runtime.run.run_id,
        step_id=step.step_id,
        output_name="summary",
        attempt=1,
    )
    assert binding_a.asset_id == asset_1.asset_id
    assert binding_b.asset_id == asset_2.asset_id
    assert binding_a.execution_id == adjudicated.execution_id


def test_unknown_output_name_fails_without_binding(tmp_path):
    runtime, step = _runtime_with_step(tmp_path, "named2", ["output"])
    asset = register_asset(runtime, tmp_path)
    result = make_result(
        runtime, step, output_assets=[_named("unexpected", asset.asset_id)]
    )
    adjudicated = runtime.record_execution_result(result)
    assert adjudicated.status == "failed"
    assert adjudicated.error["type"] == "UnknownOutputName"
    assert len(runtime.output_bindings) == 0


def test_unnamed_single_output_binds_declared_name(tmp_path):
    runtime, step = _runtime_with_step(tmp_path, "named3", ["output"])
    asset = register_asset(runtime, tmp_path, execution_id=None)
    result = make_result(runtime, step, output_assets=[_named("", asset.asset_id)])
    adjudicated = runtime.record_execution_result(result)
    assert adjudicated.status == "succeeded"
    binding = runtime.output_bindings.resolve(
        run_id=runtime.run.run_id,
        step_id=step.step_id,
        output_name="output",
        attempt=1,
    )
    assert binding.asset_id == asset.asset_id


def test_unnamed_output_with_multiple_expected_fails(tmp_path):
    runtime, step = _runtime_with_step(tmp_path, "named4", ["a", "b"])
    asset = register_asset(runtime, tmp_path)
    result = make_result(runtime, step, output_assets=[_named("", asset.asset_id)])
    adjudicated = runtime.record_execution_result(result)
    assert adjudicated.status == "failed"
    assert adjudicated.error["type"] == "UnnamedMultiOutput"
    assert len(runtime.output_bindings) == 0


def test_duplicate_output_names_fail(tmp_path):
    runtime, step = _runtime_with_step(tmp_path, "named5", ["a", "b"])
    asset = register_asset(runtime, tmp_path)
    result = make_result(
        runtime,
        step,
        output_assets=[_named("a", asset.asset_id), _named("a", asset.asset_id)],
    )
    adjudicated = runtime.record_execution_result(result)
    assert adjudicated.status == "failed"
    assert adjudicated.error["type"] == "DuplicateOutputName"
    assert len(runtime.output_bindings) == 0


def test_unregistered_asset_fails(tmp_path):
    runtime, step = _runtime_with_step(tmp_path, "named6", ["output"])
    result = make_result(runtime, step, output_assets=[_named("output", "asset_ghost")])
    adjudicated = runtime.record_execution_result(result)
    assert adjudicated.status == "failed"
    assert adjudicated.error["type"] == "UnregisteredOutputAsset"
    assert len(runtime.output_bindings) == 0


def test_provenance_mismatch_fails_for_non_sqlite(tmp_path):
    runtime, step = _runtime_with_step(tmp_path, "named7", ["output"])
    asset = register_asset(runtime, tmp_path, execution_id="exec_other")
    result = make_result(runtime, step, output_assets=[_named("output", asset.asset_id)])
    adjudicated = runtime.record_execution_result(result)
    assert adjudicated.status == "failed"
    assert adjudicated.error["type"] == "ProvenanceMismatch"
    assert len(runtime.output_bindings) == 0


def test_sqlite_dedup_reuse_binds_current_execution(tmp_path):
    runtime, step = _runtime_with_step(tmp_path, "named8", ["output"])
    asset = register_asset(runtime, tmp_path, execution_id="exec_parent")
    # Mimic a deduped SQLite snapshot asset reused across attempts.
    asset.source_type = "sqlite"
    result = make_result(runtime, step, output_assets=[_named("output", asset.asset_id)])
    adjudicated = runtime.record_execution_result(result)
    assert adjudicated.status == "succeeded"
    binding = runtime.output_bindings.resolve(
        run_id=runtime.run.run_id,
        step_id=step.step_id,
        output_name="output",
        attempt=1,
    )
    assert binding.execution_id == adjudicated.execution_id
    assert binding.execution_id != "exec_parent"


def test_binding_conflict_fails_and_keeps_original(tmp_path):
    runtime, step = _runtime_with_step(tmp_path, "named9", ["output"])
    asset_1 = register_asset(runtime, tmp_path)
    asset_2 = register_asset(runtime, tmp_path)
    first = make_result(runtime, step, execution_id="exec_conflict", output_assets=[_named("output", asset_1.asset_id)])
    runtime.record_execution_result(first)
    second = make_result(runtime, step, execution_id="exec_conflict", output_assets=[_named("output", asset_2.asset_id)])
    adjudicated = runtime.record_execution_result(second)
    assert adjudicated.status == "failed"
    assert adjudicated.error["type"] == "BindingRegistrationFailed"
    binding = runtime.output_bindings.resolve(
        run_id=runtime.run.run_id,
        step_id=step.step_id,
        output_name="output",
        attempt=1,
    )
    assert binding.asset_id == asset_1.asset_id


def test_failed_execution_never_registers_binding(tmp_path):
    runtime, step = _runtime_with_step(tmp_path, "named10", ["output"])
    asset = register_asset(runtime, tmp_path)
    result = make_result(
        runtime,
        step,
        status="failed",
        output_assets=[_named("output", asset.asset_id)],
        error={"type": "ToolExecutionError", "message": "boom"},
    )
    adjudicated = runtime.record_execution_result(result)
    assert adjudicated.status == "failed"
    assert len(runtime.output_bindings) == 0

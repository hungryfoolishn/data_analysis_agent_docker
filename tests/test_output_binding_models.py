"""B11.2.1.0 v3 §3: binding/output model contracts."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from langgraph_langchain.execution.task_models import (
    TaskExecutionRequest,
    build_execution_result,
)
from langgraph_langchain.runtime.models import (
    ExecutionOutputAsset,
    ExecutionResult,
    OutputAssetBinding,
    ResolvedLogicalRef,
)
from langgraph_langchain.runtime.output_bindings import (
    OutputBindingConflictError,
    classify_input_reference,
    parse_logical_reference,
)
from langgraph_langchain.schemas import AnalysisTask

from tests.output_binding_common import STEP_A


def _binding(**overrides):
    payload = {
        "run_id": "run_1",
        "step_id": STEP_A,
        "output_name": "output",
        "asset_id": "asset_1",
        "execution_id": "exec_1",
        "attempt": 1,
    }
    payload.update(overrides)
    return OutputAssetBinding(**payload)


def test_binding_is_frozen():
    binding = _binding()
    with pytest.raises(ValidationError):
        binding.asset_id = "asset_2"


def test_execution_output_asset_is_frozen():
    item = ExecutionOutputAsset(output_name="output", asset_id="asset_1")
    with pytest.raises(ValidationError):
        item.asset_id = "asset_2"


def test_resolved_logical_ref_is_frozen():
    ref = ResolvedLogicalRef(
        reference=f"{STEP_A}.output",
        step_id=STEP_A,
        output_name="output",
        binding=_binding(),
    )
    with pytest.raises(ValidationError):
        ref.output_name = "other"


def test_build_execution_result_derives_output_asset_ids():
    task = AnalysisTask(session_id="s", question="q")
    request = TaskExecutionRequest(task=task, run_id="run_1")
    result = build_execution_result(
        request,
        status="succeeded",
        tool_name="load_data",
        output_assets=[
            ExecutionOutputAsset(output_name="a", asset_id="asset_1"),
            ExecutionOutputAsset(output_name="b", asset_id="asset_2"),
            ExecutionOutputAsset(output_name="c", asset_id="asset_1"),
        ],
    )
    assert result.output_asset_ids == ["asset_1", "asset_2"]
    assert result.execution_id_source is None
    assert result.input_bindings == []


def test_classify_physical_asset_id():
    assert classify_input_reference("asset_abc123", {STEP_A}) == "asset_id"
    assert classify_input_reference("", {STEP_A}) == "asset_id"
    assert classify_input_reference(None, {STEP_A}) == "asset_id"


def test_classify_logical_reference_requires_known_step():
    reference = f"{STEP_A}.grouped_data"
    assert classify_input_reference(reference, {STEP_A}) == "logical_ref"
    assert classify_input_reference(reference, set()) == "asset_id"


def test_classify_ambiguous_reference_raises():
    reference = f"{STEP_A}.grouped_data"
    with pytest.raises(OutputBindingConflictError):
        classify_input_reference(reference, {STEP_A}, {reference})


def test_classify_rejects_non_reference_shapes():
    assert classify_input_reference("data.csv", {STEP_A}) == "asset_id"
    assert classify_input_reference("path/to/file.csv", {STEP_A}) == "asset_id"
    assert (
        classify_input_reference(f"{STEP_A}.grouped-data", {STEP_A})
        == "asset_id"
    )


def test_parse_logical_reference_validates_step_format():
    assert parse_logical_reference(f"{STEP_A}.output") == (STEP_A, "output")
    with pytest.raises(OutputBindingConflictError):
        parse_logical_reference("custom_step.output")
    with pytest.raises(OutputBindingConflictError):
        parse_logical_reference("nodot")


def test_execution_result_default_fields_are_empty():
    result = ExecutionResult(run_id="run_1", tool_name="load_data", status="succeeded")
    assert result.output_asset_ids == []
    assert result.output_assets == []
    assert result.input_bindings == []

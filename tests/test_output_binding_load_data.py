"""B11.2.1 v3 §6.4: load_data context path and fallback binding."""

from __future__ import annotations

import pytest

from tests.output_binding_common import (
    STEP_A,
    build_plan,
    make_result,
    make_runtime,
    make_step,
    register_asset,
)


def _runtime_with_load_step(tmp_path, name, expected=("output",)):
    runtime = make_runtime(tmp_path, name=name)
    step = make_step(STEP_A, method="load_data", expected=list(expected))
    runtime.initialize_plan(build_plan([step]))
    return runtime, step


def test_single_execution_linked_asset_binds_expected_output(tmp_path):
    runtime, step = _runtime_with_load_step(tmp_path, "load1")
    result = make_result(runtime, step)
    asset = register_asset(runtime, tmp_path, execution_id=result.execution_id)
    adjudicated = runtime.record_execution_result(result)
    assert adjudicated.status == "succeeded"
    binding = runtime.output_bindings.resolve(
        run_id=runtime.run.run_id,
        step_id=step.step_id,
        output_name="output",
        attempt=1,
    )
    assert binding.asset_id == asset.asset_id


def test_ambiguous_linked_assets_fail(tmp_path):
    runtime, step = _runtime_with_load_step(tmp_path, "load2")
    result = make_result(runtime, step)
    register_asset(runtime, tmp_path, execution_id=result.execution_id)
    register_asset(runtime, tmp_path, execution_id=result.execution_id)
    adjudicated = runtime.record_execution_result(result)
    assert adjudicated.status == "failed"
    assert adjudicated.error["type"] == "AmbiguousOutputAsset"
    assert len(runtime.output_bindings) == 0


def test_missing_linked_asset_fails(tmp_path):
    runtime, step = _runtime_with_load_step(tmp_path, "load3")
    result = make_result(runtime, step)
    adjudicated = runtime.record_execution_result(result)
    assert adjudicated.status == "failed"
    assert adjudicated.error["type"] == "AmbiguousOutputAsset"
    assert len(runtime.output_bindings) == 0

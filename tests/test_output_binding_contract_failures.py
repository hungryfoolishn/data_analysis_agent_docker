"""B11.2.1 v3 §5.6: Recorder vs Runtime adjudication states."""

from __future__ import annotations

from tests.output_binding_common import (
    STEP_A,
    build_plan,
    make_result,
    make_runtime,
    make_step,
    register_asset,
)


def test_contract_failure_keeps_audit_trail_and_no_bindings(tmp_path):
    runtime = make_runtime(tmp_path, name="contract1")
    step = make_step(STEP_A, method="load_data", expected=["output"])
    runtime.initialize_plan(build_plan([step]))
    asset = register_asset(runtime, tmp_path)

    # Tool Recorder: succeeded record for the tool-side execution. The id is
    # the canonical one because the tool received the injected execution id.
    tool_record = make_result(runtime, step, execution_id="exec_canonical_1")
    runtime.record_execution(
        tool_name=step.method,
        status="succeeded",
        execution_id=tool_record.execution_id,
        step_id=step.step_id,
        input_asset_ids=[asset.asset_id],
        stdout_preview="tool side output",
    )

    # Runtime adjudication: canonical result violates the output contract.
    from langgraph_langchain.runtime.models import ExecutionOutputAsset

    canonical = make_result(
        runtime,
        step,
        execution_id="exec_canonical_1",
        output_assets=[
            ExecutionOutputAsset(output_name="unexpected", asset_id=asset.asset_id)
        ],
    )
    adjudicated = runtime.record_execution_result(canonical)
    assert adjudicated.status == "failed"
    assert adjudicated.error["type"] == "UnknownOutputName"

    # Same canonical id -> single authoritative execution node with the final
    # adjudication; tool stdout detail is preserved in the merged record.
    assert len(runtime.executions) == 1
    merged = runtime.executions[0]
    assert merged.execution_id == tool_record.execution_id
    assert merged.status == "failed"
    assert merged.error["type"] == "UnknownOutputName"
    assert any(
        item["task_id"] == STEP_A and item["execution_id"] == adjudicated.execution_id
        for item in runtime.failures
    )
    # No binding is consumable downstream.
    assert len(runtime.output_bindings) == 0
    # The failed asset stays registered as an unreferenced asset.
    assert asset.asset_id in runtime.assets

"""B11.2.1 v3 §5.2/§5.6: canonical execution id chain and adjudication states."""

from __future__ import annotations

import json

import pytest
from pydantic import BaseModel

from langgraph_langchain.execution.structured_executor import StructuredTaskExecutor
from langgraph_langchain.runtime.models import ExecutionResult

from tests.output_binding_common import (
    STEP_A,
    build_plan,
    make_runtime,
    make_step,
    register_asset,
)


class _ToolArgs(BaseModel):
    execution_id: str = ""


class _LegacyArgs(BaseModel):
    value: str = "x"


class _CanonicalTool:
    name = "fake_canonical"

    args_schema = _ToolArgs

    def invoke(self, arguments):
        return json.dumps(
            {
                "status": "succeeded",
                "output_asset_id": "asset_1",
                "received_execution_id": arguments.get("execution_id", ""),
            }
        )


class _LegacyTool:
    name = "fake_legacy"

    args_schema = _LegacyArgs

    def invoke(self, arguments):
        assert "execution_id" not in arguments
        return "ok"


def _prepared_request(runtime, step):
    runtime.initialize_plan(build_plan([step]))
    task = runtime.scheduler.get_task(step.step_id)
    return runtime.build_execution_request(task)


def test_canonical_execution_id_injected_and_shared(tmp_path):
    runtime = make_runtime(tmp_path, name="exec1")
    step = make_step(STEP_A, expected=["output"])
    request = _prepared_request(runtime, step)
    assert request.execution_id
    executor = StructuredTaskExecutor(tool_resolver=lambda name: _CanonicalTool())
    result = executor.execute(request)
    assert result.execution_id == request.execution_id
    assert result.execution_id_source == "canonical"
    assert result.status == "succeeded"


def test_legacy_tool_not_injected_and_marked_legacy(tmp_path):
    runtime = make_runtime(tmp_path, name="exec2")
    step = make_step(STEP_A, expected=["output"])
    request = _prepared_request(runtime, step)
    executor = StructuredTaskExecutor(tool_resolver=lambda name: _LegacyTool())
    result = executor.execute(request)
    assert result.execution_id == request.execution_id
    assert result.execution_id_source == "legacy"


def test_record_execution_result_adopts_legacy_tool_record(tmp_path):
    runtime = make_runtime(tmp_path, name="exec3")
    step = make_step(STEP_A, expected=["output"])
    runtime.initialize_plan(build_plan([step]))
    tool_record = ExecutionResult(
        run_id=runtime.run.run_id,
        task_id=step.step_id,
        step_id=step.step_id,
        tool_name=step.method,
        status="succeeded",
        input_asset_ids=[],
        stdout_preview="tool side",
    )
    runtime.record_execution(
        tool_name=step.method,
        status="succeeded",
        execution_id=tool_record.execution_id,
        step_id=step.step_id,
        input_asset_ids=[],
        stdout_preview="tool side",
    )
    assert len(runtime.executions) == 1
    # A single execution-linked asset lets the load_data fallback satisfy the
    # output contract after the legacy id is adopted.
    register_asset(runtime, tmp_path, execution_id=tool_record.execution_id)
    result = ExecutionResult(
        run_id=runtime.run.run_id,
        task_id=step.step_id,
        step_id=step.step_id,
        tool_name=step.method,
        status="succeeded",
        input_asset_ids=[],
        execution_id_source=None,
    )
    adjudicated = runtime.record_execution_result(result)
    assert adjudicated.execution_id == tool_record.execution_id
    assert len(runtime.executions) == 1
    assert runtime.executions[0].status == "succeeded"


def test_record_execution_result_rejects_canonical_id_mismatch(tmp_path):
    runtime = make_runtime(tmp_path, name="exec4")
    step = make_step(STEP_A, expected=["output"])
    runtime.initialize_plan(build_plan([step]))
    tool_record = ExecutionResult(
        run_id=runtime.run.run_id,
        task_id=step.step_id,
        step_id=step.step_id,
        tool_name=step.method,
        status="succeeded",
        input_asset_ids=[],
    )
    runtime.record_execution(
        tool_name=step.method,
        status="succeeded",
        execution_id=tool_record.execution_id,
        step_id=step.step_id,
        input_asset_ids=[],
        stdout_preview="tool side",
    )
    result = ExecutionResult(
        run_id=runtime.run.run_id,
        task_id=step.step_id,
        step_id=step.step_id,
        tool_name=step.method,
        status="succeeded",
        input_asset_ids=[],
        execution_id="exec_canonical_other",
        execution_id_source="canonical",
    )
    adjudicated = runtime.record_execution_result(result)
    assert adjudicated.status == "failed"
    assert adjudicated.error["type"] == "ExecutionIdMismatch"
    # Tool recorder record preserved; final adjudication recorded separately.
    assert len(runtime.executions) == 2
    statuses = {item.execution_id: item.status for item in runtime.executions}
    assert statuses[tool_record.execution_id] == "succeeded"
    assert statuses["exec_canonical_other"] == "failed"
    assert len(runtime.output_bindings) == 0

"""B11.2.1 v3 §7.3: plan-time typing of physical IDs vs logical references."""

from __future__ import annotations

import json

import pytest

from langgraph_langchain.runtime.context import AnalysisRuntime
from langgraph_langchain.runtime.plans import (
    AnalysisPlan,
    PlanExecutionValidationError,
    validate_runtime_plan,
)

from tests.output_binding_common import (
    STEP_A,
    STEP_B,
    build_plan,
    make_runtime,
    make_step,
)


def _validate(runtime, plan):
    from langgraph_langchain.tools.registry import registry

    return validate_runtime_plan(
        plan,
        available_methods=registry.get_tool_names(),
        available_asset_ids=set(runtime.assets.keys()),
    )


def _runtime(tmp_path, name):
    return make_runtime(tmp_path, name=name)


def _plan_with_chain(required_inputs=None, parameters=None, depends=None, expected_a=None):
    step_a = make_step(
        STEP_A, method="load_data", expected=expected_a or ["output"]
    )
    step_b = make_step(
        STEP_B,
        method="load_data",
        expected=["output"],
        depends=depends if depends is not None else [STEP_A],
        required_inputs=required_inputs or [],
        parameters=parameters or {},
    )
    return build_plan([step_a, step_b])


def test_logical_reference_passes_plan_validation(tmp_path):
    runtime = _runtime(tmp_path, "plan1")
    plan = _plan_with_chain(
        required_inputs=[f"{STEP_A}.output"],
        parameters={"asset_id": f"{STEP_A}.output"},
    )
    assert _validate(runtime, plan) == []


def test_logical_reference_without_dependency_fails(tmp_path):
    runtime = _runtime(tmp_path, "plan2")
    plan = _plan_with_chain(
        required_inputs=[f"{STEP_A}.output"],
        parameters={},
        depends=[],
    )
    errors = _validate(runtime, plan)
    assert any("not one of its dependencies" in error for error in errors)


def test_argument_reference_without_dependency_fails(tmp_path):
    runtime = _runtime(tmp_path, "plan3")
    plan = _plan_with_chain(
        required_inputs=[],
        parameters={"asset_id": f"{STEP_A}.output"},
        depends=[],
    )
    errors = _validate(runtime, plan)
    assert any("not one of its dependencies" in error for error in errors)


def test_parameters_json_reference_scanned(tmp_path):
    runtime = _runtime(tmp_path, "plan4")
    plan = _plan_with_chain(
        required_inputs=[],
        parameters={
            "parameters_json": json.dumps({"asset_id": f"{STEP_A}.output"})
        },
    )
    errors = _validate(runtime, plan)
    assert errors == []
    plan_bad = _plan_with_chain(
        required_inputs=[],
        parameters={
            "parameters_json": json.dumps({"asset_id": f"{STEP_A}.output"})
        },
        depends=[],
    )
    errors_bad = _validate(runtime, plan_bad)
    assert any("not one of its dependencies" in error for error in errors_bad)


def test_unknown_output_name_fails(tmp_path):
    runtime = _runtime(tmp_path, "plan5")
    plan = _plan_with_chain(
        required_inputs=[f"{STEP_A}.other"],
        expected_a=["output"],
    )
    errors = _validate(runtime, plan)
    assert any("not declared by step" in error for error in errors)


def test_unknown_producer_step_treated_as_physical_asset(tmp_path):
    runtime = _runtime(tmp_path, "plan6")
    plan = _plan_with_chain(required_inputs=["step_other.output"])
    errors = _validate(runtime, plan)
    assert any("unknown input asset" in error for error in errors)


def test_physical_ids_still_validated(tmp_path):
    runtime = _runtime(tmp_path, "plan7")
    plan = _plan_with_chain(required_inputs=["asset_ghost"])
    errors = _validate(runtime, plan)
    assert any("unknown input asset" in error for error in errors)


def test_initialize_plan_accepts_then_runtime_rejects_unresolved_reference(tmp_path):
    from langgraph_langchain.execution.structured_executor import (
        StructuredTaskExecutor,
    )
    from langgraph_langchain.runtime.context import BindingResolutionError
    from langgraph_langchain.runtime.executor import TaskExecutor

    runtime = _runtime(tmp_path, "plan8")
    plan = _plan_with_chain(required_inputs=[f"{STEP_A}.output"])
    # Plan structure is valid without bindings; the missing binding is an
    # execution-time failure, not a plan-time one.
    runtime.initialize_plan(plan)
    runtime.scheduler.mark_running(STEP_A)
    runtime.scheduler.mark_succeeded(STEP_A)
    runtime.configure_executor(
        TaskExecutor(structured=StructuredTaskExecutor(tool_resolver=lambda n: None))
    )
    result = runtime.execute_next_task()
    assert result is not None
    assert result.status == "failed"
    assert result.error["type"] == "BindingResolutionError"


"""B4.2.1: GeneralPlan v2 reference contract tests."""

from __future__ import annotations

import json

import pytest

import pandas as pd

from langgraph_langchain.execution.structured_executor import StructuredTaskExecutor
from langgraph_langchain.execution.task_models import TaskExecutionRequest
from langgraph_langchain.runtime.analysis_workflow import general_plan_to_runtime_plan
from langgraph_langchain.runtime.context import AnalysisRuntime
from langgraph_langchain.runtime.executor import TaskExecutor
from langgraph_langchain.runtime.general_planner import (
    GeneralAnalysisPlan,
    GeneralPlanStep,
    validate_general_plan,
)
from langgraph_langchain.runtime.llm_planner import run_planning
from langgraph_langchain.runtime.plans import AnalysisPlan, validate_runtime_plan
from pydantic import BaseModel

from langgraph_langchain.schemas import AnalysisTask

from tests.output_binding_common import make_runtime, register_asset

STEP_1 = "step_" + "1" * 32
STEP_2 = "step_" + "2" * 32
STEP_3 = "step_" + "3" * 32

ASSET_COLUMNS = {"asset_1": ["department", "revenue", "date"]}


def _step(step_id, *, operation, input_refs, output_name="output", expected="dataframe", depends=None, params=None):
    return GeneralPlanStep(
        step_id=step_id,
        operation=operation,
        input_refs=list(input_refs),
        parameters=dict(params or {}),
        expected_output=expected,
        output_name=output_name,
        depends_on=list(depends or []),
    )


def _v2_plan():
    return GeneralAnalysisPlan(
        user_goal="Aggregate revenue by department and sort the result",
        input_asset_ids=["asset_1"],
        steps=[
            _step(
                STEP_1,
                operation="group_aggregate",
                input_refs=["asset_1"],
                output_name="aggregate_result",
                params={"group_by": "department", "metric": "revenue", "agg": "sum"},
            ),
            _step(
                STEP_2,
                operation="sort",
                input_refs=[f"{STEP_1}.aggregate_result"],
                output_name="sorted_result",
                depends=[STEP_1],
                params={"by": "revenue"},
            ),
        ],
    )


def _validate(plan):
    return validate_general_plan(plan, asset_columns=ASSET_COLUMNS)


def test_initial_asset_reference_passes():
    plan = GeneralAnalysisPlan(
        user_goal="g",
        input_asset_ids=["asset_1"],
        steps=[
            _step(
                STEP_1,
                operation="group_aggregate",
                input_refs=["asset_1"],
                params={"group_by": "department", "metric": "revenue", "agg": "sum"},
            )
        ],
    )
    assert _validate(plan) == []


def test_predecessor_output_reference_passes():
    assert _validate(_v2_plan()) == []


def test_forward_reference_rejected():
    plan = _v2_plan()
    # step_1 (first) references the output of the later step_2.
    plan.steps[0].input_refs = [f"{STEP_2}.sorted_result"]
    errors = _validate(plan)
    assert any("forward" in error or "unknown step" in error for error in errors)


def test_self_reference_rejected():
    plan = _v2_plan()
    plan.steps[0].input_refs = [f"{STEP_1}.aggregate_result"]
    errors = _validate(plan)
    assert any("references its own output" in error for error in errors)


def test_unknown_step_reference_rejected():
    plan = _v2_plan()
    plan.steps[1].input_refs = [f"step_{('9' * 32)}.aggregate_result"]
    errors = _validate(plan)
    assert any("unknown step" in error for error in errors)


def test_output_name_mismatch_rejected():
    plan = _v2_plan()
    plan.steps[1].input_refs = [f"{STEP_1}.wrong_name"]
    errors = _validate(plan)
    assert any("not declared by step" in error for error in errors)


def test_cycle_rejected():
    step_a = _step(STEP_1, operation="sort", input_refs=["asset_1"], params={"by": "revenue"})
    step_b = _step(STEP_2, operation="sort", input_refs=["asset_1"], depends=[STEP_1], params={"by": "revenue"})
    step_a.depends_on = [STEP_2]
    step_b.input_refs = [f"{STEP_1}.output"]
    plan = GeneralAnalysisPlan(user_goal="g", input_asset_ids=["asset_1"], steps=[step_a, step_b])
    errors = _validate(plan)
    assert any("cycle" in error for error in errors)


def test_reference_without_depends_on_rejected():
    plan = _v2_plan()
    plan.steps[1].depends_on = []
    errors = _validate(plan)
    assert any("without declaring" in error for error in errors)


def test_v1_plan_without_output_name_still_valid():
    steps = [
        _step(
            STEP_1,
            operation="group_aggregate",
            input_refs=["asset_1"],
            params={"group_by": "department", "metric": "revenue", "agg": "sum"},
        ),
        _step(
            STEP_2,
            operation="summarize",
            input_refs=["asset_1"],
            expected="metric",
        ),
    ]
    plan = GeneralAnalysisPlan(user_goal="g", input_asset_ids=["asset_1"], steps=steps)
    assert plan.steps[0].output_name == "output"
    assert _validate(plan) == []


def test_converted_runtime_plan_passes_validate_runtime_plan(tmp_path):
    runtime = make_runtime(tmp_path, name="v2conv")
    asset = register_asset(runtime, tmp_path, execution_id=None)
    columns = ASSET_COLUMNS["asset_1"]
    plan = GeneralAnalysisPlan(
        user_goal="g",
        input_asset_ids=[asset.asset_id],
        steps=[
            _step(
                STEP_1,
                operation="group_aggregate",
                input_refs=[asset.asset_id],
                output_name="aggregate_result",
                params={"group_by": "department", "metric": "revenue", "agg": "sum"},
            ),
            _step(
                STEP_2,
                operation="sort",
                input_refs=[f"{STEP_1}.aggregate_result"],
                output_name="sorted_result",
                depends=[STEP_1],
                params={"by": "revenue"},
            ),
        ],
    )
    runtime_plan = general_plan_to_runtime_plan(plan)
    from langgraph_langchain.tools.registry import registry

    errors = validate_runtime_plan(
        runtime_plan,
        available_methods=registry.get_tool_names(),
        available_asset_ids={asset.asset_id},
    )
    assert errors == []
    assert runtime_plan.steps[0].expected_outputs == ["aggregate_result"]
    assert runtime_plan.steps[1].required_inputs == [f"{STEP_1}.aggregate_result"]


def _prepared_runtime(tmp_path, name, plan, asset):
    runtime = make_runtime(tmp_path, name=name)
    runtime_plan = general_plan_to_runtime_plan(plan)
    runtime.initialize_plan(runtime_plan)
    return runtime


class _ScriptedArgs(BaseModel):
    execution_id: str = ""
    output_name: str = "output"


class _ScriptedTool:
    """Emits runtime-contract JSON for the injected output_name/execution_id."""

    args_schema = _ScriptedArgs

    def __init__(self, runtime, outputs):
        self.runtime = runtime
        self.outputs = outputs  # list of asset ids per call
        self.calls = []

    def invoke(self, arguments):
        self.calls.append(dict(arguments))
        asset_id = self.outputs[len(self.calls) - 1]
        return json.dumps(
            {
                "status": "succeeded",
                "execution_id": arguments.get("execution_id", ""),
                "output_asset_id": asset_id,
                "output_assets": {arguments.get("output_name", "output"): asset_id},
            }
        )


def test_full_v2_chain_binds_named_outputs_and_downstream_resolution(tmp_path):
    runtime = make_runtime(tmp_path, name="v2chain")
    source_asset = register_asset(runtime, tmp_path, execution_id=None)
    output_asset_1 = register_asset(runtime, tmp_path, execution_id=None)
    output_asset_2 = register_asset(runtime, tmp_path, execution_id=None)

    plan = GeneralAnalysisPlan(
        user_goal="g",
        input_asset_ids=[source_asset.asset_id],
        steps=[
            _step(
                STEP_1,
                operation="group_aggregate",
                input_refs=[source_asset.asset_id],
                output_name="aggregate_result",
                params={"group_by": "department", "metric": "revenue", "agg": "sum"},
            ),
            _step(
                STEP_2,
                operation="sort",
                input_refs=[f"{STEP_1}.aggregate_result"],
                output_name="sorted_result",
                depends=[STEP_1],
                params={"by": "revenue"},
            ),
        ],
    )
    runtime_plan = general_plan_to_runtime_plan(plan)
    runtime.initialize_plan(runtime_plan)

    tool = _ScriptedTool(runtime, [output_asset_1.asset_id, output_asset_2.asset_id])
    runtime.configure_executor(
        TaskExecutor(
            structured=StructuredTaskExecutor(tool_resolver=lambda name: tool)
        )
    )

    for _ in range(2):
        result = runtime.execute_next_task()
        assert result is not None and result.status == "succeeded"
    assert runtime.status == "completed"

    # The executor injected the declared output name; the tool emitted the
    # matching key, so both named bindings exist.
    binding_1 = runtime.output_bindings.resolve(
        run_id=runtime.run.run_id,
        step_id=STEP_1,
        output_name="aggregate_result",
        attempt=1,
    )
    binding_2 = runtime.output_bindings.resolve(
        run_id=runtime.run.run_id,
        step_id=STEP_2,
        output_name="sorted_result",
        attempt=1,
    )
    assert binding_1.asset_id == output_asset_1.asset_id
    assert binding_2.asset_id == output_asset_2.asset_id

    # Downstream consumed the upstream output through the Binding, not the
    # run-level initial asset.
    assert runtime.executions[1].input_asset_ids == [output_asset_1.asset_id]
    assert tool.calls[1]["asset_id"] == output_asset_1.asset_id
    assert tool.calls[1]["output_name"] == "sorted_result"


def test_executor_injects_declared_output_name(tmp_path):
    from langgraph_langchain.runtime.models import RuntimePlanStep

    runtime = make_runtime(tmp_path, name="v2inj")
    source_asset = register_asset(runtime, tmp_path, execution_id=None)
    step = RuntimePlanStep(
        step_id=STEP_1,
        objective="Aggregate revenue",
        method="run_analysis_operation",
        expected_outputs=["aggregate_result"],
        parameters={
            "asset_id": source_asset.asset_id,
            "operation": "group_aggregate",
            "group_by": "department",
            "metric": "revenue",
            "agg": "sum",
        },
    )
    plan = AnalysisPlan(goal="g", input_asset_ids=[source_asset.asset_id], steps=[step])
    runtime.initialize_plan(plan)
    task = runtime.scheduler.get_task(STEP_1)

    received: dict = {}

    class _Args(BaseModel):
        execution_id: str = ""
        output_name: str = "output"

    class _EchoTool:
        args_schema = _Args

        def invoke(self, arguments):
            received.update(arguments)
            return json.dumps(
                {"status": "ok", "output_assets": {arguments["output_name"]: "asset_x"}}
            )

    request = TaskExecutionRequest(
        task=task,
        run_id=runtime.run.run_id,
        execution_id="exec_test",
        arguments={},
    )
    executor = StructuredTaskExecutor(tool_resolver=lambda name: _EchoTool())
    result = executor.execute(request)
    assert received["output_name"] == "aggregate_result"
    assert received["execution_id"] == "exec_test"
    assert result.output_asset_ids == ["asset_x"]
    assert result.output_assets[0].output_name == "aggregate_result"
    assert result.execution_id_source == "canonical"


# ---------------------------------------------------------------------------
# B4.2.1 PlanningOutcome state machine (frozen contract §9)
# ---------------------------------------------------------------------------

from langgraph_langchain.runtime.llm_planner import run_planning  # noqa: E402
from langgraph_langchain.runtime.general_planner import (  # noqa: E402
    DeterministicAnalysisPlanner,
)


def _planner_input():
    from langgraph_langchain.data.assets import SchemaSnapshot
    from langgraph_langchain.runtime.planner_models import PlannerAssetContext, PlannerInput

    snapshot = SchemaSnapshot.from_dataframe(
        pd.DataFrame({"department": ["East"], "revenue": [1.0]})
    )
    # Deterministic-planner-matchable question (used by fallback tests).
    return PlannerInput(
        question="按 department 分组 sum revenue",
        assets=[
            PlannerAssetContext(
                asset_id="asset_1",
                schema_snapshot=snapshot,
            )
        ],
    )


def _deterministic():
    return DeterministicAnalysisPlanner()


def test_outcome_planned_with_fake_llm():
    plan = _v2_plan()
    outcome = run_planning(
        _planner_input(),
        deterministic_planner=_deterministic(),
        llm_planner_fn=lambda input, errors: plan.model_dump_json(),
    )
    assert outcome.status == "planned"
    assert outcome.plan is not None
    assert outcome.plan.steps[1].input_refs == [f"{STEP_1}.aggregate_result"]


def test_outcome_planned_with_deterministic_fallback_when_llm_unavailable():
    def _unavailable(input, errors):
        raise RuntimeError("llm down")

    outcome = run_planning(
        _planner_input(),
        deterministic_planner=_deterministic(),
        llm_planner_fn=_unavailable,
    )
    assert outcome.status == "fallback_planned"
    assert outcome.reason_code == "llm_unavailable"
    assert outcome.plan is not None


def test_outcome_planned_with_fallback_after_invalid_llm_output():
    calls = []

    def _garbage(input, errors):
        calls.append(errors)
        return "not json at all"

    outcome = run_planning(
        _planner_input(),
        deterministic_planner=_deterministic(),
        llm_planner_fn=_garbage,
    )
    assert outcome.status == "fallback_planned"
    assert len(calls) == 3  # initial + 2 repair retries
    assert outcome.reason_code == "llm_unavailable"


def test_outcome_needs_clarification_when_intent_unmatched():
    from langgraph_langchain.data.assets import SchemaSnapshot
    from langgraph_langchain.runtime.planner_models import PlannerAssetContext, PlannerInput

    snapshot = SchemaSnapshot.from_dataframe(
        pd.DataFrame({"department": ["East"], "revenue": [1.0]})
    )
    planner_input = PlannerInput(
        question="分析利润率变化",
        assets=[PlannerAssetContext(asset_id="asset_1", schema_snapshot=snapshot)],
    )
    outcome = run_planning(
        planner_input,
        deterministic_planner=_deterministic(),
        llm_planner_fn=None,
    )
    # "利润率" has no matching column/operation: the planner must ask for
    # clarification instead of silently rewriting the goal.
    assert outcome.status == "needs_clarification"
    assert outcome.clarification_questions


def test_outcome_planning_failed_on_validation_errors():
    from langgraph_langchain.data.assets import SchemaSnapshot
    from langgraph_langchain.runtime.planner_models import PlannerAssetContext, PlannerInput

    snapshot = SchemaSnapshot.from_dataframe(
        pd.DataFrame({"department": ["East"], "revenue": [1.0]})
    )
    planner_input = PlannerInput(
        question="按不存在字段统计",
        assets=[PlannerAssetContext(asset_id="asset_1", schema_snapshot=snapshot)],
    )
    outcome = run_planning(
        planner_input,
        deterministic_planner=_deterministic(),
        llm_planner_fn=None,
    )
    assert outcome.status == "planning_failed"
    assert outcome.plan is None

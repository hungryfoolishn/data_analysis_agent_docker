"""B11.2.1.1 P1-2: propose_plan() and initialize_plan() validate identically."""

from __future__ import annotations

import pytest

from tests.output_binding_common import (
    STEP_A,
    STEP_B,
    build_plan,
    make_runtime,
    make_step,
)


def _chain_plan(*, step_b_depends):
    step_a = make_step(STEP_A, method="load_data", expected=["output"])
    step_b = make_step(
        STEP_B,
        method="load_data",
        expected=["output"],
        depends=step_b_depends,
        required_inputs=[f"{STEP_A}.output"],
        parameters={"asset_id": f"{STEP_A}.output"},
    )
    return build_plan([step_a, step_b])


def test_propose_plan_accepts_logical_reference_plan(tmp_path):
    runtime = make_runtime(tmp_path, name="entry1")
    plan = _chain_plan(step_b_depends=[STEP_A])
    proposed = runtime.propose_plan(plan)
    assert [step.step_id for step in proposed.steps] == [STEP_A, STEP_B]


def test_propose_plan_rejects_reference_outside_closure(tmp_path):
    runtime = make_runtime(tmp_path, name="entry2")
    plan = _chain_plan(step_b_depends=[])
    with pytest.raises(ValueError, match="not one of its dependencies"):
        runtime.propose_plan(plan)


def test_propose_plan_and_initialize_plan_agree(tmp_path):
    runtime = make_runtime(tmp_path, name="entry3")
    valid_plan = _chain_plan(step_b_depends=[STEP_A])
    runtime.propose_plan(valid_plan.model_copy(deep=True))
    second = make_runtime(tmp_path, name="entry3b")
    second.initialize_plan(_chain_plan(step_b_depends=[STEP_A]))

"""B11.2.1.0 v3 §4: OutputBindingRegistry contracts."""

from __future__ import annotations

import pytest

from langgraph_langchain.runtime.models import OutputAssetBinding
from langgraph_langchain.runtime.output_bindings import (
    OutputBindingConflictError,
    OutputBindingNotFoundError,
    OutputBindingRegistry,
)

from tests.output_binding_common import STEP_A, STEP_B


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


def test_register_and_resolve_exact_attempt():
    registry = OutputBindingRegistry()
    registry.register(_binding())
    resolved = registry.resolve(
        run_id="run_1", step_id=STEP_A, output_name="output", attempt=1
    )
    assert resolved.asset_id == "asset_1"
    with pytest.raises(OutputBindingNotFoundError):
        registry.resolve(
            run_id="run_1", step_id=STEP_A, output_name="output", attempt=2
        )


def test_register_is_idempotent_for_identical_content():
    registry = OutputBindingRegistry()
    first = registry.register(_binding())
    second = registry.register(_binding())
    assert first is second
    assert len(registry) == 1


def test_register_conflict_raises_and_keeps_original():
    registry = OutputBindingRegistry()
    registry.register(_binding())
    with pytest.raises(OutputBindingConflictError):
        registry.register(_binding(asset_id="asset_2"))
    with pytest.raises(OutputBindingConflictError):
        registry.register(_binding(execution_id="exec_2"))
    resolved = registry.resolve(
        run_id="run_1", step_id=STEP_A, output_name="output", attempt=1
    )
    assert resolved.asset_id == "asset_1"
    assert resolved.execution_id == "exec_1"


def test_attempts_are_isolated():
    registry = OutputBindingRegistry()
    registry.register(_binding())
    registry.register(_binding(attempt=2, asset_id="asset_2", execution_id="exec_2"))
    first = registry.resolve(
        run_id="run_1", step_id=STEP_A, output_name="output", attempt=1
    )
    second = registry.resolve(
        run_id="run_1", step_id=STEP_A, output_name="output", attempt=2
    )
    assert first.asset_id == "asset_1"
    assert second.asset_id == "asset_2"


def test_runs_are_isolated():
    registry = OutputBindingRegistry()
    registry.register(_binding())
    with pytest.raises(OutputBindingNotFoundError):
        registry.resolve(
            run_id="run_2", step_id=STEP_A, output_name="output", attempt=1
        )


def test_multiple_outputs_and_steps_are_independent():
    registry = OutputBindingRegistry()
    registry.register(_binding(output_name="grouped_data", asset_id="asset_1"))
    registry.register(_binding(output_name="summary_data", asset_id="asset_2"))
    registry.register(_binding(step_id=STEP_B, asset_id="asset_3"))
    assert len(registry) == 3
    assert len(registry.resolve_for_step(run_id="run_1", step_id=STEP_A, attempt=1)) == 2
    assert len(registry.resolve_for_step(run_id="run_1", step_id=STEP_B, attempt=1)) == 1


def test_register_many_is_all_or_nothing():
    registry = OutputBindingRegistry()
    good = _binding()
    conflicting = _binding(asset_id="asset_9")
    with pytest.raises(OutputBindingConflictError):
        registry.register_many([good, conflicting])
    assert len(registry) == 0
    registered = registry.register_many([good])
    assert len(registry) == 1
    assert registered[0].asset_id == "asset_1"


def test_register_many_allows_identical_duplicates():
    registry = OutputBindingRegistry()
    binding = _binding()
    registry.register_many([binding, binding])
    assert len(registry) == 1


def test_list_for_run_and_clear():
    registry = OutputBindingRegistry()
    registry.register(_binding())
    registry.register(_binding(step_id=STEP_B, attempt=2))
    assert len(registry.list_for_run(run_id="run_1")) == 2
    assert len(registry.list_for_run(run_id="run_1", attempt=2)) == 1
    assert registry.list_for_run(run_id="run_9") == []
    registry.clear()
    assert len(registry) == 0


def test_discard_and_list_all():
    registry = OutputBindingRegistry()
    binding = _binding()
    registry.register(binding)
    assert len(registry.list_all()) == 1
    registry.discard(binding)
    assert len(registry) == 0

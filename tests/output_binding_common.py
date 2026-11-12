"""Shared harness for B11.2.1 Output Asset Binding tests."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from langgraph_langchain.runtime.context import AnalysisRuntime
from langgraph_langchain.runtime.models import ExecutionResult, RuntimePlanStep
from langgraph_langchain.runtime.plans import AnalysisPlan


STEP_A = "step_" + "a" * 32
STEP_B = "step_" + "b" * 32
STEP_C = "step_" + "c" * 32


def make_runtime(tmp_path: Path, *, name: str = "ws") -> AnalysisRuntime:
    workspace = tmp_path / name
    workspace.mkdir(parents=True, exist_ok=True)
    return AnalysisRuntime(
        workspace_dir=workspace,
        session_id="binding-tests",
        question="binding test question",
    )


def make_step(
    step_id: str,
    *,
    method: str = "load_data",
    expected=None,
    depends=None,
    required_inputs=None,
    parameters=None,
) -> RuntimePlanStep:
    return RuntimePlanStep(
        step_id=step_id,
        objective=f"step {step_id}",
        method=method,
        required_inputs=list(required_inputs or []),
        expected_outputs=list(expected or []),
        parameters=dict(parameters or {}),
        depends_on=list(depends or []),
    )


def build_plan(steps) -> AnalysisPlan:
    return AnalysisPlan(
        goal="binding test goal",
        steps=list(steps),
        require_confirmation=False,
        strict_execution=True,
    )


def register_asset(runtime: AnalysisRuntime, tmp_path: Path, *, execution_id=None):
    """Register a deterministic CSV-backed DataAsset, optionally execution-linked."""
    frame = pd.DataFrame({"a": [1, 2, 3]})
    path = tmp_path / f"asset_{len(runtime.assets)}.csv"
    frame.to_csv(path, index=False)
    metadata = {"execution_id": execution_id} if execution_id else {}
    return runtime.register_dataframe(
        dataframe=frame,
        source_path=path,
        source_type="csv",
        source_metadata=metadata,
    )


def make_result(
    runtime: AnalysisRuntime,
    step: RuntimePlanStep,
    *,
    status: str = "succeeded",
    execution_id=None,
    execution_id_source: str = "canonical",
    output_assets=None,
    output_artifact_ids=None,
    input_asset_ids=None,
    error=None,
) -> ExecutionResult:
    result_kwargs: dict = {
        "run_id": runtime.run.run_id,
        "task_id": step.step_id,
        "step_id": step.step_id,
        "tool_name": step.method,
        "status": status,
        "execution_id_source": execution_id_source,
        "output_assets": list(output_assets or []),
        "output_artifact_ids": list(output_artifact_ids or []),
    }
    if execution_id:
        result_kwargs["execution_id"] = execution_id
    return ExecutionResult(
        input_asset_ids=(
            list(input_asset_ids)
            if input_asset_ids is not None
            else list(runtime.task.input_asset_ids)
        ),
        error=error,
        **result_kwargs,
    )

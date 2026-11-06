"""Register assets and convert structured plans into Runtime plans."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from langgraph_langchain.config import MAX_IN_MEMORY_ROWS
from langgraph_langchain.data.assets import DataAsset
from langgraph_langchain.data.sources import FileDataSource
from langgraph_langchain.runtime.context import AnalysisRuntime
from langgraph_langchain.runtime.general_planner import (
    DeterministicAnalysisPlanner,
    GeneralAnalysisPlan,
    GeneralPlanStep,
    PlannerClarificationError,
    PlanValidationError,
)
from langgraph_langchain.runtime.models import RuntimePlanStep
from langgraph_langchain.runtime.plans import AnalysisPlan


@dataclass(frozen=True)
class PreparedFileAnalysis:
    asset: DataAsset
    general_plan: GeneralAnalysisPlan
    runtime_plan: AnalysisPlan
    row_estimate: int
    used_fallback_plan: bool = False


def register_file_asset(
    runtime: AnalysisRuntime,
    path: Path,
    *,
    sheet_name: str | None = None,
) -> tuple[DataAsset, FileDataSource]:
    """Register a bounded file asset and return it with its source adapter."""
    source = FileDataSource(path, sheet_name=sheet_name)
    scan = source.scan_schema()
    preview_rows = min(MAX_IN_MEMORY_ROWS, 10_000)
    dataframe = source.materialize(max_rows=preview_rows)
    suffix = path.suffix.lower().lstrip(".") or "unknown"
    asset = runtime.register_dataframe(
        dataframe=dataframe,
        source_path=path,
        source_type=suffix,
        sheet_name=sheet_name if suffix in {"xlsx", "xls"} else None,
        source_metadata={
            "format": suffix,
            "row_estimate": scan.estimated_rows,
            "registered_row_cap": preview_rows,
            "schema_columns": [column.name for column in scan.schema_snapshot.columns],
            "source_hash": scan.source_hash,
        },
    )
    return asset, source


def general_plan_to_runtime_plan(plan: GeneralAnalysisPlan) -> AnalysisPlan:
    steps: list[RuntimePlanStep] = []
    previous_step_id: str | None = None
    for item in plan.steps:
        operation_parameters = {
            key: value
            for key, value in item.parameters.items()
            if key not in {"asset_id", "parameters_json"}
        }
        step = RuntimePlanStep(
            step_id=item.step_id,
            objective=f"{item.operation}: {plan.user_goal}",
            method="run_analysis_operation",
            required_inputs=list(plan.input_asset_ids),
            expected_outputs=[f"{item.step_id}:output"],
            parameters={
                "asset_id": item.input_refs[0],
                "operation": item.operation,
                **operation_parameters,
                "parameters_json": json.dumps(
                    operation_parameters, ensure_ascii=False, sort_keys=True
                ),
            },
            depends_on=(
                list(item.depends_on)
                if item.depends_on
                else ([previous_step_id] if previous_step_id else [])
            ),
        )
        steps.append(step)
        previous_step_id = step.step_id
    return AnalysisPlan(
        goal=plan.user_goal,
        input_asset_ids=list(plan.input_asset_ids),
        steps=steps,
        success_criteria=[
            "Each analysis step uses an allowlisted deterministic operation.",
            "Numeric findings are traceable to registered assets and evidence.",
        ],
    )


def prepare_file_analysis(
    runtime: AnalysisRuntime,
    path: Path,
    *,
    goal: str,
    sheet_name: str | None = None,
) -> PreparedFileAnalysis:
    """Register a file asset, plan it, and initialize the Runtime plan."""
    asset, source = register_file_asset(runtime, path, sheet_name=sheet_name)
    asset_columns = {
        asset.asset_id: [column.name for column in source.scan_schema().schema_snapshot.columns]
    }
    result = DeterministicAnalysisPlanner().plan(
        goal=goal,
        input_asset_ids=[asset.asset_id],
        asset_columns=asset_columns,
    )
    general_plan = result.plan
    runtime_plan = general_plan_to_runtime_plan(general_plan)
    runtime.initialize_plan(runtime_plan)
    scan = source.scan_schema()
    return PreparedFileAnalysis(
        asset=asset,
        general_plan=general_plan,
        runtime_plan=runtime_plan,
        row_estimate=scan.estimated_rows,
    )

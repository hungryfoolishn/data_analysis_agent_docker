from pathlib import Path

import pytest

from langgraph_langchain.runtime.context import AnalysisRuntime
from langgraph_langchain.runtime.plans import AnalysisPlan, default_analysis_plan
from langgraph_langchain.runtime.models import RuntimePlanStep


def test_default_plan_is_reviewable_before_execution(tmp_path: Path):
    runtime = AnalysisRuntime(
        workspace_dir=tmp_path / "session",
        session_id="session",
        question="Analyze revenue by month",
        plan_first=True,
    )

    assert runtime.run.plan is not None
    assert runtime.run.plan["goal"] == "Analyze revenue by month"
    assert len(runtime.run.plan["steps"]) == 5
    assert runtime.run.steps == []
    assert runtime.run.plan["steps"][1]["depends_on"] == [runtime.run.plan["steps"][0]["step_id"]]


def test_strict_plan_rejects_unknown_method_and_unblocks_dependencies(tmp_path: Path):
    runtime = AnalysisRuntime(
        workspace_dir=tmp_path / "session",
        session_id="session",
        question="Analyze revenue",
    )
    invalid = AnalysisPlan(
        goal="Analyze revenue",
        steps=[RuntimePlanStep(objective="Bad", method="not_a_tool")],
    )
    with pytest.raises(ValueError, match="Unknown analysis method"):
        runtime.propose_plan(invalid)

    first = RuntimePlanStep(objective="Load", method="load_data")
    second = RuntimePlanStep(
        objective="Report", method="finish_report", depends_on=[first.step_id]
    )
    runtime.propose_plan(
        AnalysisPlan(goal="Analyze revenue", steps=[first, second], strict_execution=True)
    )
    with pytest.raises(ValueError, match="waiting for dependencies"):
        runtime.start_step(objective="Report", method="finish_report")
    started = runtime.start_step(objective="Load", method="load_data")
    runtime.complete_step(started.step_id)
    assert runtime.start_step(objective="Report", method="finish_report").step_id == second.step_id

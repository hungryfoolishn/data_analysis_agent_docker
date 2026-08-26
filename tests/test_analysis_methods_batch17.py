import json
import logging
from types import SimpleNamespace

import pandas as pd
import pytest

from langgraph_langchain.analysis_methods import (
    AnalysisMethodError,
    analyze_correlation,
    calculate_ratio,
    compare_periods,
    profile_distribution,
)
from langgraph_langchain.tools import get_tools_for_session
from langgraph_langchain.tools.registry import registry
from langgraph_langchain.runtime.context import AnalysisRuntime
from langgraph_langchain.state_machine import AnalysisStage, AnalysisStateMachine


def _frame():
    return pd.DataFrame(
        {
            "date": ["2026-01-05", "2026-01-20", "2026-02-05", "2026-02-20", "2026-03-01"],
            "revenue": [10.0, 20.0, 30.0, 40.0, 50.0],
            "cost": [5.0, 10.0, 10.0, 20.0, 25.0],
        }
    )


def test_distribution_and_correlation_contracts():
    frame = _frame()
    distribution = profile_distribution(frame, metric="revenue")
    assert distribution.quantiles["p50"] == 30.0
    assert distribution.analyzed_rows == 5
    correlation = analyze_correlation(frame, x_field="revenue", y_field="cost")
    assert correlation.sample_size == 5
    assert correlation.correlation == pytest.approx(0.9622504, rel=1e-4)
    assert "not causal" in correlation.non_causal_disclosure


def test_period_comparison_and_ratio_boundaries():
    frame = _frame()
    comparison = compare_periods(frame, date_field="date", metric="revenue")
    assert comparison.previous_period == "2026-02"
    assert comparison.current_period == "2026-03"
    assert comparison.absolute_change == -20.0
    ratio = calculate_ratio(frame, numerator_field="revenue", denominator_field="cost")
    assert ratio.ratio == pytest.approx(150 / 70)
    zero = calculate_ratio(frame.assign(cost=0), numerator_field="revenue", denominator_field="cost")
    assert zero.denominator_zero is True
    assert zero.ratio is None


def test_formal_methods_reject_small_or_invalid_inputs():
    with pytest.raises(AnalysisMethodError, match="at least 4"):
        profile_distribution(_frame().head(3), metric="revenue")
    with pytest.raises(AnalysisMethodError, match="at least 3"):
        analyze_correlation(_frame().head(2), x_field="revenue", y_field="cost")


def test_batch17_tools_are_registered():
    expected = {"profile_distribution", "analyze_correlation", "compare_periods", "calculate_ratio"}
    assert expected.issubset(set(registry.get_tool_names()))


def test_batch17_tools_persist_execution_and_artifacts(tmp_path):
    frame = _frame()
    workspace = tmp_path / "session"
    workspace.mkdir()
    source = workspace / "source.csv"
    frame.to_csv(source, index=False)
    runtime = AnalysisRuntime(workspace_dir=workspace, session_id="session", question="analyze")
    runtime.register_dataframe(dataframe=frame, source_path=source)
    machine = AnalysisStateMachine()
    machine.current_stage = AnalysisStage.BASIC_EDA
    machine.tools_used = ["load_data", "eda_profile"]
    session = SimpleNamespace(
        workspace_dir=workspace, ns={"df": frame}, analysis_runtime=runtime,
        new_artifacts=[], current_runtime_step_id=None, state_machine=machine,
        findings=[], logger=logging.getLogger("test.batch17"),
    )
    calls = [
        ("profile_distribution", {"metric": "revenue"}),
        ("analyze_correlation", {"x_field": "revenue", "y_field": "cost"}),
        ("compare_periods", {"date_field": "date", "metric": "revenue"}),
        ("calculate_ratio", {"numerator_field": "revenue", "denominator_field": "cost"}),
    ]
    tools = {item.name: item for item in get_tools_for_session(session)}
    for name, arguments in calls:
        payload = json.loads(tools[name].invoke(arguments))
        assert payload["artifact"]["artifact_id"]
        assert runtime.executions[-1].tool_name == name
        assert runtime.executions[-1].status == "succeeded"


def test_batch17_remaining_methods_contracts():
    from langgraph_langchain.analysis_methods import analyze_funnel, analyze_retention, test_group_difference, analyze_concentration, run_sensitivity_check
    frame = pd.DataFrame({
        "entity": ["a", "a", "b", "b", "c"],
        "stage": ["view", "buy", "view", "buy", "view"],
        "period": ["2026-01", "2026-02", "2026-01", "2026-02", "2026-01"],
        "group": ["A", "A", "B", "B", "B"],
        "value": [10.0, 12.0, 8.0, 7.0, 6.0],
    })
    funnel = analyze_funnel(frame, entity_field="entity", stage_field="stage", stage_order=["view", "buy"])
    assert funnel.rows[0]["entity_count"] == 3
    assert analyze_retention(frame, entity_field="entity", period_field="period").cohorts
    difference = test_group_difference(frame, metric="value", group_field="group", group_a="A", group_b="B")
    assert difference.sample_size_a == 2 and difference.sample_size_b == 3
    assert analyze_concentration(frame, dimension="group", metric="value", top_n=1).top_n_share > 0
    assert run_sensitivity_check(frame, metric="value").alternatives

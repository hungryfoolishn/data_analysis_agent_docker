from __future__ import annotations

import json
import logging
from types import SimpleNamespace

import pandas as pd
import pytest

from langgraph_langchain.analysis_methods import (
    AnalysisMethodError,
    AnomalyResult,
    ContributionResult,
    GroupComparisonResult,
    TimeTrendResult,
    analyze_time_trend,
    compare_groups,
    decompose_contribution,
    detect_iqr_anomalies,
)
from langgraph_langchain.runtime.context import AnalysisRuntime
from langgraph_langchain.langgraph_agent import _tool_output_status
from langgraph_langchain.state_machine import AnalysisStage, AnalysisStateMachine
from langgraph_langchain.tools import get_tools_for_session
from langgraph_langchain.tools.registry import registry


def _dataframe() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": ["2026-01-05", "2026-01-20", "2026-02-05", "2026-02-20"],
            "region": ["East", "West", "East", "West"],
            "amount": [40.0, 60.0, 70.0, 50.0],
        }
    )


def _session(tmp_path, dataframe=None):
    workspace = tmp_path / "session_tools"
    workspace.mkdir()
    frame = _dataframe() if dataframe is None else dataframe
    source = workspace / "source.csv"
    frame.to_csv(source, index=False)
    runtime = AnalysisRuntime(
        workspace_dir=workspace,
        session_id="session_tools",
        question="analyze",
    )
    runtime.register_dataframe(dataframe=frame, source_path=source)
    machine = AnalysisStateMachine()
    machine.current_stage = AnalysisStage.BASIC_EDA
    machine.tools_used = ["load_data", "eda_profile"]
    return SimpleNamespace(
        workspace_dir=workspace,
        ns={"df": frame},
        analysis_runtime=runtime,
        new_artifacts=[],
        current_runtime_step_id=None,
        state_machine=machine,
        findings=[],
        logger=logging.getLogger("test.analysis_methods"),
    )


def _tool(session, name):
    return next(item for item in get_tools_for_session(session) if item.name == name)


def test_group_comparison_contract_and_values():
    result = compare_groups(
        _dataframe(), dimension="region", metric="amount", aggregation="sum"
    )
    validated = GroupComparisonResult.model_validate(result.model_dump())

    assert validated.grain == "region"
    assert validated.groups[0].group == "East"
    assert validated.groups[0].value == 110.0
    assert validated.groups[0].share_of_aggregate == pytest.approx(0.5)


def test_group_comparison_rejects_invalid_fields_and_cardinality():
    with pytest.raises(AnalysisMethodError, match="Unknown field"):
        compare_groups(_dataframe(), dimension="missing", metric="amount")
    high_cardinality = pd.DataFrame({"group": list("abcd"), "value": [1, 2, 3, 4]})
    with pytest.raises(AnalysisMethodError, match="exceeding max_groups"):
        compare_groups(high_cardinality, dimension="group", metric="value", max_groups=3)


def test_time_trend_contract_and_period_changes():
    result = analyze_time_trend(
        _dataframe(), date_field="date", metric="amount", frequency="month"
    )
    validated = TimeTrendResult.model_validate(result.model_dump())

    assert [row.value for row in validated.periods] == [100.0, 120.0]
    assert validated.periods[1].absolute_change == 20.0
    assert validated.periods[1].percent_change == pytest.approx(0.2)


def test_time_trend_rejects_bad_dates_and_single_period():
    bad = _dataframe().assign(date="not-a-date")
    with pytest.raises(AnalysisMethodError, match="No rows"):
        analyze_time_trend(bad, date_field="date", metric="amount")
    one_period = _dataframe().assign(date="2026-01-01")
    with pytest.raises(AnalysisMethodError, match="at least two"):
        analyze_time_trend(one_period, date_field="date", metric="amount")


def test_contribution_reconciles_latest_two_periods():
    result = decompose_contribution(
        _dataframe(), date_field="date", dimension="region", metric="amount"
    )
    validated = ContributionResult.model_validate(result.model_dump())
    changes = {item.group: item.contribution for item in validated.contributions}

    assert validated.previous_period == "2026-01"
    assert validated.current_period == "2026-02"
    assert validated.absolute_change == 20.0
    assert changes == {"East": 30.0, "West": -10.0}
    assert validated.reconciliation_error == pytest.approx(0.0)


def test_contribution_rejects_zero_previous_total():
    frame = _dataframe().copy()
    frame.loc[:1, "amount"] = 0.0
    with pytest.raises(AnalysisMethodError, match="Previous-period total is zero"):
        decompose_contribution(
            frame, date_field="date", dimension="region", metric="amount"
        )


def test_iqr_anomaly_contract_and_no_anomaly_case():
    frame = pd.DataFrame({"amount": [10, 10, 11, 9, 10, 100]})
    result = AnomalyResult.model_validate(
        detect_iqr_anomalies(frame, metric="amount").model_dump()
    )
    assert result.anomaly_count == 1
    assert result.anomalies[0].value == 100.0

    none = detect_iqr_anomalies(pd.DataFrame({"amount": [1, 2, 3, 4]}), metric="amount")
    assert none.anomaly_count == 0


@pytest.mark.parametrize(
    ("name", "arguments", "model", "rows_key"),
    [
        ("compare_groups", {"dimension": "region", "metric": "amount", "aggregation": "sum"}, GroupComparisonResult, "groups"),
        ("analyze_time_trend", {"date_field": "date", "metric": "amount"}, TimeTrendResult, "periods"),
        ("decompose_contribution", {"date_field": "date", "dimension": "region", "metric": "amount"}, ContributionResult, "contributions"),
        ("detect_anomalies", {"metric": "amount"}, AnomalyResult, "anomalies"),
    ],
)
def test_formal_tools_persist_execution_and_table_artifact(
    tmp_path, name, arguments, model, rows_key
):
    session = _session(tmp_path)
    payload = json.loads(_tool(session, name).invoke(arguments))

    model.model_validate(payload)
    assert rows_key in payload
    assert payload["artifact"]["artifact_id"]
    assert session.analysis_runtime.executions[-1].tool_name == name
    assert session.analysis_runtime.executions[-1].status == "succeeded"
    assert session.analysis_runtime.executions[-1].output_artifact_ids == [
        payload["artifact"]["artifact_id"]
    ]
    artifact = session.analysis_runtime.artifacts[payload["artifact"]["artifact_id"]]
    assert artifact.artifact_type == "table"
    assert artifact.input_asset_ids == session.analysis_runtime.task.input_asset_ids
    assert (session.workspace_dir / artifact.name).exists()


def test_formal_tool_failure_is_recorded(tmp_path):
    session = _session(tmp_path)
    payload = json.loads(
        _tool(session, "compare_groups").invoke(
            {"dimension": "missing", "metric": "amount"}
        )
    )

    assert "error" in payload
    assert session.analysis_runtime.executions[-1].status == "failed"
    assert session.analysis_runtime.executions[-1].error["type"] == "AnalysisMethodError"


def test_all_formal_method_tools_are_discovered():
    expected = {
        "compare_groups",
        "analyze_time_trend",
        "decompose_contribution",
        "detect_anomalies",
    }
    assert expected.issubset(set(registry.get_tool_names()))


def test_structured_tool_error_marks_runtime_step_failed():
    assert _tool_output_status('{"error": "invalid dimension"}') == "failed"

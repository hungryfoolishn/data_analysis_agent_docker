"""B9.2: macro transform engine tests (pure functions + runtime operation)."""

from __future__ import annotations

import pandas as pd
import pytest

from langgraph_langchain.runtime.analysis_operations import (
    execute_analysis_operation,
)
from langgraph_langchain.runtime.general_planner import validate_general_plan
from langgraph_langchain.runtime.macro.models import MacroTimeSemantics
from langgraph_langchain.runtime.general_planner import GeneralAnalysisPlan
from langgraph_langchain.runtime.macro.transforms import (
    MacroTransformError,
    annualized,
    apply_macro_transform,
    cumulative_to_single_period,
    index_change,
    mom,
    period_comparison,
    qoq,
    rolling_average,
    yoy,
)


def _monthly_frame():
    periods = [f"2025-{month:02d}" for month in range(1, 13)] + ["2026-01"]
    values = [100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0,
              107.0, 108.0, 109.0, 110.0, 111.0, 112.0]
    return pd.DataFrame({"period": periods, "value": values})


def _quarterly_cumulative():
    # China-style cumulative quarterly GDP (single year)
    return pd.DataFrame(
        {
            "period": ["2025Q1", "2025Q2", "2025Q3", "2025Q4"],
            "value": [300.0, 620.0, 960.0, 1300.0],
        }
    )


def test_yoy_monthly():
    result = yoy(_monthly_frame(), frequency="monthly")
    # 2026-01 vs 2025-01: (112/100 - 1) * 100 = 12.0
    row = result.set_index("period").loc["2026-01"]
    assert row["value"] == pytest.approx(12.0)


def test_yoy_requires_lag_observations():
    with pytest.raises(MacroTransformError):
        yoy(_monthly_frame().head(5), frequency="monthly")


def test_mom_correct():
    result = mom(_monthly_frame(), frequency="monthly")
    row = result.set_index("period").loc["2025-02"]
    assert row["value"] == pytest.approx(1.0)


def test_mom_rejects_non_monthly():
    with pytest.raises(MacroTransformError):
        mom(_monthly_frame(), frequency="quarterly")


def test_qoq_correct():
    frame = pd.DataFrame(
        {"period": ["2025Q1", "2025Q2", "2025Q3"], "value": [100.0, 104.0, 108.16]}
    )
    result = qoq(frame, frequency="quarterly")
    assert result.set_index("period").loc["2025Q2"]["value"] == pytest.approx(4.0)


def test_annualized_compounds_quarterly():
    frame = pd.DataFrame({"period": ["2025Q1", "2025Q2"], "value": [1.0, 1.0]})
    result = annualized(frame, frequency="quarterly")
    # (1 + 0.01)^4 - 1 = ~4.06%
    assert result.set_index("period").loc["2025Q2"]["value"] == pytest.approx(4.06, rel=1e-2)


def test_annualized_rejects_annual_frequency():
    frame = pd.DataFrame({"period": ["2025"], "value": [4.0]})
    with pytest.raises(MacroTransformError):
        annualized(frame, frequency="annual")


def test_index_change_explicit_base():
    frame = pd.DataFrame({"period": ["2025", "2026", "2027"], "value": [100.0, 110.0, 121.0]})
    result = index_change(frame, frequency="annual", base_period="2025")
    assert result.set_index("period").loc["2027"]["value"] == pytest.approx(21.0)


def test_index_change_unknown_base_fails():
    with pytest.raises(MacroTransformError):
        index_change(_monthly_frame(), frequency="monthly", base_period="1999-01")


def test_rolling_average_window():
    result = rolling_average(_monthly_frame(), frequency="monthly", window=3)
    row = result.set_index("period").loc["2025-03"]
    assert row["value"] == pytest.approx(101.0)


def test_rolling_average_window_too_large():
    with pytest.raises(MacroTransformError):
        rolling_average(_monthly_frame(), frequency="monthly", window=50)


def test_period_comparison_difference():
    frame = pd.DataFrame({"period": ["2025", "2026"], "value": [100.0, 112.0]})
    result = period_comparison(frame, frequency="annual", base_period="2025", compare_period="2026")
    difference = result[result["role"] == "difference"]
    assert difference["value"].iloc[0] == pytest.approx(12.0)


def test_period_comparison_missing_period_fails():
    with pytest.raises(MacroTransformError):
        period_comparison(_monthly_frame(), frequency="monthly", base_period="2025-01", compare_period="2027-01")


# ---------------------------------------------------------------------------
# cumulative_to_single_period (frozen rules §5.3)
# ---------------------------------------------------------------------------

def test_cumulative_to_single_period_single_year():
    result = cumulative_to_single_period(_quarterly_cumulative(), frequency="quarterly")
    values = result.set_index("period")["value"]
    assert values["2025Q1"] == pytest.approx(300.0)
    assert values["2025Q2"] == pytest.approx(320.0)   # 620 - 300
    assert values["2025Q3"] == pytest.approx(340.0)   # 960 - 620
    assert values["2025Q4"] == pytest.approx(340.0)   # 1300 - 960


def test_cumulative_to_single_period_resets_across_years():
    frame = pd.DataFrame(
        {
            "period": ["2025Q1", "2025Q2", "2026Q1", "2026Q2"],
            "value": [300.0, 620.0, 320.0, 660.0],
        }
    )
    result = cumulative_to_single_period(frame, frequency="quarterly")
    values = result.set_index("period")["value"]
    assert values["2026Q1"] == pytest.approx(320.0)  # annual boundary reset
    assert values["2026Q2"] == pytest.approx(340.0)  # 660 - 320, NOT 660 - 620


def test_cumulative_to_single_period_missing_q1_fails():
    frame = pd.DataFrame({"period": ["2025Q2"], "value": [620.0]})
    with pytest.raises(MacroTransformError):
        cumulative_to_single_period(frame, frequency="quarterly")


def test_cumulative_to_single_period_gap_fails():
    frame = pd.DataFrame(
        {"period": ["2025Q1", "2025Q3"], "value": [300.0, 960.0]}
    )
    with pytest.raises(MacroTransformError):
        cumulative_to_single_period(frame, frequency="quarterly")


# ---------------------------------------------------------------------------
# semantics guards (B9 §5.5)
# ---------------------------------------------------------------------------

def test_growth_rate_input_rejects_recompute():
    frame = _monthly_frame()
    # Published YoY data: level_kind="growth_rate" forbids re-diffing (B9 §5.5).
    semantics = MacroTimeSemantics(
        frequency="monthly", seasonal_adjustment="sa", price_basis="unknown",
        cumulative=False, level_kind="growth_rate",
    )
    with pytest.raises(MacroTransformError):
        apply_macro_transform(
            frame,
            transform="yoy",
            frequency="monthly",
            semantics=semantics,
        )


def test_nsa_input_produces_warning():
    result, warnings = apply_macro_transform(
        _monthly_frame(),
        transform="yoy",
        frequency="monthly",
        semantics=MacroTimeSemantics(
            frequency="monthly",
            seasonal_adjustment="nsa",
            price_basis="index",
        ),
    )
    assert any("seasonal" in warning for warning in warnings)


def test_cumulative_input_rejects_mom():
    frame = pd.DataFrame({"period": ["2025-01", "2025-02"], "value": [1.0, 2.0]})
    with pytest.raises(MacroTransformError):
        apply_macro_transform(
            frame,
            transform="mom",
            frequency="monthly",
            semantics=MacroTimeSemantics(
                frequency="monthly",
                seasonal_adjustment="sa",
                price_basis="unknown",
                cumulative=True,
            ),
        )


def test_unknown_transform_rejected():
    with pytest.raises(MacroTransformError):
        apply_macro_transform(_monthly_frame(), transform="magic", frequency="monthly")


# ---------------------------------------------------------------------------
# Runtime operation integration (execute_analysis_operation)
# ---------------------------------------------------------------------------

def test_runtime_operation_executes_macro_transform():
    result = execute_analysis_operation(
        _monthly_frame(),
        operation="macro_transform",
        parameters={
            "transform": "yoy",
            "column": "value",
            "time_column": "period",
            "frequency": "monthly",
        },
    )
    assert result.set_index("period").loc["2026-01"]["value"] == pytest.approx(12.0)


def test_runtime_operation_wraps_transform_errors():
    with pytest.raises(Exception, match="whitelisted|Unknown macro transform"):
        execute_analysis_operation(
            _monthly_frame(),
            operation="macro_transform",
            parameters={"transform": "not_a_transform", "column": "value", "time_column": "period"},
        )


def test_macro_transform_in_general_plan_whitelist():
    from langgraph_langchain.runtime.general_planner import PLAN_OPERATIONS

    assert "macro_transform" in PLAN_OPERATIONS
    from langgraph_langchain.runtime.general_planner import GeneralPlanStep

    step = GeneralPlanStep(
        step_id="step_" + "a" * 32,
        operation="macro_transform",
        input_refs=["asset_1"],
        parameters={
            "transform": "yoy",
            "column": "value",
            "time_column": "period",
            "frequency": "monthly",
        },
        expected_output="dataframe",
    )
    plan = GeneralAnalysisPlan(
        user_goal="macro yoy", input_asset_ids=["asset_1"], steps=[step]
    )
    from langgraph_langchain.runtime.general_planner import validate_general_plan

    assert validate_general_plan(plan, asset_columns={"asset_1": ["value"]}) == []

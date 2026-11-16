"""B9.8.3: statistical kind modeling and YoY-rate normalization tests.

The P1 being pinned: NBS publishes CPI as a "same period last year = 100"
index (100.2 = +0.2%); that value must never flow into a comparison frame as
if it were a YoY percentage.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from langgraph_langchain.runtime.macro.models import MacroIndicator
from langgraph_langchain.runtime.macro.statistics import (
    STATISTICS_VERSION,
    NormalizedRateResult,
    StatisticSemanticsError,
    normalize_to_yoy_rate,
    resolve_statistic_kind,
)


def _indicator(**overrides):
    payload = {
        "indicator_id": "us_cpi_monthly",
        "concept": "cpi",
        "country": "us",
        "source_id": "fred",
        "dataset_id": "fred_cpi",
        "unit": "index_1982_1984_100",
        "frequency": "monthly",
        "seasonal_adjustment": "sa",
        "price_basis": "index",
        "level_kind": "index",
        "base_period": "1982-1984",
        "statistic_kind": "index_level",
        "supported_transforms": ["yoy"],
    }
    payload.update(overrides)
    return MacroIndicator(**payload)


# ---------------------------------------------------------------------------
# kind resolution: declared wins; legacy derivation is a documented whitelist
# ---------------------------------------------------------------------------


def test_declared_statistic_kind_wins():
    ind = _indicator(statistic_kind="index_level")
    assert resolve_statistic_kind(ind) == "index_level"


def test_legacy_derivation_whitelist():
    # legacy entries without statistic_kind
    assert resolve_statistic_kind(_indicator(statistic_kind=None)) == "index_level"
    assert resolve_statistic_kind(_indicator(
        statistic_kind=None, level_kind="level", price_basis="real",
    )) == "level"
    # growth_rate family resolved ONLY by exact known units
    assert resolve_statistic_kind(_indicator(
        statistic_kind=None, level_kind="growth_rate",
        unit="same_period_last_year_100",
    )) == "yoy_index"
    assert resolve_statistic_kind(_indicator(
        statistic_kind=None, level_kind="growth_rate", unit="percent",
    )) == "yoy_rate"
    # anything else → unknown (never guessed)
    assert resolve_statistic_kind(_indicator(
        statistic_kind=None, level_kind="growth_rate", unit="weird_index_100",
    )) == "unknown"


def test_illegal_statistic_kind_rejected_by_model():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _indicator(statistic_kind="absolute_vibes")


# ---------------------------------------------------------------------------
# yoy_index → percent (the P1 fix), exact values from the conversion table
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        (100.0, 0.0),
        (100.2, 0.2),
        (101.5, 1.5),
        (102.5, 2.5),
        (99.8, -0.2),
    ],
)
def test_yoy_index_minus_100_exact_values(raw_value, expected):
    ind = _indicator(
        indicator_id="cn_cpi_yoy_annual", concept="cpi", country="cn",
        source_id="nbs", dataset_id="nbs_cpi",
        unit="same_period_last_year_100", frequency="annual",
        seasonal_adjustment="unknown", price_basis="index",
        level_kind="growth_rate", base_period="same_period_last_year",
        statistic_kind="yoy_index",
        supported_transforms=["rolling_average"],
    )
    frame = pd.DataFrame({"period": ["2025"], "value": [raw_value]})
    result = normalize_to_yoy_rate(frame, ind)
    assert isinstance(result, NormalizedRateResult)
    assert result.output_statistic_kind == "yoy_rate"
    assert result.output_unit == "percent"
    assert result.conversion_method == "yoy_index_minus_100"
    assert result.transform_version == STATISTICS_VERSION
    assert result.dataframe.loc[0, "value"] == pytest.approx(expected)


def test_yoy_index_nan_stays_nan_never_minus_100():
    ind = _indicator(
        indicator_id="cn_cpi_yoy_annual", concept="cpi", country="cn",
        source_id="nbs", dataset_id="nbs_cpi",
        unit="same_period_last_year_100", frequency="annual",
        level_kind="growth_rate", statistic_kind="yoy_index",
    )
    frame = pd.DataFrame({"period": ["2024", "2025"], "value": [100.2, np.nan]})
    result = normalize_to_yoy_rate(frame, ind)
    assert result.dataframe.loc[0, "value"] == pytest.approx(0.2)
    assert pd.isna(result.dataframe.loc[1, "value"])  # not -100


def test_yoy_index_nonfinite_and_nonpositive_rejected():
    ind = _indicator(
        indicator_id="cn_cpi_yoy_annual", concept="cpi", country="cn",
        source_id="nbs", dataset_id="nbs_cpi",
        unit="same_period_last_year_100", frequency="annual",
        level_kind="growth_rate", statistic_kind="yoy_index",
    )
    with pytest.raises(StatisticSemanticsError, match="non-finite"):
        normalize_to_yoy_rate(
            pd.DataFrame({"period": ["2024"], "value": [np.inf]}), ind
        )
    with pytest.raises(StatisticSemanticsError, match="positive"):
        normalize_to_yoy_rate(
            pd.DataFrame({"period": ["2024"], "value": [-0.5]}), ind
        )


# ---------------------------------------------------------------------------
# yoy_rate passthrough: never shifted by 100 again
# ---------------------------------------------------------------------------


def test_yoy_rate_passthrough_not_re_shifted():
    ind = _indicator(
        indicator_id="us_cpi_yoy_pct", concept="cpi",
        unit="percent", level_kind="growth_rate", statistic_kind="yoy_rate",
        price_basis="index",
    )
    frame = pd.DataFrame({"period": ["2025"], "value": [3.1]})
    result = normalize_to_yoy_rate(frame, ind)
    assert result.conversion_method == "passthrough_yoy_rate"
    assert result.dataframe.loc[0, "value"] == pytest.approx(3.1)  # NOT -96.9


# ---------------------------------------------------------------------------
# index_level / level → PeriodIndex-aware yoy
# ---------------------------------------------------------------------------


def test_index_level_converts_via_period_lag():
    ind = _indicator()  # us cpi monthly index level
    months = pd.PeriodIndex(
        [pd.Period(f"2024-{m:02d}", freq="M") for m in range(1, 13)]
        + [pd.Period(f"2025-{m:02d}", freq="M") for m in range(1, 13)]
    )
    frame = pd.DataFrame({"period": months, "value": np.where(
        months.year == 2025,
        100.0 * 1.03,  # +3% vs same month 2024
        100.0,
    )})
    result = normalize_to_yoy_rate(frame, ind)
    assert result.input_statistic_kind == "index_level"
    assert result.output_statistic_kind == "yoy_rate"
    values = result.dataframe.set_index("period")["value"]
    # transform output period contract: string period labels
    assert values["2025-06"] == pytest.approx(3.0)


def test_level_converted_with_basis_carried():
    ind = _indicator(
        indicator_id="cn_gdp_annual", concept="gdp", country="cn",
        source_id="nbs", dataset_id="nbs_gdp",
        unit="100_million_cny", frequency="annual", price_basis="current",
        level_kind="level", statistic_kind="level",
        supported_transforms=["index_change"],
    )
    frame = pd.DataFrame({
        "period": ["2023", "2024", "2025"],
        "value": [100.0, 105.0, 110.25],
    })
    result = normalize_to_yoy_rate(frame, ind)
    values = result.dataframe.set_index("period")["value"]
    assert values["2024"] == pytest.approx(5.0)
    assert values["2025"] == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# unsupported kinds fail explicitly — no silent guessing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["mom_rate", "qoq_rate", "unknown"])
def test_unsupported_kinds_rejected(kind):
    ind = _indicator(statistic_kind=kind, level_kind="level")
    with pytest.raises(StatisticSemanticsError, match="refusing to guess"):
        normalize_to_yoy_rate(
            pd.DataFrame({"period": ["2024"], "value": [1.0]}), ind
        )


def test_legacy_unknown_derivation_rejected():
    ind = _indicator(
        statistic_kind=None, level_kind="growth_rate", unit="weird_index_100",
    )
    with pytest.raises(StatisticSemanticsError, match="refusing to guess"):
        normalize_to_yoy_rate(
            pd.DataFrame({"period": ["2024"], "value": [100.2]}), ind
        )


def test_input_frame_not_mutated():
    ind = _indicator(
        indicator_id="cn_cpi_yoy_annual", concept="cpi", country="cn",
        source_id="nbs", dataset_id="nbs_cpi",
        unit="same_period_last_year_100", frequency="annual",
        level_kind="growth_rate", statistic_kind="yoy_index",
    )
    frame = pd.DataFrame({"period": ["2025"], "value": [100.2]})
    original = frame.copy()
    normalize_to_yoy_rate(frame, ind)
    pd.testing.assert_frame_equal(frame, original)

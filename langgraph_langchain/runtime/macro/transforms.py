"""Macro transform engine (B9.2): pure functions over period/value frames.

Each transform is a deterministic pure function:
    input  = DataFrame with a period/time column and a value column
    output = transformed DataFrame (period/value)
plus warnings.  No DataAsset registration, Runtime state or Evidence side
effects happen here — those belong to the Runtime operation wrapper
(`analysis_operations.execute_macro_transform`) and the Evidence layer.

Frozen semantics: B9.0 design §6 and task §5.2–5.5 (comparability,
cumulative-to-single-period rules, annualization rules, published-rate rule).
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from langgraph_langchain.runtime.macro.models import MacroTimeSemantics

__all__ = [
    "MacroTransformError",
    "apply_macro_transform",
    "cumulative_to_single_period",
    "yoy",
    "mom",
    "qoq",
    "annualized",
    "index_change",
    "rolling_average",
    "period_comparison",
]

TRANSFORM_VERSION = "b9.2.0"

_LAG_BY_FREQUENCY = {"monthly": 12, "quarterly": 4, "annual": 1, "weekly": 52, "daily": 365}


class MacroTransformError(ValueError):
    """Raised when a transform cannot be applied to the given input."""


_PANDAS_FREQ = {"monthly": "M", "quarterly": "Q", "annual": "Y", "weekly": "W", "daily": "D"}


def _period_series(frame: pd.DataFrame, time_column: str, frequency: str) -> pd.PeriodIndex:
    alias = _PANDAS_FREQ.get(frequency)
    if alias is None:
        raise MacroTransformError(f"Unsupported frequency: {frequency!r}")
    column = frame[time_column]
    if pd.api.types.is_period_dtype(column):
        # Period-typed input (e.g. from the comparison pipeline) — pandas 4
        # rejects to_datetime on PeriodDtype; use the periods directly.
        try:
            return pd.PeriodIndex(column, freq=alias)
        except (TypeError, ValueError, OverflowError) as exc:
            raise MacroTransformError(
                f"Cannot use '{time_column}' as {frequency} periods: {exc}"
            ) from exc
    try:
        periods = pd.PeriodIndex(pd.to_datetime(column), freq=alias)
    except (TypeError, ValueError, OverflowError) as exc:
        raise MacroTransformError(
            f"Cannot parse time column '{time_column}' as {frequency} periods: {exc}"
        ) from exc
    return periods


def _prepared(frame: pd.DataFrame, time_column: str, value_column: str, frequency: str):
    if time_column not in frame.columns or value_column not in frame.columns:
        raise MacroTransformError(
            f"Input frame must contain columns '{time_column}' and '{value_column}'"
        )
    periods = _period_series(frame, time_column, frequency)
    series = pd.to_numeric(frame[value_column], errors="coerce")
    ordered = pd.DataFrame({"period": periods, "value": series}).dropna(subset=["period"])
    ordered = ordered.sort_values("period").reset_index(drop=True)
    if ordered["value"].isna().all():
        raise MacroTransformError("Value column has no numeric observations")
    # Duplicate periods corrupt lag arithmetic (Series.get returns sub-Series
    # on a duplicated index); every transform requires unique period keys and
    # fails fast instead (B9.8.2 §4.5 duplicate-period rule).
    duplicated = int(ordered.duplicated(subset=["period"]).sum())
    if duplicated:
        raise MacroTransformError(
            f"Input contains {duplicated} duplicate {frequency} period(s); "
            "deduplicate or aggregate before transforming"
        )
    return ordered


def _pct_change(ordered: pd.DataFrame, lag: int, transform: str, frequency: str) -> pd.DataFrame:
    """Period-aware percentage change: uses PeriodIndex arithmetic, not row shift."""
    values = ordered.set_index("period")["value"]
    alias = _PANDAS_FREQ[frequency]
    periods = pd.PeriodIndex(values.index, freq=alias)
    series = pd.Series(values.values, index=periods)
    lagged = pd.Series(
        [series.get(p - lag, None) for p in periods],
        index=periods,
    )
    result = ((series - lagged) / lagged) * 100.0
    result = result.dropna()
    # Convert PeriodIndex back to period strings for output
    out = pd.DataFrame({"period": [str(p) for p in result.index], "value": result.values})
    return out


def yoy(frame: pd.DataFrame, *, time_column: str = "period", value_column: str = "value",
        frequency: str = "monthly", **_: Any) -> pd.DataFrame:
    """Year-over-year percentage change: (v_t / v_{t-lag} - 1) * 100.

    Annual series use lag=1 (previous calendar year) — required for
    cross-country GDP growth comparison (B9.8.2 §4.5).
    """
    lag = _LAG_BY_FREQUENCY.get(frequency)
    if not lag:
        raise MacroTransformError(
            f"yoy requires a known frequency, got {frequency!r}"
        )
    ordered = _prepared(frame, time_column, value_column, frequency)
    if len(ordered) <= lag:
        raise MacroTransformError(
            f"yoy requires more than {lag} observations, got {len(ordered)}"
        )
    return _pct_change(ordered, lag, "yoy", frequency)


def mom(frame: pd.DataFrame, *, time_column: str = "period", value_column: str = "value",
        frequency: str = "monthly", **_: Any) -> pd.DataFrame:
    if frequency != "monthly":
        raise MacroTransformError(f"mom requires monthly frequency, got {frequency!r}")
    ordered = _prepared(frame, time_column, value_column, frequency)
    if len(ordered) < 2:
        raise MacroTransformError("mom requires at least two observations")
    return _pct_change(ordered, 1, "mom", frequency)


def qoq(frame: pd.DataFrame, *, time_column: str = "period", value_column: str = "value",
        frequency: str = "quarterly", **_: Any) -> pd.DataFrame:
    if frequency != "quarterly":
        raise MacroTransformError(f"qoq requires quarterly frequency, got {frequency!r}")
    ordered = _prepared(frame, time_column, value_column, frequency)
    if len(ordered) < 2:
        raise MacroTransformError("qoq requires at least two observations")
    return _pct_change(ordered, 1, "qoq", frequency)


def annualized(frame: pd.DataFrame, *, time_column: str = "period", value_column: str = "value",
               frequency: str = "monthly", basis: str = "prior_change", **_: Any) -> pd.DataFrame:
    """Compound-annualize a prior-change series.

    basis="prior_change": the value column holds period-over-period percentage
    changes (qoq for quarterly, mom for monthly).  Annual frequency is
    rejected (annual data must not be multiplied by a frequency factor).
    """
    if frequency == "annual":
        raise MacroTransformError(
            "annualized must not multiply annual data by a frequency factor"
        )
    factor = {"monthly": 12, "quarterly": 4}.get(frequency)
    if factor is None:
        raise MacroTransformError(f"annualized supports monthly/quarterly, got {frequency!r}")
    if basis != "prior_change":
        raise MacroTransformError("annualized basis must be 'prior_change'")
    ordered = _prepared(frame, time_column, value_column, frequency)
    rate = ordered["value"] / 100.0
    result = ((1.0 + rate) ** factor - 1.0) * 100.0
    out = pd.DataFrame({"period": ordered["period"], "value": result})
    return out.dropna(subset=["value"]).reset_index(drop=True)


def cumulative(frame: pd.DataFrame, *, time_column: str = "period", value_column: str = "value",
               frequency: str = "monthly", **_: Any) -> pd.DataFrame:
    """Year-to-date cumulative sum of a flow series."""
    if frequency not in {"monthly", "quarterly", "weekly", "daily"}:
        raise MacroTransformError(f"cumulative requires a sub-annual flow series, got {frequency!r}")
    ordered = _prepared(frame, time_column, value_column, frequency)
    periods = pd.PeriodIndex(ordered["period"], freq=frequency)
    cumulative_values = ordered.groupby(periods.year)["value"].cumsum()
    return pd.DataFrame({"period": ordered["period"], "value": cumulative_values}).reset_index(drop=True)


def index_change(frame: pd.DataFrame, *, time_column: str = "period", value_column: str = "value",
                 frequency: str = "monthly", base_period: str | None = None, **_: Any) -> pd.DataFrame:
    """(v_t / v_base - 1) * 100 against an explicit or first-observation base."""
    ordered = _prepared(frame, time_column, value_column, frequency)
    series = ordered.set_index("period")["value"]
    base = base_period
    if base is None:
        base = ordered.iloc[0]["period"]
    if base not in series.index:
        raise MacroTransformError(f"base period {base!r} not present in input")
    base_value = series.loc[base]
    if base_value in (0, None) or pd.isna(base_value):
        raise MacroTransformError(f"base period value is zero/missing: {base!r}")
    result = ((series / base_value) - 1.0) * 100.0
    result = result.dropna().reset_index()
    result.columns = ["period", "value"]
    return result


def rolling_average(frame: pd.DataFrame, *, time_column: str = "period", value_column: str = "value",
                    frequency: str = "monthly", window: int = 3, **_: Any) -> pd.DataFrame:
    if not isinstance(window, int) or window < 2:
        raise MacroTransformError("rolling_average requires integer window >= 2")
    ordered = _prepared(frame, time_column, value_column, frequency)
    series = ordered.set_index("period")["value"]
    if len(series) < window:
        raise MacroTransformError(
            f"rolling_average window {window} exceeds observations {len(series)}"
        )
    result = series.rolling(window).mean().dropna().reset_index()
    result.columns = ["period", "value"]
    return result


def period_comparison(
    frame: pd.DataFrame,
    *,
    time_column: str = "period",
    value_column: str = "value",
    frequency: str = "monthly",
    base_period: str | None = None,
    compare_period: str | None = None,
    **_: Any,
) -> pd.DataFrame:
    """Level difference between two explicit periods (both required)."""
    if not base_period or not compare_period:
        raise MacroTransformError("period_comparison requires base_period and compare_period")
    ordered = _prepared(frame, time_column, value_column, frequency)
    series = ordered.set_index("period")["value"]
    for required in (base_period, compare_period):
        if required not in series.index:
            raise MacroTransformError(f"period {required!r} not present in input")
    base_value = float(series.loc[base_period])
    compare_value = float(series.loc[compare_period])
    return pd.DataFrame(
        [
            {"period": base_period, "value": base_value, "role": "base"},
            {"period": compare_period, "value": compare_value, "role": "compare"},
            {
                "period": f"{compare_period} vs {base_period}",
                "value": compare_value - base_value,
                "role": "difference",
            },
        ],
        columns=["period", "value", "role"],
    )


def cumulative_to_single_period(
    frame: pd.DataFrame, *, time_column: str = "period", value_column: str = "value",
    frequency: str = "quarterly", **_: Any
) -> pd.DataFrame:
    """China cumulative-to-single-period (B9 §5.3, frozen rules).

    Q1 = Q1 cumulative; Q2 = H1 - Q1; Q3 = 9M - H1; Q4 = FY - 9M.
    Annual boundary resets; cross-year subtraction is impossible by
    construction (per-year grouping); missing predecessors fail explicitly;
    input data comes from a single data_version (enforced by the wrapper).
    """
    if frequency != "quarterly":
        raise MacroTransformError(
            f"cumulative_to_single_period requires quarterly frequency, got {frequency!r}"
        )
    ordered = _prepared(frame, time_column, value_column, frequency)
    periods = pd.PeriodIndex(ordered["period"], freq="Q")
    ordered = ordered.assign(_year=periods.year, _quarter=periods.quarter)
    rows: list[dict[str, Any]] = []
    for year, group in ordered.groupby("_year"):
        group = group.sort_values("_quarter")
        if 1 not in set(group["_quarter"]):
            raise MacroTransformError(
                f"Year {year}: Q1 cumulative value missing; cannot derive single periods"
            )
        previous: float | None = None
        seen_quarters: list[int] = []
        for _, row in group.iterrows():
            quarter = int(row["_quarter"])
            expected = max(seen_quarters) + 1 if seen_quarters else 1
            if quarter != expected:
                raise MacroTransformError(
                    f"Year {year}: cumulative quarter sequence gap: expected Q{expected}, got Q{quarter}"
                )
            if quarter == 1:
                single = float(row["value"])
            else:
                single = float(row["value"]) - previous  # previous is never None here
            previous = float(row["value"])
            seen_quarters.append(quarter)
            rows.append(
                {"period": f"{year}Q{quarter}", "value": single, "role": "single_period"}
            )
    return pd.DataFrame(rows, columns=["period", "value", "role"])

TRANSFORM_FUNCTIONS = {
    "yoy": yoy,
    "mom": mom,
    "qoq": qoq,
    "annualized": annualized,
    "cumulative": cumulative,
    "index_change": index_change,
    "rolling_average": rolling_average,
    "period_comparison": period_comparison,
    "cumulative_to_single_period": cumulative_to_single_period,
}


def apply_macro_transform(
    frame: pd.DataFrame,
    *,
    transform: str,
    time_column: str = "period",
    value_column: str = "value",
    frequency: str = "monthly",
    semantics: MacroTimeSemantics | None = None,
    **parameters: Any,
) -> tuple[pd.DataFrame, list[str]]:
    """Dispatch one macro transform with semantics-aware guards (B9.2).

    Returns ``(result_frame, warnings)``.  Raises ``MacroTransformError`` on
    invalid input, incompatible frequency or non-transformable input kinds.
    """
    fn = TRANSFORM_FUNCTIONS.get(transform)
    if fn is None:
        raise MacroTransformError(f"Unknown macro transform: {transform!r}")

    warnings: list[str] = []
    if transform in {"yoy", "mom", "qoq"} and semantics is not None:
        if semantics.level_kind == "growth_rate":
            raise MacroTransformError(
                f"Input '{semantics.level_kind}' is already a published growth rate; "
                f"computing {transform} again would double-diffuse (B9 §5.5)"
            )
        if semantics.seasonal_adjustment == "nsa":
            warnings.append(
                "input series is not seasonally adjusted; results contain seasonal effects"
            )
    if transform in {"mom", "qoq"} and semantics is not None and semantics.cumulative:
        raise MacroTransformError(
            "cumulative inputs must be converted with cumulative_to_single_period "
            "before MoM/QoQ"
        )

    result = fn(
        frame,
        time_column=time_column,
        value_column=value_column,
        frequency=frequency,
        **parameters,
    )
    return result, warnings

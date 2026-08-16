"""Domain-neutral, structured analysis methods used by formal agent tools."""

from __future__ import annotations

from typing import Any, Literal, Optional

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field


Aggregation = Literal["sum", "mean", "median", "count"]
Frequency = Literal["day", "week", "month", "quarter", "year"]


class AnalysisMethodError(ValueError):
    """Raised when a formal analysis method cannot produce a valid result."""


class ArtifactReference(BaseModel):
    artifact_id: Optional[str] = None
    name: str
    url: str


class GroupComparisonRow(BaseModel):
    group: str
    row_count: int = Field(ge=0)
    non_null_count: int = Field(ge=0)
    value: float
    share_of_aggregate: Optional[float] = None
    rank: int = Field(ge=1)


class GroupComparisonResult(BaseModel):
    method: Literal["group_comparison"] = "group_comparison"
    grain: str
    dimension: str
    metric: str
    aggregation: Aggregation
    source_rows: int = Field(ge=0)
    analyzed_rows: int = Field(ge=0)
    excluded_rows: int = Field(ge=0)
    group_count: int = Field(ge=1)
    groups: list[GroupComparisonRow]
    artifact: Optional[ArtifactReference] = None


class TrendPoint(BaseModel):
    period: str
    value: float
    row_count: int = Field(ge=0)
    absolute_change: Optional[float] = None
    percent_change: Optional[float] = None


class TimeTrendResult(BaseModel):
    method: Literal["time_trend"] = "time_trend"
    grain: str
    date_field: str
    metric: str
    aggregation: Aggregation
    frequency: Frequency
    source_rows: int = Field(ge=0)
    analyzed_rows: int = Field(ge=0)
    excluded_rows: int = Field(ge=0)
    start_period: str
    end_period: str
    periods: list[TrendPoint] = Field(min_length=2)
    artifact: Optional[ArtifactReference] = None


class ContributionRow(BaseModel):
    dimension: str
    group: str
    previous: float
    current: float
    contribution: float
    contribution_share: Optional[float] = None


class ContributionResult(BaseModel):
    method: Literal["contribution_decomposition"] = "contribution_decomposition"
    grain: str
    dimension: str
    metric: str
    date_field: str
    frequency: Frequency
    previous_period: str
    current_period: str
    previous_total: float
    current_total: float
    absolute_change: float
    relative_change: float
    reconciliation_error: float
    broad_based: bool
    coverage_ratio: float = Field(ge=0)
    contributions: list[ContributionRow]
    top_positive_contributors: list[dict[str, Any]]
    top_negative_contributors: list[dict[str, Any]]
    artifact: Optional[ArtifactReference] = None


class AnomalyRow(BaseModel):
    row_index: str
    value: float


class AnomalyResult(BaseModel):
    method: Literal["iqr"] = "iqr"
    grain: Literal["source_row"] = "source_row"
    metric: str
    source_rows: int = Field(ge=0)
    analyzed_rows: int = Field(ge=0)
    excluded_rows: int = Field(ge=0)
    lower_bound: float
    upper_bound: float
    anomaly_count: int = Field(ge=0)
    anomaly_rate: float = Field(ge=0, le=1)
    anomalies: list[AnomalyRow]
    artifact: Optional[ArtifactReference] = None


_PERIOD_CODES: dict[Frequency, str] = {
    "day": "D",
    "week": "W",
    "month": "M",
    "quarter": "Q",
    "year": "Y",
}


def _require_dataframe(df: pd.DataFrame) -> None:
    if not isinstance(df, pd.DataFrame) or df.empty:
        raise AnalysisMethodError("A non-empty DataFrame is required; call load_data first.")


def _require_columns(df: pd.DataFrame, *columns: str) -> None:
    missing = [column for column in columns if column not in df.columns]
    if missing:
        raise AnalysisMethodError(f"Unknown field(s): {', '.join(missing)}.")


def _numeric_series(df: pd.DataFrame, metric: str) -> pd.Series:
    values = pd.to_numeric(df[metric], errors="coerce")
    if values.notna().sum() == 0:
        raise AnalysisMethodError(f"Metric '{metric}' has no numeric values.")
    return values


def _period_series(df: pd.DataFrame, date_field: str, frequency: Frequency) -> pd.Series:
    try:
        dates = pd.to_datetime(df[date_field], errors="coerce")
        return dates.dt.to_period(_PERIOD_CODES[frequency])
    except (TypeError, ValueError, AttributeError) as exc:
        raise AnalysisMethodError(
            f"Field '{date_field}' cannot be interpreted as datetimes: {exc}"
        ) from exc


def _aggregate(grouped, aggregation: Aggregation) -> pd.Series:
    if aggregation == "sum":
        return grouped.sum(min_count=1)
    if aggregation == "mean":
        return grouped.mean()
    if aggregation == "median":
        return grouped.median()
    return grouped.count()


def compare_groups(
    df: pd.DataFrame,
    *,
    dimension: str,
    metric: str,
    aggregation: Aggregation = "mean",
    max_groups: int = 50,
) -> GroupComparisonResult:
    _require_dataframe(df)
    _require_columns(df, dimension, metric)
    if not 2 <= max_groups <= 200:
        raise AnalysisMethodError("max_groups must be between 2 and 200.")

    values = _numeric_series(df, metric)
    dimensions = df[dimension].astype("object").where(df[dimension].notna(), "<MISSING>")
    group_count = int(dimensions.nunique(dropna=False))
    if group_count < 2:
        raise AnalysisMethodError(f"Dimension '{dimension}' must contain at least two groups.")
    if group_count > max_groups:
        raise AnalysisMethodError(
            f"Dimension '{dimension}' has {group_count} groups, exceeding max_groups={max_groups}. "
            "Choose a lower-cardinality business dimension."
        )

    working = pd.DataFrame({"group": dimensions, "value": values})
    grouped = working.groupby("group", dropna=False, sort=False)["value"]
    aggregates = _aggregate(grouped, aggregation).dropna().sort_values(ascending=False)
    counts = working.groupby("group", dropna=False).size()
    non_null = grouped.count()
    if aggregates.empty:
        raise AnalysisMethodError("No groups contain analyzable metric values.")

    denominator = float(aggregates.sum()) if aggregation in {"sum", "count"} else 0.0
    rows = []
    for rank, (group, value) in enumerate(aggregates.items(), start=1):
        numeric_value = float(value)
        rows.append(
            GroupComparisonRow(
                group=str(group),
                row_count=int(counts.loc[group]),
                non_null_count=int(non_null.loc[group]),
                value=numeric_value,
                share_of_aggregate=(numeric_value / denominator if denominator else None),
                rank=rank,
            )
        )
    analyzed = int(values.notna().sum())
    return GroupComparisonResult(
        grain=dimension,
        dimension=dimension,
        metric=metric,
        aggregation=aggregation,
        source_rows=len(df),
        analyzed_rows=analyzed,
        excluded_rows=len(df) - analyzed,
        group_count=len(rows),
        groups=rows,
    )


def analyze_time_trend(
    df: pd.DataFrame,
    *,
    date_field: str,
    metric: str,
    frequency: Frequency = "month",
    aggregation: Aggregation = "sum",
) -> TimeTrendResult:
    _require_dataframe(df)
    _require_columns(df, date_field, metric)
    values = _numeric_series(df, metric)
    periods = _period_series(df, date_field, frequency)
    working = pd.DataFrame({"period": periods, "value": values}).dropna()
    if working.empty:
        raise AnalysisMethodError("No rows have both a valid datetime and numeric metric value.")

    grouped = working.groupby("period", sort=True)["value"]
    aggregates = _aggregate(grouped, aggregation).dropna()
    if len(aggregates) < 2:
        raise AnalysisMethodError(
            f"Time trend requires at least two non-empty {frequency} periods; found {len(aggregates)}."
        )
    row_counts = grouped.size()
    trend_rows: list[TrendPoint] = []
    previous: Optional[float] = None
    for period, value in aggregates.items():
        current = float(value)
        absolute_change = None if previous is None else current - previous
        percent_change = (
            None if previous in (None, 0.0) else (current - previous) / previous
        )
        trend_rows.append(
            TrendPoint(
                period=str(period),
                value=current,
                row_count=int(row_counts.loc[period]),
                absolute_change=absolute_change,
                percent_change=percent_change,
            )
        )
        previous = current

    return TimeTrendResult(
        grain=f"{frequency}_period",
        date_field=date_field,
        metric=metric,
        aggregation=aggregation,
        frequency=frequency,
        source_rows=len(df),
        analyzed_rows=len(working),
        excluded_rows=len(df) - len(working),
        start_period=trend_rows[0].period,
        end_period=trend_rows[-1].period,
        periods=trend_rows,
    )


def decompose_contribution(
    df: pd.DataFrame,
    *,
    date_field: str,
    dimension: str,
    metric: str,
    frequency: Frequency = "month",
    top_k: int = 5,
    max_groups: int = 100,
) -> ContributionResult:
    _require_dataframe(df)
    _require_columns(df, date_field, dimension, metric)
    if not 1 <= top_k <= 20:
        raise AnalysisMethodError("top_k must be between 1 and 20.")
    if not 2 <= max_groups <= 500:
        raise AnalysisMethodError("max_groups must be between 2 and 500.")

    values = _numeric_series(df, metric)
    periods = _period_series(df, date_field, frequency)
    dimensions = df[dimension].astype("object").where(df[dimension].notna(), "<MISSING>")
    working = pd.DataFrame(
        {"period": periods, "group": dimensions, "value": values}
    ).dropna(subset=["period", "value"])
    unique_periods = sorted(working["period"].unique())
    if len(unique_periods) < 2:
        raise AnalysisMethodError(
            f"Contribution decomposition requires at least two non-empty {frequency} periods."
        )
    if working["group"].nunique(dropna=False) > max_groups:
        raise AnalysisMethodError(
            f"Dimension '{dimension}' exceeds max_groups={max_groups}; choose a business dimension."
        )

    previous_period, current_period = unique_periods[-2:]
    window = working[working["period"].isin([previous_period, current_period])]
    pivot = window.pivot_table(
        index="group", columns="period", values="value", aggfunc="sum", fill_value=0.0
    )
    previous_total = float(pivot.get(previous_period, pd.Series(dtype=float)).sum())
    current_total = float(pivot.get(current_period, pd.Series(dtype=float)).sum())
    if np.isclose(previous_total, 0.0):
        raise AnalysisMethodError(
            f"Previous-period total is zero for {previous_period}; relative change is undefined."
        )
    pivot = pivot.reindex(columns=[previous_period, current_period], fill_value=0.0)
    pivot["contribution"] = pivot[current_period] - pivot[previous_period]
    absolute_change = current_total - previous_total
    contribution_total = float(pivot["contribution"].sum())
    denominator = absolute_change if not np.isclose(absolute_change, 0.0) else None

    rows: list[ContributionRow] = []
    for group, row in pivot.reindex(pivot["contribution"].abs().sort_values(ascending=False).index).iterrows():
        contribution = float(row["contribution"])
        rows.append(
            ContributionRow(
                dimension=dimension,
                group=str(group),
                previous=float(row[previous_period]),
                current=float(row[current_period]),
                contribution=contribution,
                contribution_share=(contribution / denominator if denominator else None),
            )
        )

    positives = sorted(
        (item.model_dump() for item in rows if item.contribution > 0),
        key=lambda item: item["contribution"],
        reverse=True,
    )[:top_k]
    negatives = sorted(
        (item.model_dump() for item in rows if item.contribution < 0),
        key=lambda item: item["contribution"],
    )[:top_k]
    non_zero = [item for item in rows if not np.isclose(item.contribution, 0.0)]
    aligned = 0
    if non_zero and not np.isclose(absolute_change, 0.0):
        aligned = sum(np.sign(item.contribution) == np.sign(absolute_change) for item in non_zero)
    broad_based = bool(non_zero and aligned / len(non_zero) >= 0.6)
    ranked_abs = sorted((abs(item.contribution) for item in rows), reverse=True)
    coverage_denominator = abs(absolute_change) or sum(ranked_abs)
    coverage_ratio = sum(ranked_abs[:top_k]) / coverage_denominator if coverage_denominator else 0.0

    return ContributionResult(
        grain=f"{dimension} x {frequency}_comparison",
        dimension=dimension,
        metric=metric,
        date_field=date_field,
        frequency=frequency,
        previous_period=str(previous_period),
        current_period=str(current_period),
        previous_total=previous_total,
        current_total=current_total,
        absolute_change=absolute_change,
        relative_change=absolute_change / previous_total,
        reconciliation_error=absolute_change - contribution_total,
        broad_based=broad_based,
        coverage_ratio=float(coverage_ratio),
        contributions=rows,
        top_positive_contributors=positives,
        top_negative_contributors=negatives,
    )


def detect_iqr_anomalies(df: pd.DataFrame, *, metric: str) -> AnomalyResult:
    _require_dataframe(df)
    _require_columns(df, metric)
    values = _numeric_series(df, metric)
    clean = values.dropna()
    if len(clean) < 4:
        raise AnalysisMethodError(
            f"IQR anomaly detection requires at least 4 numeric observations; found {len(clean)}."
        )
    q1, q3 = float(clean.quantile(0.25)), float(clean.quantile(0.75))
    iqr = q3 - q1
    lower, upper = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    anomalous = clean[(clean < lower) | (clean > upper)]
    rows = [
        AnomalyRow(row_index=str(index), value=float(value))
        for index, value in anomalous.items()
    ]
    return AnomalyResult(
        metric=metric,
        source_rows=len(df),
        analyzed_rows=len(clean),
        excluded_rows=len(df) - len(clean),
        lower_bound=lower,
        upper_bound=upper,
        anomaly_count=len(rows),
        anomaly_rate=len(rows) / len(clean),
        anomalies=rows,
    )

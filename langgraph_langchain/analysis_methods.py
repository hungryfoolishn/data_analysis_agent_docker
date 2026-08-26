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


class DistributionResult(BaseModel):
    method: Literal["profile_distribution"] = "profile_distribution"
    grain: Literal["source_rows"] = "source_rows"
    metric: str
    source_rows: int = Field(ge=0)
    analyzed_rows: int = Field(ge=0)
    excluded_rows: int = Field(ge=0)
    quantiles: dict[str, float]
    mean: float
    median: float
    std: float
    skewness: Optional[float] = None
    iqr: float
    lower_fence: float
    upper_fence: float
    long_tail: bool
    rows: list[dict[str, Any]] = Field(default_factory=list)
    disclosure: list[str] = Field(default_factory=list)
    artifact: Optional[ArtifactReference] = None


class CorrelationResult(BaseModel):
    method: Literal["analyze_correlation"] = "analyze_correlation"
    grain: Literal["row_pair"] = "row_pair"
    x_field: str
    y_field: str
    correlation_method: Literal["pearson", "spearman"]
    correlation: float
    sample_size: int = Field(ge=3)
    excluded_rows: int = Field(ge=0)
    missing_handling: Literal["pairwise_complete"] = "pairwise_complete"
    non_causal_disclosure: str
    rows: list[dict[str, Any]] = Field(default_factory=list)
    artifact: Optional[ArtifactReference] = None


class PeriodComparisonResult(BaseModel):
    method: Literal["compare_periods"] = "compare_periods"
    grain: str
    date_field: str
    metric: str
    frequency: Frequency
    aggregation: Aggregation
    previous_period: str
    current_period: str
    previous_value: float
    current_value: float
    absolute_change: float
    percent_change: Optional[float] = None
    comparable: bool = True
    previous_row_count: int = Field(ge=0)
    current_row_count: int = Field(ge=0)
    excluded_rows: int = Field(ge=0)
    periods: list[dict[str, Any]] = Field(default_factory=list)
    disclosure: list[str] = Field(default_factory=list)
    artifact: Optional[ArtifactReference] = None


class RatioResult(BaseModel):
    method: Literal["calculate_ratio"] = "calculate_ratio"
    grain: Literal["whole_dataset"] = "whole_dataset"
    numerator_field: str
    denominator_field: str
    aggregation: Literal["sum", "mean", "count"] = "sum"
    numerator_value: float
    denominator_value: float
    ratio: Optional[float] = None
    denominator_zero: bool
    source_rows: int = Field(ge=0)
    analyzed_rows: int = Field(ge=0)
    excluded_rows: int = Field(ge=0)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    disclosure: list[str] = Field(default_factory=list)
    artifact: Optional[ArtifactReference] = None


def profile_distribution(df: pd.DataFrame, *, metric: str, quantile_points: Optional[list[float]] = None) -> DistributionResult:
    _require_dataframe(df)
    _require_columns(df, metric)
    values = _numeric_series(df, metric).dropna()
    if len(values) < 4:
        raise AnalysisMethodError(f"Distribution profiling requires at least 4 numeric observations; found {len(values)}.")
    points = quantile_points or [0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99]
    if any(point < 0 or point > 1 for point in points):
        raise AnalysisMethodError("quantile_points must be between 0 and 1.")
    quantiles = {f"p{int(point * 100):02d}": float(values.quantile(point)) for point in points}
    q1, q3 = quantiles.get("p25", float(values.quantile(0.25))), quantiles.get("p75", float(values.quantile(0.75)))
    iqr = q3 - q1
    mean, median = float(values.mean()), float(values.median())
    std = float(values.std(ddof=1)) if len(values) > 1 else 0.0
    skew = values.skew()
    skewness = float(skew) if pd.notna(skew) else None
    return DistributionResult(
        metric=metric, source_rows=len(df), analyzed_rows=len(values), excluded_rows=len(df) - len(values),
        quantiles=quantiles, mean=mean, median=median, std=std, skewness=skewness,
        iqr=float(iqr), lower_fence=float(q1 - 1.5 * iqr), upper_fence=float(q3 + 1.5 * iqr),
        long_tail=bool(abs(mean - median) > max(std, 1e-12)),
        rows=[{"statistic": key, "value": value} for key, value in quantiles.items()],
        disclosure=["Statistics are descriptive and do not establish causality."],
    )


def analyze_correlation(df: pd.DataFrame, *, x_field: str, y_field: str, correlation_method: Literal["pearson", "spearman"] = "pearson") -> CorrelationResult:
    _require_dataframe(df)
    _require_columns(df, x_field, y_field)
    pair = pd.DataFrame({"x": pd.to_numeric(df[x_field], errors="coerce"), "y": pd.to_numeric(df[y_field], errors="coerce")}).dropna()
    if len(pair) < 3:
        raise AnalysisMethodError("Correlation requires at least 3 pairwise-complete rows.")
    correlation = float(pair["x"].corr(pair["y"], method=correlation_method))
    if pd.isna(correlation):
        raise AnalysisMethodError("Correlation is undefined when one field has no variation.")
    return CorrelationResult(
        x_field=x_field, y_field=y_field, correlation_method=correlation_method,
        correlation=correlation, sample_size=len(pair), excluded_rows=len(df) - len(pair),
        non_causal_disclosure="Correlation indicates association only; it is not causal evidence.",
        rows=[{"x_field": x_field, "y_field": y_field, "correlation": correlation, "sample_size": len(pair)}],
    )


def compare_periods(df: pd.DataFrame, *, date_field: str, metric: str, frequency: Frequency = "month", aggregation: Aggregation = "sum", current_period: Optional[str] = None, previous_period: Optional[str] = None) -> PeriodComparisonResult:
    _require_dataframe(df)
    _require_columns(df, date_field, metric)
    values = _numeric_series(df, metric)
    periods = _period_series(df, date_field, frequency)
    working = pd.DataFrame({"period": periods, "value": values}).dropna()
    grouped = working.groupby("period", sort=True)["value"]
    aggregates = _aggregate(grouped, aggregation).dropna()
    if len(aggregates) < 2:
        raise AnalysisMethodError("Period comparison requires at least two non-empty periods.")
    available = [str(period) for period in aggregates.index]
    current_key, previous_key = current_period or available[-1], previous_period or available[-2]
    lookup = {str(period): period for period in aggregates.index}
    if current_key not in lookup or previous_key not in lookup:
        raise AnalysisMethodError(f"Requested periods must be available. Available periods: {', '.join(available)}.")
    current_obj, previous_obj = lookup[current_key], lookup[previous_key]
    previous_value, current_value = float(aggregates.loc[previous_obj]), float(aggregates.loc[current_obj])
    absolute_change = current_value - previous_value
    percent_change = None if np.isclose(previous_value, 0.0) else absolute_change / previous_value
    counts = grouped.size()
    rows = [
        {"period": previous_key, "value": previous_value, "row_count": int(counts.loc[previous_obj])},
        {"period": current_key, "value": current_value, "row_count": int(counts.loc[current_obj])},
    ]
    return PeriodComparisonResult(
        grain=f"{frequency}_period", date_field=date_field, metric=metric, frequency=frequency, aggregation=aggregation,
        previous_period=previous_key, current_period=current_key, previous_value=previous_value, current_value=current_value,
        absolute_change=absolute_change, percent_change=percent_change, comparable=not np.isclose(previous_value, 0.0),
        previous_row_count=rows[0]["row_count"], current_row_count=rows[1]["row_count"],
        excluded_rows=len(df) - len(working), periods=rows,
        disclosure=["Periods are compared at the declared frequency and aggregation."],
    )


def calculate_ratio(df: pd.DataFrame, *, numerator_field: str, denominator_field: str, aggregation: Literal["sum", "mean", "count"] = "sum") -> RatioResult:
    _require_dataframe(df)
    _require_columns(df, numerator_field, denominator_field)
    numerator = pd.to_numeric(df[numerator_field], errors="coerce")
    denominator = pd.to_numeric(df[denominator_field], errors="coerce")
    pair = pd.DataFrame({"numerator": numerator, "denominator": denominator}).dropna()
    if pair.empty:
        raise AnalysisMethodError("Ratio requires rows with numeric numerator and denominator.")
    if aggregation == "sum":
        numerator_value, denominator_value = float(pair.numerator.sum()), float(pair.denominator.sum())
    elif aggregation == "mean":
        numerator_value, denominator_value = float(pair.numerator.mean()), float(pair.denominator.mean())
    elif aggregation == "count":
        numerator_value, denominator_value = float(pair.numerator.count()), float(pair.denominator.count())
    else:
        raise AnalysisMethodError("aggregation must be sum, mean, or count.")
    denominator_zero = bool(np.isclose(denominator_value, 0.0))
    ratio = None if denominator_zero else numerator_value / denominator_value
    disclosure = ["Ratio is descriptive; verify numerator and denominator definitions before interpretation."]
    if denominator_zero:
        disclosure.append("Denominator is zero; ratio is undefined.")
    return RatioResult(
        numerator_field=numerator_field, denominator_field=denominator_field, aggregation=aggregation,
        numerator_value=numerator_value, denominator_value=denominator_value, ratio=ratio,
        denominator_zero=denominator_zero, source_rows=len(df), analyzed_rows=len(pair),
        excluded_rows=len(df) - len(pair), rows=[{"numerator": numerator_value, "denominator": denominator_value, "ratio": ratio}],
        disclosure=disclosure,
    )


class FunnelResult(BaseModel):
    method: Literal["analyze_funnel"] = "analyze_funnel"
    entity_field: str
    stage_field: str
    stages: list[str]
    rows: list[dict[str, Any]]
    disclosure: list[str]
    artifact: Optional[ArtifactReference] = None


class RetentionResult(BaseModel):
    method: Literal["analyze_retention"] = "analyze_retention"
    entity_field: str
    period_field: str
    cohort_field: Optional[str] = None
    cohorts: list[dict[str, Any]]
    disclosure: list[str]
    artifact: Optional[ArtifactReference] = None


class GroupDifferenceResult(BaseModel):
    method: Literal["test_group_difference"] = "test_group_difference"
    metric: str
    group_field: str
    group_a: str
    group_b: str
    mean_a: float
    mean_b: float
    difference: float
    pooled_std: Optional[float] = None
    effect_size: Optional[float] = None
    sample_size_a: int = Field(ge=2)
    sample_size_b: int = Field(ge=2)
    disclosure: list[str]
    artifact: Optional[ArtifactReference] = None


class ConcentrationResult(BaseModel):
    method: Literal["analyze_concentration"] = "analyze_concentration"
    dimension: str
    metric: str
    top_n: int
    total_value: float
    top_n_value: float
    top_n_share: Optional[float]
    hhi: Optional[float]
    groups: list[dict[str, Any]]
    disclosure: list[str]
    artifact: Optional[ArtifactReference] = None


class SensitivityResult(BaseModel):
    method: Literal["run_sensitivity_check"] = "run_sensitivity_check"
    metric: str
    baseline: float
    alternatives: list[dict[str, Any]]
    stable: bool
    disclosure: list[str]
    artifact: Optional[ArtifactReference] = None


def analyze_funnel(df: pd.DataFrame, *, entity_field: str, stage_field: str, stage_order: list[str]) -> FunnelResult:
    _require_dataframe(df)
    _require_columns(df, entity_field, stage_field)
    if len(stage_order) < 2 or len(set(stage_order)) != len(stage_order):
        raise AnalysisMethodError("stage_order must contain at least two unique stages.")
    working = df[[entity_field, stage_field]].dropna().drop_duplicates()
    rows=[]; first_count=None
    for stage in stage_order:
        count=int(working.loc[working[stage_field].astype(str)==str(stage), entity_field].nunique())
        if first_count is None: first_count=count
        rows.append({"stage": str(stage), "entity_count": count, "conversion_from_first": (count/first_count if first_count else None)})
    return FunnelResult(entity_field=entity_field, stage_field=stage_field, stages=[str(x) for x in stage_order], rows=rows, disclosure=["Entities are deduplicated within each stage; conversion is descriptive, not causal."])


def analyze_retention(df: pd.DataFrame, *, entity_field: str, period_field: str, cohort_field: Optional[str] = None) -> RetentionResult:
    _require_dataframe(df)
    _require_columns(df, entity_field, period_field)
    fields=[entity_field, period_field] + ([cohort_field] if cohort_field else [])
    working=df[fields].dropna().copy()
    working[period_field]=working[period_field].astype(str)
    if cohort_field:
        working[cohort_field]=working[cohort_field].astype(str)
        cohorts=working.groupby(cohort_field)
    else:
        first=working.groupby(entity_field)[period_field].min().rename("_cohort")
        working=working.join(first,on=entity_field)
        cohorts=working.groupby("_cohort")
    result=[]
    for cohort, group in cohorts:
        base=int(group[entity_field].nunique())
        active={str(period): int(group.loc[group[period_field]==period, entity_field].nunique()) for period in sorted(group[period_field].unique())}
        result.append({"cohort": str(cohort), "cohort_size": base, "active": active, "retention": {k:(v/base if base else None) for k,v in active.items()}})
    if not result:
        raise AnalysisMethodError("No valid retention rows after filtering.")
    return RetentionResult(entity_field=entity_field, period_field=period_field, cohort_field=cohort_field, cohorts=result, disclosure=["Cohorts use first observed period when cohort_field is omitted; right-censoring is not inferred."])


def test_group_difference(df: pd.DataFrame, *, metric: str, group_field: str, group_a: str, group_b: str) -> GroupDifferenceResult:
    _require_dataframe(df)
    _require_columns(df, metric, group_field)
    values=pd.to_numeric(df[metric],errors="coerce")
    a=values[df[group_field].astype(str)==str(group_a)].dropna()
    b=values[df[group_field].astype(str)==str(group_b)].dropna()
    if len(a)<2 or len(b)<2:
        raise AnalysisMethodError("Each comparison group requires at least 2 numeric observations.")
    va,vb=float(a.var(ddof=1)),float(b.var(ddof=1))
    pooled=float(np.sqrt(((len(a)-1)*va+(len(b)-1)*vb)/(len(a)+len(b)-2)))
    effect=None if np.isclose(pooled,0) else (float(a.mean())-float(b.mean()))/pooled
    return GroupDifferenceResult(metric=metric,group_field=group_field,group_a=str(group_a),group_b=str(group_b),mean_a=float(a.mean()),mean_b=float(b.mean()),difference=float(a.mean()-b.mean()),pooled_std=pooled,effect_size=effect,sample_size_a=len(a),sample_size_b=len(b),disclosure=["Effect size is descriptive; no causal or multiple-comparison claim is made."])


def analyze_concentration(df: pd.DataFrame, *, dimension: str, metric: str, top_n: int = 5) -> ConcentrationResult:
    _require_dataframe(df)
    _require_columns(df, dimension, metric)
    if top_n<1: raise AnalysisMethodError("top_n must be at least 1.")
    values=pd.to_numeric(df[metric],errors="coerce")
    working=pd.DataFrame({"group":df[dimension].astype(str),"value":values}).dropna()
    grouped=working.groupby("group")["value"].sum().sort_values(ascending=False)
    total=float(grouped.sum())
    if np.isclose(total,0): raise AnalysisMethodError("Total metric value is zero; concentration is undefined.")
    shares=grouped/total
    rows=[{"group":str(k),"value":float(v),"share":float(shares.loc[k])} for k,v in grouped.items()]
    top=float(grouped.head(top_n).sum())
    return ConcentrationResult(dimension=dimension,metric=metric,top_n=top_n,total_value=total,top_n_value=top,top_n_share=top/total,hhi=float((shares**2).sum()),groups=rows,disclosure=["Concentration is descriptive and depends on the declared aggregation and dimension."])


def run_sensitivity_check(df: pd.DataFrame, *, metric: str, trim_fraction: float = 0.05) -> SensitivityResult:
    _require_dataframe(df)
    _require_columns(df, metric)
    if not 0 <= trim_fraction < 0.5: raise AnalysisMethodError("trim_fraction must be in [0, 0.5).")
    values=_numeric_series(df,metric).dropna()
    baseline=float(values.mean())
    alternatives=[{"scenario":"median","value":float(values.median())}]
    if trim_fraction:
        sorted_values=values.sort_values()
        n=int(len(sorted_values)*trim_fraction)
        trimmed=sorted_values.iloc[n:len(sorted_values)-n] if len(sorted_values)>2*n else sorted_values
        alternatives.append({"scenario":f"trim_{int(trim_fraction*100)}pct","value":float(trimmed.mean())})
    spread=max(abs(item["value"]-baseline) for item in alternatives) if alternatives else 0
    return SensitivityResult(metric=metric,baseline=baseline,alternatives=alternatives,stable=bool(spread <= max(abs(baseline)*0.1,1e-12)),disclosure=["Sensitivity changes the aggregation rule or trims extremes; it does not prove causal robustness."])

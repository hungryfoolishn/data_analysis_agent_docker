"""Deterministic dataframe analysis operations for structured plans."""

from __future__ import annotations

from typing import Any, Mapping

import numpy as np
import pandas as pd

from .general_planner import PLAN_OPERATIONS


class AnalysisOperationError(ValueError):
    """Raised when an allowlisted analysis operation cannot be executed."""


def _require_columns(frame: pd.DataFrame, *names: str | None) -> None:
    for name in names:
        if name and name not in frame.columns:
            raise AnalysisOperationError(f"Unknown column: {name}")


def _numeric(frame: pd.DataFrame, column: str) -> pd.Series:
    return pd.to_numeric(frame[column], errors="coerce")


def _inspect(frame: pd.DataFrame, parameters: Mapping[str, Any]) -> pd.DataFrame:
    return frame.head(int(parameters.get("rows", 20))).copy()


def _summarize(frame: pd.DataFrame, parameters: Mapping[str, Any]) -> pd.DataFrame:
    numeric = frame.select_dtypes(include=["number"])
    records: list[dict[str, Any]] = [
        {"statistic": "row_count", "value": float(len(frame))},
        {"statistic": "column_count", "value": float(len(frame.columns))},
    ]
    for column in numeric.columns:
        series = pd.to_numeric(frame[column], errors="coerce").dropna()
        if series.empty:
            continue
        records.extend(
            [
                {"statistic": f"{column}_sum", "value": float(series.sum())},
                {"statistic": f"{column}_mean", "value": float(series.mean())},
                {"statistic": f"{column}_min", "value": float(series.min())},
                {"statistic": f"{column}_max", "value": float(series.max())},
            ]
        )
    return pd.DataFrame(records)


def _select_columns(frame: pd.DataFrame, parameters: Mapping[str, Any]) -> pd.DataFrame:
    columns = parameters.get("columns")
    if not isinstance(columns, list) or not columns:
        raise AnalysisOperationError("columns must be a non-empty list")
    _require_columns(frame, *columns)
    return frame.loc[:, columns].copy()


def _filter(frame: pd.DataFrame, parameters: Mapping[str, Any]) -> pd.DataFrame:
    _require_columns(frame, parameters.get("column"))
    column = parameters["column"]
    operator = parameters.get("operator", "eq")
    value = parameters.get("value")
    series = frame[column]
    if operator == "eq":
        mask = series.isna() if value is None else series.eq(value)
    elif operator == "ne":
        mask = series.ne(value)
    elif operator == "gt":
        mask = _numeric(frame, column) > pd.to_numeric(value, errors="coerce")
    elif operator == "gte":
        mask = _numeric(frame, column) >= pd.to_numeric(value, errors="coerce")
    elif operator == "lt":
        mask = _numeric(frame, column) < pd.to_numeric(value, errors="coerce")
    elif operator == "lte":
        mask = _numeric(frame, column) <= pd.to_numeric(value, errors="coerce")
    elif operator == "in":
        values = value if isinstance(value, list) else [value]
        mask = series.isin(values)
    else:
        raise AnalysisOperationError(f"Unsupported filter operator: {operator}")
    return frame.loc[mask.fillna(False)].copy()


def _group_aggregate(frame: pd.DataFrame, parameters: Mapping[str, Any]) -> pd.DataFrame:
    _require_columns(frame, parameters.get("group_by"), parameters.get("metric"))
    group_by = parameters["group_by"]
    metric = parameters["metric"]
    agg = parameters.get("agg", "sum")
    if agg == "count":
        grouped = frame.groupby(group_by, dropna=False)[metric].count()
    else:
        grouped = _numeric(frame, metric).groupby(frame[group_by], dropna=False).agg(agg)
    output = grouped.reset_index(name=f"{metric}_{agg}")
    return output.sort_values(f"{metric}_{agg}", ascending=False).reset_index(drop=True)


def _sort(frame: pd.DataFrame, parameters: Mapping[str, Any]) -> pd.DataFrame:
    _require_columns(frame, parameters.get("by"))
    ascending = bool(parameters.get("ascending", True))
    return frame.sort_values(parameters["by"], ascending=ascending).reset_index(drop=True)


def _trend(frame: pd.DataFrame, parameters: Mapping[str, Any]) -> pd.DataFrame:
    _require_columns(frame, parameters.get("time_column"), parameters.get("value_column"))
    time_column = parameters["time_column"]
    value_column = parameters["value_column"]
    output = frame.copy()
    output[time_column] = pd.to_datetime(output[time_column], errors="coerce")
    output[value_column] = _numeric(output, value_column)
    output = output.dropna(subset=[time_column, value_column]).sort_values(time_column)
    output["value_change"] = output[value_column].diff()
    output["value_pct_change"] = output[value_column].pct_change(fill_method=None)
    window = parameters.get("window")
    if window is not None:
        try:
            output["value_moving_average"] = output[value_column].rolling(int(window)).mean()
        except (TypeError, ValueError):
            pass
    return output.reset_index(drop=True)


def _detect_anomalies(frame: pd.DataFrame, parameters: Mapping[str, Any]) -> pd.DataFrame:
    _require_columns(frame, parameters.get("value_column"))
    value_column = parameters["value_column"]
    values = _numeric(frame, value_column).dropna()
    if values.empty:
        raise AnalysisOperationError("No numeric values for anomaly detection")
    q1 = values.quantile(0.25)
    q3 = values.quantile(0.75)
    iqr = q3 - q1
    lower = q1 - 1.5 * iqr
    upper = q3 + 1.5 * iqr
    numeric_values = _numeric(frame, value_column)
    mask = (numeric_values < lower) | (numeric_values > upper)
    output = frame.loc[mask.fillna(False)].copy()
    output["anomaly_lower_bound"] = lower
    output["anomaly_upper_bound"] = upper
    return output.reset_index(drop=True)


def _correlation(frame: pd.DataFrame, parameters: Mapping[str, Any]) -> pd.DataFrame:
    columns = parameters.get("columns")
    if not isinstance(columns, list) or len(columns) < 2:
        raise AnalysisOperationError("correlation requires at least two columns")
    _require_columns(frame, *columns)
    matrix = frame.loc[:, columns].apply(pd.to_numeric, errors="coerce").corr()
    records = []
    for left_index, left in enumerate(columns):
        for right in columns[left_index + 1 :]:
            records.append(
                {
                    "column_a": left,
                    "column_b": right,
                    "correlation": matrix.loc[left, right],
                }
            )
    return pd.DataFrame(records)


def _data_quality(frame: pd.DataFrame, parameters: Mapping[str, Any]) -> pd.DataFrame:
    time_column = parameters.get("time_column")
    records: list[dict[str, Any]] = []
    for column in frame.columns:
        series = frame[column]
        missing = int(series.isna().sum())
        record = {
            "column": str(column),
            "dtype": str(series.dtype),
            "missing_count": missing,
            "missing_ratio": float(missing / len(frame)) if len(frame) else 0.0,
            "unique_count": int(series.nunique(dropna=True)),
        }
        if column == time_column:
            parsed = pd.to_datetime(series, errors="coerce")
            record["invalid_datetime_count"] = int((series.notna() & parsed.isna()).sum())
        records.append(record)
    duplicated = frame.duplicated().sum()
    records.append(
        {
            "column": "__frame__",
            "dtype": "frame",
            "missing_count": int(frame.isna().sum().sum()),
            "missing_ratio": float(frame.isna().sum().sum() / frame.size) if frame.size else 0.0,
            "unique_count": int(len(frame.drop_duplicates())),
            "duplicate_row_count": int(duplicated),
        }
    )
    return pd.DataFrame(records)


_EXECUTORS = {
    "inspect": _inspect,
    "summarize": _summarize,
    "select_columns": _select_columns,
    "filter": _filter,
    "group_aggregate": _group_aggregate,
    "sort": _sort,
    "trend": _trend,
    "detect_anomalies": _detect_anomalies,
    "correlation": _correlation,
    "data_quality": _data_quality,
}


def execute_analysis_operation(
    frame: pd.DataFrame,
    *,
    operation: str,
    parameters: Mapping[str, Any] | None = None,
) -> pd.DataFrame:
    """Execute one allowlisted analysis operation."""
    if operation not in PLAN_OPERATIONS or operation not in _EXECUTORS:
        raise AnalysisOperationError(f"Unsupported analysis operation: {operation}")
    if not isinstance(frame, pd.DataFrame):
        raise AnalysisOperationError("Input must be a pandas DataFrame")
    return _EXECUTORS[operation](frame, parameters or {})

"""Deterministic dataframe analysis operations for structured plans."""

from __future__ import annotations

from typing import Any, Mapping

import json

import numpy as np
import pandas as pd

from .general_planner import PLAN_OPERATIONS
from .planner_models import GENERATOR_VERSION, PROFILE_VERSION


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


def build_data_profile(
    frame: "pd.DataFrame",
    *,
    asset_id: str,
    max_scan_rows: int = 100_000,
    max_numeric_columns: int = 20,
    top_k: int = 10,
    max_profile_bytes: int = 64 * 1024,
    duplicate_row_budget: int = 200_000,
) -> "DataProfile":
    """Build a bounded, deterministic DataProfile for planning (B4.2.1 §4).

    B4.2.1.1 fix 2 - unified scan budget: every statistic (missing rate,
    numeric summary, category top-K, unique counts, candidate keys) is
    computed on a bounded scan.  Small datasets get exact statistics; frames
    exceeding ``max_scan_rows`` use a reproducible bounded sample
    (``random_state=0``) and the profile is explicitly marked ``sampled``.
    No full-frame copy is ever made and raw rows are never stored.
    """
    from langgraph_langchain.runtime.planner_models import (
        GENERATOR_VERSION,
        PROFILE_VERSION,
        ColumnProfile,
        DataProfile,
        DataProfileMeta,
    )

    row_count = int(len(frame))
    scan_rows = row_count
    statistics_scope = "exact"
    work = frame  # statistics read columns in place; never copies the frame
    incomplete_reasons: list[str] = []

    if row_count > max_scan_rows:
        work = frame.sample(n=max_scan_rows, random_state=0)  # reproducible
        scan_rows = int(len(work))
        statistics_scope = "sampled"
        incomplete_reasons.append(
            f"statistics sampled: row_count {row_count} exceeds scan budget {max_scan_rows}"
        )

    truncated = statistics_scope == "sampled"

    duplicate_rows: int | None = None
    if statistics_scope == "exact" and row_count <= duplicate_row_budget:
        duplicate_rows = int(work.duplicated().sum())
    else:
        incomplete_reasons.append(
            "duplicate_rows skipped: exceeds duplicate_row_budget or sampled scope"
        )

    numeric_candidates = [
        name for name in frame.columns if pd.api.types.is_numeric_dtype(frame[name])
    ]
    if len(numeric_candidates) > max_numeric_columns:
        truncated = True
        incomplete_reasons.append(
            f"numeric columns truncated to {max_numeric_columns} of {len(numeric_candidates)}"
        )
        numeric_candidates = numeric_candidates[:max_numeric_columns]

    column_profiles: list[ColumnProfile] = []
    for name in frame.columns:
        series = work[name]
        null_count = int(series.isna().sum())
        missing_rate = null_count / scan_rows if scan_rows else 0.0
        profile = ColumnProfile(
            name=str(name),
            dtype=str(series.dtype),
            missing_rate=round(missing_rate, 6),
        )
        is_numeric = pd.api.types.is_numeric_dtype(series)
        is_time = pd.api.types.is_datetime64_any_dtype(series)
        if is_numeric and name in numeric_candidates:
            description = series.describe()
            summary_map = {
                "count": "count",
                "mean": "mean",
                "std": "std",
                "min": "min",
                "p25": "25%",
                "median": "50%",
                "p75": "75%",
                "max": "max",
            }
            profile.numeric_summary = {
                key: float(description[index_key])
                for key, index_key in summary_map.items()
                if index_key in description.index
            }
            profile.unique_count = int(series.nunique(dropna=True))
        elif is_time:
            valid = series.dropna()
            if len(valid):
                profile.time_range = [str(valid.min()), str(valid.max())]
        else:
            unique_count = int(series.nunique(dropna=True))
            profile.unique_count = unique_count
            if 0 < unique_count <= top_k * 100:
                counts = series.value_counts(dropna=True).head(top_k)
                profile.top_categories = [
                    (str(key), int(value)) for key, value in counts.items()
                ]
        column_profiles.append(profile)

    candidate_keys = [
        str(name)
        for name in frame.columns
        if scan_rows > 0
        and int(work[name].isna().sum()) == 0
        and int(work[name].nunique(dropna=True)) == scan_rows
    ]

    profile = DataProfile(
        meta=DataProfileMeta(
            asset_id=asset_id,
            data_version="",
            profile_version=PROFILE_VERSION,
            generator_version=GENERATOR_VERSION,
            generated_at=_profile_generated_at(),
            row_count=row_count,
            column_count=int(len(frame.columns)),
            duplicate_rows=duplicate_rows,
            statistics_scope=statistics_scope,
            sample_rows=scan_rows if statistics_scope == "sampled" else None,
        ),
        columns=column_profiles,
        candidate_keys=candidate_keys,
        truncated=truncated,
        incomplete_reasons=list(incomplete_reasons),
    )
    return _degrade_to_size_limit(profile, max_profile_bytes)


def _serialized_profile_size(profile: "DataProfile") -> int:
    import json as _json

    return len(
        _json.dumps(profile.model_dump(mode="json"), ensure_ascii=False, default=str).encode(
            "utf-8"
        )
    )


def _degrade_to_size_limit(profile: "DataProfile", max_profile_bytes: int) -> "DataProfile":
    """Progressively degrade a profile until it fits ``max_profile_bytes``.

    B4.2.1.1 fix 5: the final serialized byte size is a hard constraint.  Each
    degradation pass is deterministic and recorded in ``incomplete_reasons``;
    the last resort drops column entries entirely - the meta block always
    survives.
    """
    from langgraph_langchain.runtime.planner_models import ColumnProfile

    reasons: list[str] = []

    def _without_field(field: str, current: "DataProfile") -> "DataProfile":
        columns = []
        for column in current.columns:
            data = column.model_dump()
            data[field] = None
            columns.append(ColumnProfile.model_validate(data))
        return current.model_copy(update={"columns": columns})

    passes = [
        ("top_categories dropped to fit size limit", lambda p: _without_field("top_categories", p)),
        ("numeric_summary dropped to fit size limit", lambda p: _without_field("numeric_summary", p)),
        ("time_range dropped to fit size limit", lambda p: _without_field("time_range", p)),
        ("candidate_keys dropped to fit size limit", lambda p: p.model_copy(update={"candidate_keys": []})),
        ("unique_count dropped to fit size limit", lambda p: _without_field("unique_count", p)),
        ("column details reduced to names/types/missing rates to fit size limit",
         lambda p: p.model_copy(update={"columns": [
             ColumnProfile(name=column.name, dtype=column.dtype, missing_rate=column.missing_rate)
             for column in p.columns
         ]})),
    ]

    for reason, transform in passes:
        if _serialized_profile_size(profile) <= max_profile_bytes:
            break
        profile = transform(profile)
        reasons.append(reason)
        profile = profile.model_copy(update={"truncated": True, "incomplete_reasons": reasons})

    # Last resort: drop column entries in halves until the profile fits
    # (bounded loop; the meta block alone is small enough to always fit).
    while _serialized_profile_size(profile) > max_profile_bytes and profile.columns:
        keep = len(profile.columns) // 2
        profile = profile.model_copy(
            update={"columns": profile.columns[:keep], "truncated": True}
        )
        reasons.append(
            f"column entries dropped to fit size limit ({len(profile.columns)} remaining)"
        )
        profile = profile.model_copy(update={"incomplete_reasons": reasons})

    if _serialized_profile_size(profile) > max_profile_bytes:
        profile = profile.model_copy(update={"columns": [], "truncated": True})
        reasons.append("all column details dropped to fit size limit")

    # Final clamp: reasons themselves may be unbounded (B4.2.1.1 extreme case).
    while _serialized_profile_size(profile) > max_profile_bytes and reasons:
        reasons = reasons[: len(reasons) // 2]
        reasons.append("incomplete_reasons truncated to fit size limit")
        profile = profile.model_copy(update={"incomplete_reasons": reasons})
    if _serialized_profile_size(profile) > max_profile_bytes:
        profile = profile.model_copy(update={"incomplete_reasons": ["profile truncated to size limit"]})
    return profile

def _profile_generated_at() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def attach_profile(asset: "DataAsset", frame: "pd.DataFrame") -> str | None:
    """Compute and persist the DataProfile for a registered asset.

    Returns ``None`` on success, or an ``unavailable reason`` on failure.
    Failure never invalidates the Asset (frozen contract §6.4): the asset and
    its schema stay usable and planning degrades to schema-only context.
    """
    from langgraph_langchain.runtime.planner_models import DataProfile

    try:
        profile = build_data_profile(frame, asset_id=asset.asset_id)
        profile.meta.data_version = asset.content_hash or ""
        asset.source_metadata["profile"] = profile.model_dump(mode="json")
        return None
    except Exception as exc:  # noqa: BLE001 - profile must never break registration
        asset.source_metadata["profile_unavailable"] = {
            "reason": f"{type(exc).__name__}: {exc}"
        }
        return f"{type(exc).__name__}: {exc}"


def resolve_asset_profile(asset: "DataAsset") -> tuple["DataProfile | None", str | None]:
    """Resolve the persisted profile of an asset with version validation.

    Returns ``(profile, None)`` when a current profile exists, otherwise
    ``(None, reason_code)``.  Stale/incompatible/corrupted profiles are never
    silently reused (frozen contract §6.4).
    """
    from langgraph_langchain.runtime.planner_models import DataProfile

    metadata = asset.source_metadata or {}
    if metadata.get("profile_unavailable"):
        return None, "profile_unavailable"
    payload = metadata.get("profile")
    if not isinstance(payload, dict):
        return None, "profile_missing"
    try:
        profile = DataProfile.model_validate(payload)
    except Exception:
        return None, "profile_corrupted"
    if profile.meta.asset_id != asset.asset_id:
        return None, "profile_asset_mismatch"
    if profile.meta.profile_version != PROFILE_VERSION:
        return None, "profile_version_incompatible"
    if profile.meta.generator_version != GENERATOR_VERSION:
        return None, "generator_changed"
    if profile.meta.data_version != (asset.content_hash or ""):
        return None, "profile_stale"
    return profile, None

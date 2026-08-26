"""Formal, reproducible tools for common domain-neutral analysis methods."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Literal

import pandas as pd
from langchain_core.tools import tool

from langgraph_langchain.analysis_methods import (
    AnalysisMethodError,
    analyze_time_trend as compute_time_trend,
    compare_groups as compute_group_comparison,
    decompose_contribution as compute_contribution,
    detect_iqr_anomalies,
    profile_distribution,
    analyze_correlation,
    compare_periods,
    calculate_ratio,
    analyze_funnel, analyze_retention, test_group_difference, analyze_concentration, run_sensitivity_check,
)
from langgraph_langchain.runtime import SessionExecutionRecorder
from langgraph_langchain.tools._shared import _validate_tool_stage_factory
from langgraph_langchain.tools.registry import registry, tool_error


def _artifact_reference(metadata: dict) -> dict:
    return {
        "artifact_id": metadata.get("artifact_id"),
        "name": metadata["name"],
        "url": metadata["url"],
    }


def _write_rows(path: Path, rows: list[dict], columns: list[str]) -> None:
    pd.DataFrame(rows, columns=columns).to_csv(path, index=False, encoding="utf-8-sig")


def _run_method(session, tool_name: str, inputs: dict, compute: Callable, rows_key: str) -> str:
    validate = _validate_tool_stage_factory(session)
    stage_error = validate(tool_name)
    recorder = SessionExecutionRecorder(
        session,
        tool_name=tool_name,
        code_or_query=json.dumps(inputs, ensure_ascii=False, sort_keys=True),
    )
    if stage_error:
        recorder.fail(stage_error, error_type="StageValidationError")
        return tool_error(stage_error)

    dataframe = session.ns.get("df")
    if dataframe is None:
        message = "DataFrame 'df' is not loaded. Call load_data first."
        recorder.fail(message, error_type="MissingData")
        return tool_error(message)

    try:
        result = compute(dataframe, **inputs)
        payload = result.model_dump(mode="json")
        rows = payload.get(rows_key) or []
        artifact_path = session.workspace_dir / f"{tool_name}_{recorder.execution_id}.csv"
        artifact_columns = {
            "groups": ["group", "row_count", "non_null_count", "value", "share_of_aggregate", "rank"],
            "periods": ["period", "value", "row_count", "absolute_change", "percent_change"],
            "contributions": ["dimension", "group", "previous", "current", "contribution", "contribution_share"],
            "anomalies": ["row_index", "value"],
            "distribution": ["statistic", "value"],
            "correlations": ["x_field", "y_field", "correlation", "sample_size"],
            "period_comparison": ["period", "value", "row_count"],
            "ratios": ["numerator", "denominator", "ratio"],
            "funnel": ["stage", "entity_count", "conversion_from_first"],
            "retention": ["cohort", "cohort_size"],
            "group_difference": ["group_a", "group_b", "difference", "effect_size"],
            "concentration": ["group", "value", "share"],
            "sensitivity": ["scenario", "value"],
        }[rows_key]
        _write_rows(artifact_path, rows, artifact_columns)
        metadata = recorder.register_artifact(artifact_path)
        payload["artifact"] = _artifact_reference(metadata)
        result = type(result).model_validate(payload)
        serialized = result.model_dump_json()

        session.state_machine.record_tool_use(tool_name)
        bundle = session.ns.setdefault("explanation_bundle", {})
        if tool_name == "compare_groups":
            bundle.setdefault("group_comparisons", []).append(payload)
        elif tool_name == "analyze_time_trend":
            bundle.setdefault("time_trends", []).append(payload)
        elif tool_name == "detect_anomalies":
            bundle.setdefault("anomaly_checks", []).append(payload)
        elif tool_name == "decompose_contribution":
            bundle["metric_decomposition"] = payload
            focus = (
                payload["top_negative_contributors"]
                if payload["absolute_change"] < 0
                else payload["top_positive_contributors"]
            )
            bundle["driver_ranking"] = [
                {
                    "driver": f"{item['dimension']}={item['group']} observed contribution",
                    "dimension": item["dimension"],
                    "group": item["group"],
                    "contribution": item["contribution"],
                    "evidence_level": "A",
                    "score": min(
                        1.0,
                        abs(item["contribution"]) / (abs(payload["absolute_change"]) + 1e-12),
                    ),
                    "reason": (
                        "Arithmetic attribution only; this does not establish causality. "
                        f"Observed from {payload['previous_period']} to {payload['current_period']}."
                    ),
                }
                for item in focus
            ]

        recorder.succeed(serialized)
        return serialized
    except (AnalysisMethodError, TypeError, ValueError) as exc:
        recorder.fail(str(exc), error_type=type(exc).__name__)
        return tool_error(str(exc))
    except Exception as exc:
        recorder.fail(str(exc), error_type=type(exc).__name__)
        return tool_error(f"{tool_name} failed: {exc}")


def _compare_groups_factory(session):
    @tool
    def compare_groups(
        dimension: str,
        metric: str,
        aggregation: Literal["sum", "mean", "median", "count"] = "mean",
        max_groups: int = 50,
    ) -> str:
        """Compare one numeric metric across groups with an explicit dimension grain.

        Produces a ranked CSV table with group size, non-null count, aggregate value,
        and aggregate share for additive aggregations. Rejects missing fields,
        non-numeric metrics, single-group dimensions, and excessive cardinality.
        """
        return _run_method(
            session,
            "compare_groups",
            {
                "dimension": dimension,
                "metric": metric,
                "aggregation": aggregation,
                "max_groups": max_groups,
            },
            compute_group_comparison,
            "groups",
        )

    return compare_groups


def _time_trend_factory(session):
    @tool
    def analyze_time_trend(
        date_field: str,
        metric: str,
        frequency: Literal["day", "week", "month", "quarter", "year"] = "month",
        aggregation: Literal["sum", "mean", "median", "count"] = "sum",
    ) -> str:
        """Aggregate a numeric metric by time period and calculate period changes.

        The declared grain is the selected period. At least two periods with valid
        datetime and numeric values are required. The output is a structured trend
        result plus a registered CSV artifact.
        """
        return _run_method(
            session,
            "analyze_time_trend",
            {
                "date_field": date_field,
                "metric": metric,
                "frequency": frequency,
                "aggregation": aggregation,
            },
            compute_time_trend,
            "periods",
        )

    return analyze_time_trend


def _contribution_factory(session):
    @tool
    def decompose_contribution(
        date_field: str,
        dimension: str,
        metric: str,
        frequency: Literal["day", "week", "month", "quarter", "year"] = "month",
        top_k: int = 5,
        max_groups: int = 100,
    ) -> str:
        """Arithmetically attribute the latest period-over-period metric change.

        Compares the latest two non-empty periods at dimension-by-period grain and
        reconciles group changes to the total change. This is descriptive attribution,
        not causal proof. Requires a non-zero previous-period total.
        """
        return _run_method(
            session,
            "decompose_contribution",
            {
                "date_field": date_field,
                "dimension": dimension,
                "metric": metric,
                "frequency": frequency,
                "top_k": top_k,
                "max_groups": max_groups,
            },
            compute_contribution,
            "contributions",
        )

    return decompose_contribution


def _anomaly_factory(session):
    @tool
    def detect_anomalies(metric: str) -> str:
        """Detect source-row anomalies in a numeric metric using the 1.5 IQR rule.

        Requires at least four numeric observations. Returns explicit bounds,
        anomaly rate, source row indices, and a registered CSV artifact. An empty
        anomaly table is a valid result when no observations exceed the bounds.
        """
        return _run_method(
            session,
            "detect_anomalies",
            {"metric": metric},
            detect_iqr_anomalies,
            "anomalies",
        )

    return detect_anomalies


registry.register(
    name="compare_groups",
    toolset="analysis",
    factory=_compare_groups_factory,
    description="Structured grouped comparison with declared grain and a CSV artifact.",
)
registry.register(
    name="analyze_time_trend",
    toolset="analysis",
    factory=_time_trend_factory,
    description="Structured period trend analysis with explicit aggregation and changes.",
)
registry.register(
    name="decompose_contribution",
    toolset="analysis",
    factory=_contribution_factory,
    description="Arithmetic period-change attribution by group; not a causal claim.",
)
registry.register(
    name="detect_anomalies",
    toolset="analysis",
    factory=_anomaly_factory,
    description="Structured IQR anomaly detection with bounds and source row indices.",
)


def _distribution_factory(session):
    @tool
    def profile_distribution(metric: str) -> str:
        """Profile quantiles, spread, skewness, long-tail signal, and missingness."""
        return _run_method(session, "profile_distribution", {"metric": metric}, compute_profile_distribution, "distribution")
    return profile_distribution


def _correlation_factory(session):
    @tool
    def analyze_correlation(
        x_field: str,
        y_field: str,
        correlation_method: Literal["pearson", "spearman"] = "pearson",
    ) -> str:
        """Calculate an association with pairwise-complete sample size and non-causal disclosure."""
        return _run_method(
            session, "analyze_correlation",
            {"x_field": x_field, "y_field": y_field, "correlation_method": correlation_method},
            compute_correlation, "correlations",
        )
    return analyze_correlation


def _period_comparison_factory(session):
    @tool
    def compare_periods(
        date_field: str,
        metric: str,
        frequency: Literal["day", "week", "month", "quarter", "year"] = "month",
        aggregation: Literal["sum", "mean", "median", "count"] = "sum",
        current_period: str = "",
        previous_period: str = "",
    ) -> str:
        """Compare two declared or latest adjacent periods with denominator disclosure."""
        return _run_method(
            session, "compare_periods",
            {
                "date_field": date_field, "metric": metric, "frequency": frequency,
                "aggregation": aggregation,
                "current_period": current_period or None,
                "previous_period": previous_period or None,
            },
            compute_period_comparison, "period_comparison",
        )
    return compare_periods


def _ratio_factory(session):
    @tool
    def calculate_ratio(
        numerator_field: str,
        denominator_field: str,
        aggregation: Literal["sum", "mean", "count"] = "sum",
    ) -> str:
        """Calculate an explicit numerator/denominator ratio with zero-denominator handling."""
        return _run_method(
            session, "calculate_ratio",
            {
                "numerator_field": numerator_field,
                "denominator_field": denominator_field,
                "aggregation": aggregation,
            },
            compute_ratio, "ratios",
        )
    return calculate_ratio


compute_profile_distribution = profile_distribution
compute_correlation = analyze_correlation
compute_period_comparison = compare_periods
compute_ratio = calculate_ratio

registry.register(name="profile_distribution", toolset="analysis", factory=_distribution_factory, description="Distribution quantiles, spread, skewness and long-tail profiling.")
registry.register(name="analyze_correlation", toolset="analysis", factory=_correlation_factory, description="Pearson or Spearman association with sample and non-causal disclosure.")
registry.register(name="compare_periods", toolset="analysis", factory=_period_comparison_factory, description="Explicit adjacent-period comparison with comparability checks.")
registry.register(name="calculate_ratio", toolset="analysis", factory=_ratio_factory, description="Explicit numerator/denominator ratio with zero-denominator handling.")


def _funnel_factory(session):
    @tool
    def analyze_funnel(entity_field: str, stage_field: str, stage_order: list[str]) -> str:
        """Deduplicate entities by ordered stage and calculate descriptive conversion."""
        return _run_method(session, "analyze_funnel", {"entity_field": entity_field, "stage_field": stage_field, "stage_order": stage_order}, compute_funnel, "funnel")
    return analyze_funnel


def _retention_factory(session):
    @tool
    def analyze_retention(entity_field: str, period_field: str, cohort_field: str = "") -> str:
        """Build cohort activity and retention rates with right-censoring disclosure."""
        return _run_method(session, "analyze_retention", {"entity_field": entity_field, "period_field": period_field, "cohort_field": cohort_field or None}, compute_retention, "retention")
    return analyze_retention


def _difference_factory(session):
    @tool
    def test_group_difference(metric: str, group_field: str, group_a: str, group_b: str) -> str:
        """Compare two groups with pooled spread and descriptive effect size."""
        return _run_method(session, "test_group_difference", {"metric": metric, "group_field": group_field, "group_a": group_a, "group_b": group_b}, compute_difference, "group_difference")
    return test_group_difference


def _concentration_factory(session):
    @tool
    def analyze_concentration(dimension: str, metric: str, top_n: int = 5) -> str:
        """Calculate Top-N share and HHI concentration at an explicit grain."""
        return _run_method(session, "analyze_concentration", {"dimension": dimension, "metric": metric, "top_n": top_n}, compute_concentration, "concentration")
    return analyze_concentration


def _sensitivity_factory(session):
    @tool
    def run_sensitivity_check(metric: str, trim_fraction: float = 0.05) -> str:
        """Compare mean with median and optional trimmed mean."""
        return _run_method(session, "run_sensitivity_check", {"metric": metric, "trim_fraction": trim_fraction}, compute_sensitivity, "sensitivity")
    return run_sensitivity_check


compute_funnel = analyze_funnel
compute_retention = analyze_retention
compute_difference = test_group_difference
compute_concentration = analyze_concentration
compute_sensitivity = run_sensitivity_check

registry.register(name="analyze_funnel", toolset="analysis", factory=_funnel_factory, description="Ordered entity funnel with deduplication and conversion.")
registry.register(name="analyze_retention", toolset="analysis", factory=_retention_factory, description="Cohort activity and retention with right-censoring disclosure.")
registry.register(name="test_group_difference", toolset="analysis", factory=_difference_factory, description="Descriptive two-group difference and pooled effect size.")
registry.register(name="analyze_concentration", toolset="analysis", factory=_concentration_factory, description="Top-N share and HHI concentration analysis.")
registry.register(name="run_sensitivity_check", toolset="analysis", factory=_sensitivity_factory, description="Mean/median/trimmed sensitivity check.")

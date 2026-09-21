"""Deterministic comparisons for golden analysis answers."""

from __future__ import annotations

from typing import Optional

from .models import MetricComparison, MetricExpectation


def _is_rate_metric(metric: str) -> bool:
    lowered = metric.casefold()
    return any(token in lowered for token in ("率", "ratio", "rate", "percent", "%"))


def _scale_for_compare(expected: float, actual: float, metric: str) -> float:
    """Return actual on the same scale as expected.

    Analysts may report 82.3% while the golden value is 0.823, or vice versa.
    We only convert for rate-like metrics and only when the scales are unambiguous.
    """
    if not _is_rate_metric(metric):
        return actual
    if 0 <= expected <= 1 and actual > 1:
        return actual / 100.0
    if expected > 1 and 0 <= actual <= 1:
        return actual * 100.0
    return actual


def compare_metric(expectation: MetricExpectation, actual: Optional[float]) -> MetricComparison:
    if actual is None:
        return MetricComparison(
            metric=expectation.metric,
            expected=expectation.value,
            actual=None,
            tolerance=expectation.tolerance,
            passed=False,
            period=expectation.period,
            dimension=expectation.dimension,
            group=expectation.group,
            filters=expectation.filters,
            message="Metric answer is missing",
        )

    normalized = _scale_for_compare(expectation.value, float(actual), expectation.metric)
    error = abs(normalized - expectation.value)
    passed = error <= expectation.tolerance
    return MetricComparison(
        metric=expectation.metric,
        expected=expectation.value,
        actual=normalized,
        tolerance=expectation.tolerance,
        absolute_error=error,
        passed=passed,
        period=expectation.period,
        dimension=expectation.dimension,
        group=expectation.group,
        filters=expectation.filters,
        message=(
            f"Metric matches within {expectation.tolerance}"
            if passed
            else f"Metric differs by {error}; tolerance is {expectation.tolerance}"
        ),
    )

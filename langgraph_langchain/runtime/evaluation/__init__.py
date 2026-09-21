"""Runtime V8.5 golden evaluation infrastructure."""

from .comparator import compare_metric
from .golden_loader import GoldenCaseLoader, default_golden_cases_dir
from .models import (
    EvaluationRun,
    EvaluationScore,
    GoldenCandidateResult,
    GoldenCase,
    MetricAnswer,
    MetricComparison,
    MetricExpectation,
)
from .runner import GoldenEvaluationRunner

__all__ = [
    "EvaluationRun",
    "EvaluationScore",
    "GoldenCandidateResult",
    "GoldenCase",
    "GoldenCaseLoader",
    "MetricAnswer",
    "GoldenEvaluationRunner",
    "MetricComparison",
    "MetricExpectation",
    "compare_metric",
    "default_golden_cases_dir",
]

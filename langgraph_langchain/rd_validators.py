"""
Domain-Specific Validators for R&D Efficiency Analysis

This module provides validators that check for domain-specific issues
in R&D efficiency data and analysis.
"""

from typing import List, Dict, Any, Tuple, Optional
import pandas as pd
from datetime import datetime, timedelta

from langgraph_langchain.schemas import Finding, EvidenceItem, MetricDefinition
from langgraph_langchain.rd_efficiency_domain import (
    get_metric_definition,
    RDMetricDefinition,
    METRICS_BY_NAME
)


# ============================================================================
# Metric Definition Validators
# ============================================================================

def validate_rd_metric_definition(metric_def: MetricDefinition) -> Tuple[bool, List[str]]:
    """
    Validate that a metric definition follows R&D efficiency best practices.

    Returns:
        (is_valid, list_of_errors)
    """
    errors = []

    # Check if metric is a known R&D metric
    known_metric = get_metric_definition(metric_def.metric_name)

    if known_metric:
        # Validate against standard definition
        if metric_def.definition_text:
            # Check if definition mentions key concepts from standard
            standard_def_lower = known_metric.definition.lower()
            user_def_lower = metric_def.definition_text.lower()

            # Extract key terms from standard definition
            key_terms = [term for term in standard_def_lower.split() if len(term) > 4]
            matching_terms = sum(1 for term in key_terms if term in user_def_lower)

            if matching_terms < len(key_terms) * 0.3:  # Less than 30% overlap
                errors.append(
                    f"Metric '{metric_def.metric_name}' definition differs significantly from standard. "
                    f"Standard: {known_metric.definition}"
                )

        # Warn about common caveats
        if known_metric.caveats:
            # This is a warning, not an error
            pass

    else:
        # Custom metric - check for completeness
        if not metric_def.definition_text:
            errors.append(f"Custom metric '{metric_def.metric_name}' must have a clear definition")

    return len(errors) == 0, errors


def validate_metric_calculation_method(metric_name: str, calculation: str, df: pd.DataFrame) -> Tuple[bool, List[str]]:
    """
    Validate that the calculation method is appropriate for the metric.

    Returns:
        (is_valid, list_of_warnings)
    """
    warnings = []
    known_metric = get_metric_definition(metric_name)

    if not known_metric:
        return True, []  # Can't validate custom metrics

    # Check for common calculation mistakes
    if metric_name == "Story Points Completed":
        if "story_points" not in calculation.lower() and "points" not in calculation.lower():
            warnings.append("Calculation should reference 'story_points' column")

        if "status" not in calculation.lower() and "done" not in calculation.lower():
            warnings.append("Should filter for completed items (status='Done')")

    elif metric_name == "Cycle Time":
        if "median" not in calculation.lower() and "mean" not in calculation.lower():
            warnings.append("Cycle time should use median or mean, not sum")

        if "started" not in calculation.lower() or "completed" not in calculation.lower():
            warnings.append("Cycle time requires both start and completion dates")

    elif metric_name == "Defect Rate":
        if "/" not in calculation and "divide" not in calculation.lower():
            warnings.append("Defect rate should be a ratio (bugs / features)")

    elif metric_name == "Test Coverage":
        if "%" not in calculation and "percentage" not in calculation.lower():
            warnings.append("Test coverage should be expressed as percentage")

    return True, warnings


# ============================================================================
# Data Quality Validators
# ============================================================================

def validate_sprint_data(df: pd.DataFrame) -> Tuple[bool, List[str]]:
    """
    Validate sprint data for common issues.

    Returns:
        (is_valid, list_of_errors)
    """
    errors = []

    # Check for required columns
    required_cols = ["sprint"]
    missing_cols = [col for col in required_cols if col not in df.columns]
    if missing_cols:
        errors.append(f"Missing required columns: {missing_cols}")
        return False, errors

    # Check for empty sprints
    if "sprint" in df.columns:
        sprint_counts = df["sprint"].value_counts()
        empty_sprints = sprint_counts[sprint_counts == 0]
        if len(empty_sprints) > 0:
            errors.append(f"Found {len(empty_sprints)} empty sprints")

    # Check for story points issues
    if "story_points" in df.columns:
        # Zero story points
        zero_points = df[df["story_points"] == 0]
        if len(zero_points) > len(df) * 0.2:  # More than 20%
            errors.append(f"{len(zero_points)} items ({len(zero_points)/len(df)*100:.1f}%) have zero story points")

        # Negative story points
        negative_points = df[df["story_points"] < 0]
        if len(negative_points) > 0:
            errors.append(f"{len(negative_points)} items have negative story points - data error")

        # Unusually large story points
        large_points = df[df["story_points"] > 20]
        if len(large_points) > 0:
            errors.append(f"{len(large_points)} items have >20 story points - should be broken down")

    # Check for date consistency
    if "started_date" in df.columns and "completed_date" in df.columns:
        df["started_date"] = pd.to_datetime(df["started_date"], errors="coerce")
        df["completed_date"] = pd.to_datetime(df["completed_date"], errors="coerce")

        invalid_dates = df[df["completed_date"] < df["started_date"]]
        if len(invalid_dates) > 0:
            errors.append(f"{len(invalid_dates)} items completed before they started - date error")

    return len(errors) == 0, errors


def validate_pr_data(df: pd.DataFrame) -> Tuple[bool, List[str]]:
    """
    Validate pull request data for common issues.

    Returns:
        (is_valid, list_of_errors)
    """
    errors = []

    # Check for required columns
    required_cols = ["pr_id"]
    missing_cols = [col for col in required_cols if col not in df.columns]
    if missing_cols:
        errors.append(f"Missing required columns: {missing_cols}")
        return False, errors

    # Check for PR size issues
    if "lines_changed" in df.columns or ("lines_added" in df.columns and "lines_deleted" in df.columns):
        if "lines_changed" not in df.columns:
            df["lines_changed"] = df["lines_added"] + df["lines_deleted"]

        # Very large PRs
        large_prs = df[df["lines_changed"] > 1000]
        if len(large_prs) > len(df) * 0.1:  # More than 10%
            errors.append(
                f"{len(large_prs)} PRs ({len(large_prs)/len(df)*100:.1f}%) have >1000 lines changed - "
                "should be broken down"
            )

        # Tiny PRs (might be config changes)
        tiny_prs = df[df["lines_changed"] < 5]
        if len(tiny_prs) > len(df) * 0.3:  # More than 30%
            errors.append(f"{len(tiny_prs)} PRs have <5 lines changed - verify data quality")

    # Check for review time issues
    if "pr_created_at" in df.columns and "first_review_at" in df.columns:
        df["pr_created_at"] = pd.to_datetime(df["pr_created_at"], errors="coerce")
        df["first_review_at"] = pd.to_datetime(df["first_review_at"], errors="coerce")

        df["review_time_hours"] = (df["first_review_at"] - df["pr_created_at"]).dt.total_seconds() / 3600

        # Negative review time
        negative_review = df[df["review_time_hours"] < 0]
        if len(negative_review) > 0:
            errors.append(f"{len(negative_review)} PRs reviewed before creation - date error")

        # Very long review time
        long_review = df[df["review_time_hours"] > 168]  # 1 week
        if len(long_review) > len(df) * 0.2:  # More than 20%
            errors.append(
                f"{len(long_review)} PRs took >1 week for first review - "
                "review process may be bottleneck"
            )

    return len(errors) == 0, errors


def validate_deployment_data(df: pd.DataFrame) -> Tuple[bool, List[str]]:
    """
    Validate deployment data for common issues.

    Returns:
        (is_valid, list_of_errors)
    """
    errors = []

    # Check for required columns
    required_cols = ["deployment_id", "deployment_date"]
    missing_cols = [col for col in required_cols if col not in df.columns]
    if missing_cols:
        errors.append(f"Missing required columns: {missing_cols}")
        return False, errors

    # Check deployment frequency
    df["deployment_date"] = pd.to_datetime(df["deployment_date"], errors="coerce")
    date_range = (df["deployment_date"].max() - df["deployment_date"].min()).days

    if date_range > 0:
        deployments_per_week = len(df) / (date_range / 7)

        if deployments_per_week < 0.5:  # Less than 1 every 2 weeks
            errors.append(
                f"Very low deployment frequency ({deployments_per_week:.1f} per week) - "
                "may indicate deployment bottleneck"
            )

    # Check for change failure rate
    if "status" in df.columns:
        failures = df[df["status"].isin(["failed", "rollback", "hotfix"])]
        failure_rate = len(failures) / len(df) * 100

        if failure_rate > 30:
            errors.append(
                f"High change failure rate ({failure_rate:.1f}%) - "
                "indicates quality or deployment process issues"
            )

    return len(errors) == 0, errors


# ============================================================================
# Finding Validators (R&D-specific)
# ============================================================================

def validate_rd_finding(finding: Finding) -> Tuple[bool, List[str]]:
    """
    Validate that a finding follows R&D efficiency best practices.

    Returns:
        (is_valid, list_of_errors)
    """
    errors = []

    # Check for common anti-patterns in R&D analysis
    statement_lower = finding.statement.lower()

    # Anti-pattern 1: Comparing velocity across teams
    if "velocity" in statement_lower and ("team" in statement_lower or "squad" in statement_lower):
        if "higher" in statement_lower or "lower" in statement_lower or "faster" in statement_lower:
            errors.append(
                "Comparing velocity across teams is an anti-pattern - "
                "story points are relative and team-specific"
            )

    # Anti-pattern 2: Using test coverage as sole quality metric
    if "test coverage" in statement_lower and "quality" in statement_lower:
        if not any(term in statement_lower for term in ["defect", "bug", "issue"]):
            errors.append(
                "Test coverage alone does not indicate quality - "
                "should correlate with defect rate or other quality metrics"
            )

    # Anti-pattern 3: Blaming individuals
    if any(term in statement_lower for term in ["developer", "engineer", "person"]):
        if any(term in statement_lower for term in ["slow", "poor", "bad", "problem"]):
            errors.append(
                "Avoid blaming individuals - focus on process and system issues"
            )

    # Anti-pattern 4: Ignoring context in cycle time
    if "cycle time" in statement_lower:
        if not any(term in statement_lower for term in ["type", "size", "complexity", "median", "p90"]):
            errors.append(
                "Cycle time analysis should segment by work item type/size "
                "and use median/percentiles, not just average"
            )

    # Check for missing context
    if finding.category == "velocity" and not finding.stats:
        errors.append("Velocity findings should include statistical data (mean, median, trend)")

    if finding.category == "quality" and not finding.stats:
        errors.append("Quality findings should include statistical data (defect counts, rates)")

    return len(errors) == 0, errors


def validate_rd_analysis_completeness(findings: List[Finding], metrics: List[MetricDefinition]) -> Tuple[bool, List[str]]:
    """
    Validate that the R&D analysis is complete and balanced.

    Returns:
        (is_valid, list_of_warnings)
    """
    warnings = []

    # Check for category balance
    categories = [f.category for f in findings if f.category]
    category_counts = pd.Series(categories).value_counts()

    # Should have findings in multiple categories
    if len(category_counts) == 1:
        warnings.append(
            f"All findings are in '{category_counts.index[0]}' category - "
            "consider broader analysis across velocity, quality, collaboration, delivery"
        )

    # Check for common missing analyses
    has_velocity = any(f.category == "velocity" for f in findings)
    has_quality = any(f.category == "quality" for f in findings)
    has_delivery = any(f.category == "delivery" for f in findings)

    if not has_velocity:
        warnings.append("No velocity metrics analyzed - consider adding throughput or cycle time analysis")

    if not has_quality:
        warnings.append("No quality metrics analyzed - consider adding defect rate or test coverage analysis")

    if not has_delivery:
        warnings.append("No delivery metrics analyzed - consider adding lead time or deployment frequency")

    # Check for metric definitions
    if len(metrics) == 0:
        warnings.append("No metrics explicitly defined - should declare key metrics used in analysis")

    # Check for related metrics
    metric_names = [m.metric_name for m in metrics]
    for metric_name in metric_names:
        known_metric = get_metric_definition(metric_name)
        if known_metric and known_metric.related_metrics:
            missing_related = [rm for rm in known_metric.related_metrics if rm not in metric_names]
            if missing_related:
                warnings.append(
                    f"Metric '{metric_name}' is typically analyzed with: {', '.join(missing_related[:2])}"
                )

    return True, warnings


# ============================================================================
# Time Window Validators
# ============================================================================

def validate_time_window(start_date: datetime, end_date: datetime, analysis_type: str) -> Tuple[bool, List[str]]:
    """
    Validate that the time window is appropriate for the analysis type.

    Returns:
        (is_valid, list_of_warnings)
    """
    warnings = []

    days = (end_date - start_date).days

    # Sprint analysis: should be 1-3 sprints (2-6 weeks)
    if analysis_type == "sprint":
        if days < 7:
            warnings.append(f"Time window of {days} days is too short for sprint analysis (need 2+ weeks)")
        elif days > 90:
            warnings.append(f"Time window of {days} days is very long - consider analyzing recent sprints only")

    # Trend analysis: should be 3+ months
    elif analysis_type == "trend":
        if days < 60:
            warnings.append(f"Time window of {days} days is too short for trend analysis (need 3+ months)")

    # Quality analysis: should be 1-3 months
    elif analysis_type == "quality":
        if days < 30:
            warnings.append(f"Time window of {days} days is too short for quality analysis (need 1+ month)")

    # Deployment analysis: should be 1+ month
    elif analysis_type == "deployment":
        if days < 30:
            warnings.append(f"Time window of {days} days is too short for deployment analysis (need 1+ month)")

    return True, warnings


# ============================================================================
# Aggregation Validators
# ============================================================================

def validate_aggregation_method(metric_name: str, agg_method: str) -> Tuple[bool, List[str]]:
    """
    Validate that the aggregation method is appropriate for the metric.

    Returns:
        (is_valid, list_of_errors)
    """
    errors = []
    known_metric = get_metric_definition(metric_name)

    if not known_metric:
        return True, []  # Can't validate custom metrics

    # Velocity: should use sum, not average
    if metric_name == "Story Points Completed":
        if agg_method.lower() not in ["sum", "total"]:
            errors.append(f"Velocity should use SUM, not {agg_method}")

    # Cycle time: should use median, not mean
    if metric_name in ["Cycle Time", "Lead Time", "PR Review Time"]:
        if agg_method.lower() not in ["median", "p50", "percentile"]:
            errors.append(f"{metric_name} should use MEDIAN or percentiles, not {agg_method}")

    # Rates: should use average or weighted average
    if metric_name in ["Defect Rate", "Change Failure Rate"]:
        if agg_method.lower() not in ["mean", "average", "weighted_average"]:
            errors.append(f"{metric_name} should use MEAN or WEIGHTED_AVERAGE, not {agg_method}")

    return len(errors) == 0, errors

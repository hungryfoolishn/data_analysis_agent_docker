"""
R&D Efficiency Metric Library

This module provides a library of pre-defined metric calculation methods
and validation logic for R&D efficiency analysis.
"""

from typing import Dict, List, Optional, Any, Tuple
import pandas as pd
from datetime import datetime, timedelta


# ============================================================================
# Metric Calculation Functions
# ============================================================================

def calculate_velocity(df: pd.DataFrame, sprint_col: str = "sprint", points_col: str = "story_points") -> pd.DataFrame:
    """
    Calculate velocity (story points completed per sprint).

    Args:
        df: DataFrame with completed work items
        sprint_col: Column name for sprint identifier
        points_col: Column name for story points

    Returns:
        DataFrame with sprint and velocity columns
    """
    velocity = df.groupby(sprint_col)[points_col].sum().reset_index()
    velocity.columns = [sprint_col, "velocity"]
    return velocity


def calculate_throughput(df: pd.DataFrame, period_col: str = "completed_date", freq: str = "W") -> pd.DataFrame:
    """
    Calculate throughput (items completed per time period).

    Args:
        df: DataFrame with completed work items
        period_col: Column name for completion date
        freq: Pandas frequency string (D=day, W=week, M=month)

    Returns:
        DataFrame with period and throughput columns
    """
    df[period_col] = pd.to_datetime(df[period_col])
    df["period"] = df[period_col].dt.to_period(freq)
    throughput = df.groupby("period").size().reset_index(name="throughput")
    return throughput


def calculate_cycle_time(df: pd.DataFrame, start_col: str = "started_date", end_col: str = "completed_date") -> pd.Series:
    """
    Calculate cycle time (time from start to completion).

    Args:
        df: DataFrame with work items
        start_col: Column name for start date
        end_col: Column name for completion date

    Returns:
        Series with cycle time in days
    """
    df[start_col] = pd.to_datetime(df[start_col])
    df[end_col] = pd.to_datetime(df[end_col])
    cycle_time = (df[end_col] - df[start_col]).dt.days
    return cycle_time


def calculate_lead_time(df: pd.DataFrame, created_col: str = "created_date", completed_col: str = "completed_date") -> pd.Series:
    """
    Calculate lead time (time from creation to completion).

    Args:
        df: DataFrame with work items
        created_col: Column name for creation date
        completed_col: Column name for completion date

    Returns:
        Series with lead time in days
    """
    df[created_col] = pd.to_datetime(df[created_col])
    df[completed_col] = pd.to_datetime(df[completed_col])
    lead_time = (df[completed_col] - df[created_col]).dt.days
    return lead_time


def calculate_defect_rate(features_df: pd.DataFrame, bugs_df: pd.DataFrame, period_col: str = "completed_date") -> pd.DataFrame:
    """
    Calculate defect rate (bugs per feature delivered).

    Args:
        features_df: DataFrame with completed features
        bugs_df: DataFrame with bugs found
        period_col: Column name for completion/found date

    Returns:
        DataFrame with period, feature_count, bug_count, defect_rate
    """
    features_df[period_col] = pd.to_datetime(features_df[period_col])
    bugs_df[period_col] = pd.to_datetime(bugs_df[period_col])

    features_df["period"] = features_df[period_col].dt.to_period("M")
    bugs_df["period"] = bugs_df[period_col].dt.to_period("M")

    feature_counts = features_df.groupby("period").size().reset_index(name="feature_count")
    bug_counts = bugs_df.groupby("period").size().reset_index(name="bug_count")

    result = pd.merge(feature_counts, bug_counts, on="period", how="outer").fillna(0)
    result["defect_rate"] = result["bug_count"] / result["feature_count"].replace(0, 1)

    return result


def calculate_sprint_commitment_accuracy(df: pd.DataFrame, sprint_col: str = "sprint",
                                         committed_col: str = "committed_points",
                                         completed_col: str = "completed_points") -> pd.DataFrame:
    """
    Calculate sprint commitment accuracy.

    Args:
        df: DataFrame with sprint data
        sprint_col: Column name for sprint identifier
        committed_col: Column name for committed story points
        completed_col: Column name for completed story points

    Returns:
        DataFrame with sprint, committed, completed, accuracy columns
    """
    result = df.groupby(sprint_col).agg({
        committed_col: "sum",
        completed_col: "sum"
    }).reset_index()

    result["accuracy"] = (result[completed_col] / result[committed_col].replace(0, 1)) * 100
    return result


def calculate_pr_review_time(df: pd.DataFrame, created_col: str = "pr_created_at",
                             reviewed_col: str = "first_review_at") -> pd.Series:
    """
    Calculate PR review time (time to first review).

    Args:
        df: DataFrame with PR data
        created_col: Column name for PR creation timestamp
        reviewed_col: Column name for first review timestamp

    Returns:
        Series with review time in hours
    """
    df[created_col] = pd.to_datetime(df[created_col])
    df[reviewed_col] = pd.to_datetime(df[reviewed_col])
    review_time = (df[reviewed_col] - df[created_col]).dt.total_seconds() / 3600
    return review_time


def calculate_deployment_frequency(df: pd.DataFrame, date_col: str = "deployment_date", freq: str = "W") -> pd.DataFrame:
    """
    Calculate deployment frequency.

    Args:
        df: DataFrame with deployment records
        date_col: Column name for deployment date
        freq: Pandas frequency string (D=day, W=week, M=month)

    Returns:
        DataFrame with period and deployment_count columns
    """
    df[date_col] = pd.to_datetime(df[date_col])
    df["period"] = df[date_col].dt.to_period(freq)
    deployment_freq = df.groupby("period").size().reset_index(name="deployment_count")
    return deployment_freq


def calculate_change_failure_rate(df: pd.DataFrame, status_col: str = "status") -> float:
    """
    Calculate change failure rate (percentage of failed deployments).

    Args:
        df: DataFrame with deployment records
        status_col: Column name for deployment status (success/failure)

    Returns:
        Change failure rate as percentage
    """
    total = len(df)
    if total == 0:
        return 0.0

    failures = len(df[df[status_col].isin(["failed", "rollback", "hotfix"])])
    return (failures / total) * 100


# ============================================================================
# Metric Validation Functions
# ============================================================================

def validate_velocity_calculation(df: pd.DataFrame, velocity_value: float) -> Tuple[bool, List[str]]:
    """
    Validate velocity calculation and identify potential issues.

    Returns:
        (is_valid, list_of_warnings)
    """
    warnings = []

    # Check for missing story points
    if "story_points" in df.columns:
        missing_points = df["story_points"].isna().sum()
        if missing_points > 0:
            warnings.append(f"{missing_points} items missing story points - excluded from calculation")

    # Check for zero or negative story points
    if "story_points" in df.columns:
        invalid_points = df[df["story_points"] <= 0]
        if len(invalid_points) > 0:
            warnings.append(f"{len(invalid_points)} items have zero or negative story points")

    # Check for unusually high velocity
    if velocity_value > 100:
        warnings.append(f"Velocity of {velocity_value} is unusually high - verify story point scale")

    # Check for unusually low velocity
    if velocity_value < 5:
        warnings.append(f"Velocity of {velocity_value} is unusually low - check data completeness")

    is_valid = len(warnings) == 0
    return is_valid, warnings


def validate_cycle_time_calculation(cycle_times: pd.Series) -> Tuple[bool, List[str]]:
    """
    Validate cycle time calculation and identify potential issues.

    Returns:
        (is_valid, list_of_warnings)
    """
    warnings = []

    # Check for negative cycle times
    negative_count = (cycle_times < 0).sum()
    if negative_count > 0:
        warnings.append(f"{negative_count} items have negative cycle time - check date fields")

    # Check for unusually long cycle times
    long_count = (cycle_times > 90).sum()
    if long_count > 0:
        warnings.append(f"{long_count} items have cycle time > 90 days - may indicate stale items")

    # Check for zero cycle times
    zero_count = (cycle_times == 0).sum()
    if zero_count > 0:
        warnings.append(f"{zero_count} items completed on same day they started")

    is_valid = negative_count == 0
    return is_valid, warnings


def validate_defect_rate_calculation(feature_count: int, bug_count: int, defect_rate: float) -> Tuple[bool, List[str]]:
    """
    Validate defect rate calculation and identify potential issues.

    Returns:
        (is_valid, list_of_warnings)
    """
    warnings = []

    # Check for zero features
    if feature_count == 0:
        warnings.append("No features delivered in this period - defect rate not meaningful")
        return False, warnings

    # Check for unusually high defect rate
    if defect_rate > 1.0:
        warnings.append(f"Defect rate of {defect_rate:.2f} is very high (>1 bug per feature)")

    # Check for zero bugs (might be data quality issue)
    if bug_count == 0 and feature_count > 10:
        warnings.append("Zero bugs found despite significant feature delivery - verify bug tracking")

    is_valid = True
    return is_valid, warnings


def validate_sprint_commitment(committed: float, completed: float, accuracy: float) -> Tuple[bool, List[str]]:
    """
    Validate sprint commitment accuracy and identify potential issues.

    Returns:
        (is_valid, list_of_warnings)
    """
    warnings = []

    # Check for zero commitment
    if committed == 0:
        warnings.append("Sprint had zero committed points - planning issue?")
        return False, warnings

    # Check for over-commitment
    if accuracy < 70:
        warnings.append(f"Only {accuracy:.1f}% of commitment completed - significant under-delivery")

    # Check for consistent 100% (sandbagging)
    if accuracy == 100:
        warnings.append("Exactly 100% commitment met - verify no sandbagging")

    # Check for over-delivery
    if accuracy > 120:
        warnings.append(f"{accuracy:.1f}% of commitment completed - scope added during sprint?")

    is_valid = True
    return is_valid, warnings


# ============================================================================
# Data Quality Checks
# ============================================================================

def check_required_columns(df: pd.DataFrame, required_cols: List[str]) -> Tuple[bool, List[str]]:
    """
    Check if DataFrame has all required columns.

    Returns:
        (has_all_columns, list_of_missing_columns)
    """
    missing = [col for col in required_cols if col not in df.columns]
    return len(missing) == 0, missing


def check_date_range(df: pd.DataFrame, date_col: str, expected_days: int = 90) -> Tuple[bool, str]:
    """
    Check if date range is reasonable.

    Returns:
        (is_reasonable, message)
    """
    if date_col not in df.columns:
        return False, f"Column '{date_col}' not found"

    df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
    valid_dates = df[date_col].dropna()

    if len(valid_dates) == 0:
        return False, f"No valid dates in column '{date_col}'"

    date_range = (valid_dates.max() - valid_dates.min()).days

    if date_range < 7:
        return False, f"Date range is only {date_range} days - too short for meaningful analysis"

    if date_range > 730:
        return True, f"Date range is {date_range} days (>2 years) - consider filtering to recent data"

    return True, f"Date range: {date_range} days"


def check_duplicate_records(df: pd.DataFrame, key_cols: List[str]) -> Tuple[bool, int]:
    """
    Check for duplicate records.

    Returns:
        (has_no_duplicates, duplicate_count)
    """
    if not key_cols:
        return True, 0

    duplicate_count = df.duplicated(subset=key_cols).sum()
    return duplicate_count == 0, duplicate_count


def check_null_values(df: pd.DataFrame, critical_cols: List[str]) -> Dict[str, int]:
    """
    Check for null values in critical columns.

    Returns:
        Dictionary mapping column name to null count
    """
    null_counts = {}
    for col in critical_cols:
        if col in df.columns:
            null_counts[col] = df[col].isna().sum()
    return null_counts


# ============================================================================
# Metric Interpretation Helpers
# ============================================================================

def interpret_velocity_trend(velocities: List[float]) -> str:
    """
    Interpret velocity trend over time.

    Returns:
        Human-readable interpretation
    """
    if len(velocities) < 2:
        return "Insufficient data for trend analysis"

    recent = velocities[-3:]
    earlier = velocities[:-3] if len(velocities) > 3 else velocities[:1]

    recent_avg = sum(recent) / len(recent)
    earlier_avg = sum(earlier) / len(earlier)

    change_pct = ((recent_avg - earlier_avg) / earlier_avg) * 100 if earlier_avg > 0 else 0

    if abs(change_pct) < 10:
        return f"Velocity is stable around {recent_avg:.1f} points"
    elif change_pct > 10:
        return f"Velocity is increasing (+{change_pct:.1f}%) - now {recent_avg:.1f} points"
    else:
        return f"Velocity is decreasing ({change_pct:.1f}%) - now {recent_avg:.1f} points"


def interpret_cycle_time(median_days: float, p90_days: float) -> str:
    """
    Interpret cycle time metrics.

    Returns:
        Human-readable interpretation
    """
    if median_days < 3:
        speed = "very fast"
    elif median_days < 7:
        speed = "fast"
    elif median_days < 14:
        speed = "moderate"
    else:
        speed = "slow"

    if p90_days > median_days * 3:
        consistency = "with high variability"
    elif p90_days > median_days * 2:
        consistency = "with moderate variability"
    else:
        consistency = "with good consistency"

    return f"Cycle time is {speed} (median {median_days:.1f} days) {consistency} (P90 {p90_days:.1f} days)"


def interpret_defect_rate(rate: float) -> str:
    """
    Interpret defect rate.

    Returns:
        Human-readable interpretation
    """
    if rate < 0.1:
        quality = "excellent"
    elif rate < 0.3:
        quality = "good"
    elif rate < 0.5:
        quality = "acceptable"
    else:
        quality = "concerning"

    return f"Defect rate of {rate:.2f} bugs/feature is {quality}"


def interpret_sprint_commitment(accuracy: float) -> str:
    """
    Interpret sprint commitment accuracy.

    Returns:
        Human-readable interpretation
    """
    if accuracy >= 90 and accuracy <= 110:
        return f"Sprint commitment accuracy of {accuracy:.1f}% is excellent - good planning"
    elif accuracy >= 80 and accuracy < 90:
        return f"Sprint commitment accuracy of {accuracy:.1f}% is acceptable - slight under-delivery"
    elif accuracy < 80:
        return f"Sprint commitment accuracy of {accuracy:.1f}% is low - significant under-delivery"
    else:
        return f"Sprint commitment accuracy of {accuracy:.1f}% indicates over-delivery - scope added during sprint?"

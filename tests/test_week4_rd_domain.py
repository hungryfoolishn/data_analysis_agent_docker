"""
Week 4 R&D Domain Tests

Tests for R&D efficiency domain knowledge, metric library, validators, and templates.
"""

import pytest
import pandas as pd
from datetime import datetime, timedelta

from langgraph_langchain.schemas import Finding, EvidenceItem, MetricDefinition
from langgraph_langchain.rd_efficiency_domain import (
    get_metric_definition,
    suggest_related_metrics,
    METRICS_BY_NAME,
)
from langgraph_langchain.rd_metric_library import (
    calculate_velocity,
    calculate_throughput,
    calculate_cycle_time,
    calculate_defect_rate,
    validate_velocity_calculation,
    validate_cycle_time_calculation,
    interpret_velocity_trend,
    interpret_cycle_time,
    interpret_defect_rate,
)
from langgraph_langchain.rd_validators import (
    validate_rd_metric_definition,
    validate_rd_finding,
    validate_rd_analysis_completeness,
    validate_sprint_data,
    validate_pr_data,
)
from langgraph_langchain.rd_templates import (
    suggest_template,
    get_template,
    TEMPLATES_BY_ID,
)


# ============================================================================
# Domain Knowledge Tests
# ============================================================================

def test_get_metric_definition():
    """Test retrieving metric definitions."""
    metric = get_metric_definition("Story Points Completed")
    assert metric is not None
    assert metric.metric_name == "Story Points Completed"
    assert metric.category == "velocity"
    assert "story points" in metric.definition.lower()
    assert len(metric.caveats) > 0


def test_get_unknown_metric():
    """Test retrieving unknown metric returns None."""
    metric = get_metric_definition("Unknown Metric")
    assert metric is None


def test_suggest_related_metrics():
    """Test suggesting related metrics."""
    related = suggest_related_metrics("Story Points Completed")
    assert isinstance(related, list)
    assert len(related) > 0
    assert "Team Size" in related or "Sprint Duration" in related


def test_all_metrics_have_required_fields():
    """Test that all metrics have required fields."""
    for metric_name, metric in METRICS_BY_NAME.items():
        assert metric.metric_name
        assert metric.category
        assert metric.definition
        assert metric.calculation_method
        assert metric.unit
        assert metric.good_direction in ["higher", "lower", "stable"]


# ============================================================================
# Metric Library Tests
# ============================================================================

def test_calculate_velocity():
    """Test velocity calculation."""
    df = pd.DataFrame({
        "sprint": ["S1", "S1", "S2", "S2", "S3"],
        "story_points": [5, 8, 3, 10, 7]
    })

    velocity = calculate_velocity(df, "sprint", "story_points")

    assert len(velocity) == 3
    assert velocity.loc[velocity["sprint"] == "S1", "velocity"].values[0] == 13
    assert velocity.loc[velocity["sprint"] == "S2", "velocity"].values[0] == 13
    assert velocity.loc[velocity["sprint"] == "S3", "velocity"].values[0] == 7


def test_calculate_throughput():
    """Test throughput calculation."""
    dates = pd.date_range("2025-01-01", periods=10, freq="D")
    df = pd.DataFrame({
        "completed_date": dates,
        "item_id": range(10)
    })

    throughput = calculate_throughput(df, "completed_date", freq="W")

    assert len(throughput) > 0
    assert "throughput" in throughput.columns


def test_calculate_cycle_time():
    """Test cycle time calculation."""
    df = pd.DataFrame({
        "started_date": ["2025-01-01", "2025-01-05", "2025-01-10"],
        "completed_date": ["2025-01-05", "2025-01-08", "2025-01-15"]
    })

    cycle_time = calculate_cycle_time(df, "started_date", "completed_date")

    assert len(cycle_time) == 3
    assert cycle_time[0] == 4
    assert cycle_time[1] == 3
    assert cycle_time[2] == 5


def test_calculate_defect_rate():
    """Test defect rate calculation."""
    features = pd.DataFrame({
        "completed_date": pd.date_range("2025-01-01", periods=10, freq="D")
    })
    bugs = pd.DataFrame({
        "completed_date": pd.date_range("2025-01-01", periods=3, freq="D")
    })

    defect_rate = calculate_defect_rate(features, bugs, "completed_date")

    assert len(defect_rate) > 0
    assert "defect_rate" in defect_rate.columns


def test_validate_velocity_calculation():
    """Test velocity validation."""
    df = pd.DataFrame({
        "story_points": [5, 8, 3, 10, 7]
    })

    is_valid, warnings = validate_velocity_calculation(df, 33)

    assert is_valid
    assert len(warnings) == 0


def test_validate_velocity_with_missing_points():
    """Test velocity validation with missing story points."""
    df = pd.DataFrame({
        "story_points": [5, None, 3, 10, 7]
    })

    is_valid, warnings = validate_velocity_calculation(df, 25)

    assert not is_valid
    assert any("missing story points" in w.lower() for w in warnings)


def test_validate_cycle_time():
    """Test cycle time validation."""
    cycle_times = pd.Series([3, 5, 7, 4, 6])

    is_valid, warnings = validate_cycle_time_calculation(cycle_times)

    assert is_valid
    assert len(warnings) == 0


def test_validate_cycle_time_with_negatives():
    """Test cycle time validation with negative values."""
    cycle_times = pd.Series([3, -5, 7, 4, 6])

    is_valid, warnings = validate_cycle_time_calculation(cycle_times)

    assert not is_valid
    assert any("negative cycle time" in w.lower() for w in warnings)


def test_interpret_velocity_trend_increasing():
    """Test velocity trend interpretation - increasing."""
    velocities = [40, 42, 45, 48, 50]

    interpretation = interpret_velocity_trend(velocities)

    assert "increasing" in interpretation.lower()
    # Check for recent average (47.7) instead of exact last value
    assert "47" in interpretation or "48" in interpretation


def test_interpret_velocity_trend_stable():
    """Test velocity trend interpretation - stable."""
    velocities = [42, 43, 42, 44, 43]

    interpretation = interpret_velocity_trend(velocities)

    assert "stable" in interpretation.lower()


def test_interpret_cycle_time():
    """Test cycle time interpretation."""
    interpretation = interpret_cycle_time(5.2, 7.8)

    assert "5.2" in interpretation
    assert "7.8" in interpretation
    assert "fast" in interpretation.lower() or "moderate" in interpretation.lower()


def test_interpret_defect_rate_good():
    """Test defect rate interpretation - good."""
    interpretation = interpret_defect_rate(0.25)

    assert "0.25" in interpretation
    assert "good" in interpretation.lower() or "acceptable" in interpretation.lower()


def test_interpret_defect_rate_concerning():
    """Test defect rate interpretation - concerning."""
    interpretation = interpret_defect_rate(0.8)

    assert "0.8" in interpretation
    assert "concerning" in interpretation.lower()


# ============================================================================
# Validator Tests
# ============================================================================

def test_validate_rd_metric_definition_standard():
    """Test validating standard R&D metric definition."""
    metric = MetricDefinition(
        metric_name="Story Points Completed",
        definition_text="Sum of story points for completed items in a sprint"
    )

    is_valid, errors = validate_rd_metric_definition(metric)

    assert is_valid
    assert len(errors) == 0


def test_validate_rd_metric_definition_custom():
    """Test validating custom metric definition."""
    metric = MetricDefinition(
        metric_name="Custom Metric",
        definition_text="Some custom calculation"
    )

    is_valid, errors = validate_rd_metric_definition(metric)

    # Custom metrics should pass validation
    assert is_valid


def test_validate_rd_finding_velocity_comparison_antipattern():
    """Test detecting velocity comparison anti-pattern."""
    finding = Finding(
        finding_id="F001",
        statement="Team A velocity is 20% higher than Team B",
        evidence=[EvidenceItem(evidence_text="Team A: 50 points, Team B: 40 points")],
        evidence_level="B"
    )

    is_valid, errors = validate_rd_finding(finding)

    assert not is_valid
    assert any("velocity across teams" in err.lower() for err in errors)


def test_validate_rd_finding_test_coverage_antipattern():
    """Test detecting test coverage as sole quality metric anti-pattern."""
    finding = Finding(
        finding_id="F001",
        statement="Test coverage is 80% so quality is good",
        evidence=[EvidenceItem(evidence_text="Coverage increased from 70% to 80%")],
        evidence_level="B"
    )

    is_valid, errors = validate_rd_finding(finding)

    assert not is_valid
    assert any("test coverage alone" in err.lower() for err in errors)


def test_validate_rd_finding_valid():
    """Test validating a valid R&D finding."""
    finding = Finding(
        finding_id="F001",
        statement="Sprint 15 velocity was 45 points, consistent with recent trend",
        evidence=[EvidenceItem(evidence_text="Last 3 sprints: 42, 44, 45 points")],
        evidence_level="A",
        category="velocity",
        stats={"sprint_15_velocity": 45, "avg_recent_velocity": 43.7}
    )

    is_valid, errors = validate_rd_finding(finding)

    assert is_valid
    assert len(errors) == 0


def test_validate_rd_analysis_completeness_balanced():
    """Test validating balanced R&D analysis."""
    findings = [
        Finding(
            finding_id="F001",
            statement="Velocity is stable",
            evidence=[EvidenceItem(evidence_text="45 points")],
            evidence_level="A",
            category="velocity"
        ),
        Finding(
            finding_id="F002",
            statement="Defect rate is low",
            evidence=[EvidenceItem(evidence_text="0.2 bugs/feature")],
            evidence_level="A",
            category="quality"
        ),
        Finding(
            finding_id="F003",
            statement="Cycle time is fast",
            evidence=[EvidenceItem(evidence_text="Median 5 days")],
            evidence_level="A",
            category="delivery"
        ),
    ]

    metrics = [
        MetricDefinition(metric_name="Story Points Completed", definition_text="Sum of points"),
        MetricDefinition(metric_name="Defect Rate", definition_text="Bugs per feature"),
    ]

    is_complete, warnings = validate_rd_analysis_completeness(findings, metrics)

    assert is_complete
    # May have warnings about missing categories, but should be complete


def test_validate_rd_analysis_completeness_unbalanced():
    """Test detecting unbalanced R&D analysis."""
    findings = [
        Finding(
            finding_id="F001",
            statement="Velocity is stable",
            evidence=[EvidenceItem(evidence_text="45 points")],
            evidence_level="A",
            category="velocity"
        ),
        Finding(
            finding_id="F002",
            statement="Velocity increased",
            evidence=[EvidenceItem(evidence_text="50 points")],
            evidence_level="A",
            category="velocity"
        ),
    ]

    metrics = []

    is_complete, warnings = validate_rd_analysis_completeness(findings, metrics)

    assert len(warnings) > 0
    assert any("velocity" in w.lower() for w in warnings)


def test_validate_sprint_data_valid():
    """Test validating valid sprint data."""
    df = pd.DataFrame({
        "sprint": ["S1", "S1", "S2"],
        "story_points": [5, 8, 10],
        "started_date": ["2025-01-01", "2025-01-02", "2025-01-15"],
        "completed_date": ["2025-01-05", "2025-01-08", "2025-01-20"]
    })

    is_valid, errors = validate_sprint_data(df)

    assert is_valid
    assert len(errors) == 0


def test_validate_sprint_data_with_zero_points():
    """Test detecting zero story points."""
    df = pd.DataFrame({
        "sprint": ["S1", "S1", "S2"],
        "story_points": [0, 0, 10]
    })

    is_valid, errors = validate_sprint_data(df)

    assert not is_valid
    assert any("zero story points" in err.lower() for err in errors)


def test_validate_pr_data_valid():
    """Test validating valid PR data."""
    df = pd.DataFrame({
        "pr_id": [1, 2, 3],
        "lines_added": [50, 100, 200],
        "lines_deleted": [20, 30, 50],
        "pr_created_at": ["2025-01-01", "2025-01-02", "2025-01-03"],
        "first_review_at": ["2025-01-01 10:00", "2025-01-02 14:00", "2025-01-03 09:00"]
    })

    is_valid, errors = validate_pr_data(df)

    assert is_valid
    assert len(errors) == 0


# ============================================================================
# Template Tests
# ============================================================================

def test_get_template_by_id():
    """Test retrieving template by ID."""
    template = get_template("RD-T001")

    assert template is not None
    assert template.template_id == "RD-T001"
    assert template.template_name == "Sprint Retrospective Analysis"


def test_suggest_template_sprint_data():
    """Test suggesting template for sprint data."""
    columns = ["sprint", "story_points", "status", "started_date", "completed_date"]

    template = suggest_template("", columns)

    assert template is not None
    assert "sprint" in template.template_name.lower()


def test_suggest_template_pr_data():
    """Test suggesting template for PR data."""
    columns = ["pr_id", "pr_created_at", "first_review_at", "lines_added"]

    template = suggest_template("", columns)

    assert template is not None
    assert "pr" in template.template_name.lower() or "pull request" in template.template_name.lower()


def test_suggest_template_by_question():
    """Test suggesting template by user question."""
    template = suggest_template("分析 sprint 回顾", [])

    assert template is not None
    assert "sprint" in template.template_name.lower()


def test_all_templates_have_required_fields():
    """Test that all templates have required fields."""
    for template_id, template in TEMPLATES_BY_ID.items():
        assert template.template_id
        assert template.template_name
        assert template.description
        assert template.target_user
        assert template.difficulty in ["easy", "medium", "hard"]
        assert len(template.required_data) > 0
        assert len(template.key_metrics) > 0
        assert len(template.analysis_steps) > 0
        assert len(template.expected_findings) > 0
        assert len(template.report_sections) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

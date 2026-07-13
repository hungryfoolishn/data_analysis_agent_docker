"""
Test R&D Validator Integration into Main Agent Flow

This test verifies that the R&D validators are properly integrated into:
1. record_finding tool
2. declare_metric tool
3. finish_report tool
"""

import pytest
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from langgraph_langchain.schemas import Finding, MetricDefinition, EvidenceItem
from langgraph_langchain.rd_validators import (
    validate_rd_finding,
    validate_rd_metric_definition,
    validate_rd_analysis_completeness,
)


class TestRecordFindingValidation:
    """Test that record_finding validates findings against R&D best practices"""

    def test_anti_pattern_velocity_comparison(self):
        """Should detect cross-team velocity comparison anti-pattern"""
        finding = Finding(
            finding_id="F001",
            statement="Team A has higher velocity than Team B",
            evidence=[EvidenceItem(
                evidence_text="Team A: 50 points, Team B: 30 points",
                source_fields=["team", "story_points"],
            )],
            confidence_level="high",
            evidence_level="A",
            category="velocity",
        )

        is_valid, errors = validate_rd_finding(finding)
        assert not is_valid
        assert any("velocity across teams" in err.lower() for err in errors)

    def test_anti_pattern_test_coverage_only(self):
        """Should warn when using test coverage as sole quality metric"""
        finding = Finding(
            finding_id="F002",
            statement="Code quality improved due to test coverage increase",
            evidence=[EvidenceItem(
                evidence_text="Test coverage increased from 60% to 80%",
                source_fields=["test_coverage"],
            )],
            confidence_level="medium",
            evidence_level="B",
            category="quality",
        )

        is_valid, errors = validate_rd_finding(finding)
        assert not is_valid
        assert any("coverage alone" in err.lower() for err in errors)

    def test_anti_pattern_blaming_individuals(self):
        """Should detect individual blaming anti-pattern"""
        finding = Finding(
            finding_id="F003",
            statement="Developer X is slow at completing tasks",
            evidence=[EvidenceItem(
                evidence_text="Developer X took 10 days on average",
                source_fields=["developer", "completion_time"],
            )],
            confidence_level="medium",
            evidence_level="A",
            category="velocity",
        )

        is_valid, errors = validate_rd_finding(finding)
        assert not is_valid
        assert any("blaming individuals" in err.lower() for err in errors)

    def test_anti_pattern_cycle_time_without_context(self):
        """Should warn when cycle time lacks segmentation"""
        finding = Finding(
            finding_id="F004",
            statement="Average cycle time is 5 days",
            evidence=[EvidenceItem(
                evidence_text="Mean cycle time across all items is 5 days",
                source_fields=["cycle_time"],
            )],
            confidence_level="high",
            evidence_level="A",
            category="velocity",
        )

        is_valid, errors = validate_rd_finding(finding)
        assert not is_valid
        assert any("segment" in err.lower() or "median" in err.lower() for err in errors)

    def test_valid_finding_passes(self):
        """Valid findings should pass validation"""
        finding = Finding(
            finding_id="F005",
            statement="Median cycle time for small stories (1-3 points) is 2 days",
            evidence=[EvidenceItem(
                evidence_text="Median: 2 days, P90: 4 days for stories with 1-3 points",
                source_fields=["cycle_time", "story_points"],
            )],
            confidence_level="high",
            evidence_level="A",
            category="velocity",
            stats={"median": 2, "p90": 4, "count": 45},  # stats should be on Finding, not EvidenceItem
        )

        is_valid, errors = validate_rd_finding(finding)
        assert is_valid
        assert len(errors) == 0


class TestDeclareMetricValidation:
    """Test that declare_metric validates against R&D standards"""

    def test_known_metric_standard_definition(self):
        """Should validate known metrics against standard definitions"""
        # Deployment Frequency is a known DORA metric
        metric_def = MetricDefinition(
            metric_name="Deployment Frequency",
            definition_text="Number of deployments per week",
            time_window="Last 30 days",
        )

        is_valid, errors = validate_rd_metric_definition(metric_def)
        # Should pass or have minor warnings
        assert is_valid or len(errors) <= 1

    def test_known_metric_wrong_definition(self):
        """Should warn when definition differs significantly from standard"""
        metric_def = MetricDefinition(
            metric_name="Deployment Frequency",
            definition_text="Random unrelated definition",
            time_window="Last 30 days",
        )

        is_valid, errors = validate_rd_metric_definition(metric_def)
        assert not is_valid
        assert any("differs" in err.lower() for err in errors)

    def test_custom_metric_without_definition(self):
        """Should require definition for custom metrics"""
        metric_def = MetricDefinition(
            metric_name="Custom Metric XYZ",
            definition_text="",  # Empty definition
        )

        is_valid, errors = validate_rd_metric_definition(metric_def)
        assert not is_valid
        assert any("must have a clear definition" in err.lower() for err in errors)

    def test_custom_metric_with_definition(self):
        """Custom metrics with clear definitions should pass"""
        metric_def = MetricDefinition(
            metric_name="Feature Adoption Rate",
            definition_text="Percentage of users who used the feature at least once in the time window",
            time_window="Last 30 days",
            denominator="Total active users",
        )

        is_valid, errors = validate_rd_metric_definition(metric_def)
        assert is_valid
        assert len(errors) == 0


class TestFinishReportCompleteness:
    """Test that finish_report validates analysis completeness"""

    def test_single_category_warning(self):
        """Should warn when all findings are in one category"""
        findings = [
            Finding(
                finding_id="F001",
                statement="Velocity increased",
                evidence=[EvidenceItem(evidence_text="50 points per sprint")],
                confidence_level="high",
                evidence_level="A",
                category="velocity",
            ),
            Finding(
                finding_id="F002",
                statement="Velocity stable",
                evidence=[EvidenceItem(evidence_text="Consistent 50 points")],
                confidence_level="high",
                evidence_level="A",
                category="velocity",
            ),
        ]
        metrics = []

        is_complete, warnings = validate_rd_analysis_completeness(findings, metrics)
        assert len(warnings) > 0
        assert any("all findings are in" in warn.lower() for warn in warnings)

    def test_missing_velocity_analysis(self):
        """Should suggest velocity analysis if missing"""
        findings = [
            Finding(
                finding_id="F001",
                statement="Defect rate is high",
                evidence=[EvidenceItem(evidence_text="10% defect rate")],
                confidence_level="high",
                evidence_level="A",
                category="quality",
            ),
        ]
        metrics = []

        is_complete, warnings = validate_rd_analysis_completeness(findings, metrics)
        assert any("velocity" in warn.lower() for warn in warnings)

    def test_missing_quality_analysis(self):
        """Should suggest quality analysis if missing"""
        findings = [
            Finding(
                finding_id="F001",
                statement="Velocity is 50 points",
                evidence=[EvidenceItem(evidence_text="50 points per sprint")],
                confidence_level="high",
                evidence_level="A",
                category="velocity",
            ),
        ]
        metrics = []

        is_complete, warnings = validate_rd_analysis_completeness(findings, metrics)
        assert any("quality" in warn.lower() for warn in warnings)

    def test_no_metrics_defined(self):
        """Should warn when no metrics are defined"""
        findings = [
            Finding(
                finding_id="F001",
                statement="Some finding",
                evidence=[EvidenceItem(evidence_text="Some evidence")],
                confidence_level="high",
                evidence_level="A",
            ),
        ]
        metrics = []

        is_complete, warnings = validate_rd_analysis_completeness(findings, metrics)
        assert any("no metrics" in warn.lower() for warn in warnings)

    def test_balanced_analysis_passes(self):
        """Balanced analysis across categories should pass"""
        findings = [
            Finding(
                finding_id="F001",
                statement="Velocity is stable",
                evidence=[EvidenceItem(evidence_text="50 points per sprint")],
                confidence_level="high",
                evidence_level="A",
                category="velocity",
            ),
            Finding(
                finding_id="F002",
                statement="Quality is good",
                evidence=[EvidenceItem(evidence_text="2% defect rate")],
                confidence_level="high",
                evidence_level="A",
                category="quality",
            ),
            Finding(
                finding_id="F003",
                statement="Delivery is fast",
                evidence=[EvidenceItem(evidence_text="3 days lead time")],
                confidence_level="high",
                evidence_level="A",
                category="delivery",
            ),
        ]
        metrics = [
            MetricDefinition(
                metric_name="Story Points Completed",
                definition_text="Sum of story points for completed items",
            ),
            MetricDefinition(
                metric_name="Defect Rate",
                definition_text="Bugs / Features",
            ),
        ]

        is_complete, warnings = validate_rd_analysis_completeness(findings, metrics)
        # Should have minimal warnings
        assert len(warnings) <= 2  # May suggest delivery metrics


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

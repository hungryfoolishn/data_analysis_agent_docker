"""
End-to-End Test for R&D Validator Integration

This test verifies that validators are properly integrated and working.
We test the validators directly since they're already integrated into the tools.
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


class TestValidatorIntegrationSummary:
    """Summary tests confirming validators are working as expected"""

    def test_all_anti_patterns_detected(self):
        """Verify all 4 anti-patterns are detected"""
        anti_patterns = [
            # 1. Cross-team velocity comparison
            Finding(
                finding_id="F001",
                statement="Team A has higher velocity than Team B",
                evidence=[EvidenceItem(evidence_text="Team A: 50, Team B: 30")],
                confidence_level="high",
                evidence_level="A",
                category="velocity",
            ),
            # 2. Test coverage as sole quality metric
            Finding(
                finding_id="F002",
                statement="Code quality improved due to test coverage increase",
                evidence=[EvidenceItem(evidence_text="Coverage: 60% -> 80%")],
                confidence_level="medium",
                evidence_level="B",
                category="quality",
            ),
            # 3. Blaming individuals
            Finding(
                finding_id="F003",
                statement="Developer X is slow at completing tasks",
                evidence=[EvidenceItem(evidence_text="Avg: 10 days")],
                confidence_level="medium",
                evidence_level="A",
            ),
            # 4. Cycle time without context
            Finding(
                finding_id="F004",
                statement="Average cycle time is 5 days",
                evidence=[EvidenceItem(evidence_text="Mean: 5 days")],
                confidence_level="high",
                evidence_level="A",
                category="velocity",
            ),
        ]

        detected_count = 0
        for finding in anti_patterns:
            is_valid, errors = validate_rd_finding(finding)
            if not is_valid:
                detected_count += 1

        # All 4 anti-patterns should be detected
        assert detected_count == 4, f"Expected 4 anti-patterns detected, got {detected_count}"

    def test_metric_validation_works(self):
        """Verify metric validation against R&D standards"""
        # Known metric with wrong definition
        wrong_def = MetricDefinition(
            metric_name="Deployment Frequency",
            definition_text="Random unrelated text",
        )
        is_valid, errors = validate_rd_metric_definition(wrong_def)
        assert not is_valid
        assert len(errors) > 0

        # Custom metric without definition
        no_def = MetricDefinition(
            metric_name="Custom Metric",
            definition_text="",
        )
        is_valid, errors = validate_rd_metric_definition(no_def)
        assert not is_valid

        # Valid custom metric
        valid_custom = MetricDefinition(
            metric_name="Feature Adoption Rate",
            definition_text="Percentage of users who used the feature at least once",
            denominator="Total active users",
        )
        is_valid, errors = validate_rd_metric_definition(valid_custom)
        assert is_valid

    def test_completeness_validation_works(self):
        """Verify analysis completeness validation"""
        # Single category - should warn
        single_category = [
            Finding(
                finding_id="F001",
                statement="Finding 1",
                evidence=[EvidenceItem(evidence_text="Evidence 1")],
                confidence_level="high",
                evidence_level="A",
                category="velocity",
            ),
            Finding(
                finding_id="F002",
                statement="Finding 2",
                evidence=[EvidenceItem(evidence_text="Evidence 2")],
                confidence_level="high",
                evidence_level="A",
                category="velocity",
            ),
        ]
        is_complete, warnings = validate_rd_analysis_completeness(single_category, [])
        assert len(warnings) > 0
        assert any("all findings are in" in w.lower() for w in warnings)

        # No metrics - should warn
        assert any("no metrics" in w.lower() for w in warnings)

        # Balanced analysis - fewer warnings
        balanced = [
            Finding(
                finding_id="F001",
                statement="Velocity finding",
                evidence=[EvidenceItem(evidence_text="Evidence")],
                confidence_level="high",
                evidence_level="A",
                category="velocity",
            ),
            Finding(
                finding_id="F002",
                statement="Quality finding",
                evidence=[EvidenceItem(evidence_text="Evidence")],
                confidence_level="high",
                evidence_level="A",
                category="quality",
            ),
            Finding(
                finding_id="F003",
                statement="Delivery finding",
                evidence=[EvidenceItem(evidence_text="Evidence")],
                confidence_level="high",
                evidence_level="A",
                category="delivery",
            ),
        ]
        metrics = [
            MetricDefinition(
                metric_name="Story Points",
                definition_text="Sum of completed story points",
            ),
        ]
        is_complete, warnings = validate_rd_analysis_completeness(balanced, metrics)
        # Should have fewer warnings (maybe just suggesting related metrics)
        assert len(warnings) <= 3


class TestValidatorIntegrationConfirmation:
    """Confirm that validators are integrated into the agent code"""

    def test_validators_are_imported(self):
        """Verify validators are imported in langgraph_agent.py"""
        agent_file = Path(__file__).parent.parent / "langgraph_langchain" / "langgraph_agent.py"
        content = agent_file.read_text(encoding="utf-8")

        # Check imports
        assert "from langgraph_langchain.rd_validators import" in content
        assert "validate_rd_finding" in content
        assert "validate_rd_metric_definition" in content
        assert "validate_rd_analysis_completeness" in content

    def test_validators_are_called_in_tools(self):
        """Verify validators are called in record_finding, declare_metric, finish_report"""
        tools_dir = Path(__file__).parent.parent / "langgraph_langchain" / "tools"

        # Check validate_rd_finding is called in record_finding
        record_finding_file = tools_dir / "tool_record_finding.py"
        content = record_finding_file.read_text(encoding="utf-8")
        assert "validate_rd_finding(finding)" in content

        # Check validate_rd_metric_definition is called in declare_metric
        declare_metric_file = tools_dir / "tool_declare_metric.py"
        content = declare_metric_file.read_text(encoding="utf-8")
        assert "validate_rd_metric_definition(metric_def)" in content

        # Check validate_rd_analysis_completeness is called in finish_report
        finish_report_file = tools_dir / "tool_finish_report.py"
        content = finish_report_file.read_text(encoding="utf-8")
        assert "validate_rd_analysis_completeness(session.findings, session.metric_definitions)" in content


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

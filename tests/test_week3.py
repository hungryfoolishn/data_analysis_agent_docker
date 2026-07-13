"""
Unit tests for Week 3: Reviewable Governance

Tests evidence level validation and recommendation-evidence binding.
"""

import pytest
from langgraph_langchain.schemas import Finding, EvidenceItem, AnalysisAssumption, MetricDefinition
from langgraph_langchain.evidence_validator import (
    validate_evidence_level,
    validate_findings_evidence_levels,
    check_causal_language,
    check_correlation_language,
    suggest_evidence_level,
)
from langgraph_langchain.recommendation_validator import (
    classify_recommendation_type,
    validate_recommendation_language,
    validate_recommendations_against_findings,
    RecommendationType,
)


class TestEvidenceValidator:
    """Test evidence level validation."""

    def test_level_a_with_factual_language(self):
        """Level A should pass with pure factual language."""
        finding = Finding(
            finding_id="F001",
            statement="North region Q3 revenue is $420K, accounting for 42% of total",
            evidence=[EvidenceItem(evidence_text="Calculated from revenue field")],
            confidence_level="high",
            evidence_level="A",
        )
        is_valid, error = validate_evidence_level(finding)
        assert is_valid
        assert error == ""

    def test_level_a_with_correlation_language_fails(self):
        """Level A should fail with correlation language."""
        finding = Finding(
            finding_id="F002",
            statement="Revenue is correlated with order count",
            evidence=[EvidenceItem(evidence_text="Correlation coefficient 0.85")],
            confidence_level="medium",
            evidence_level="A",
        )
        is_valid, error = validate_evidence_level(finding)
        assert not is_valid
        assert "correlation language" in error.lower()

    def test_causal_language_without_level_c_fails(self):
        """Causal language should fail without Level C."""
        finding = Finding(
            finding_id="F003",
            statement="Price increase caused conversion rate to drop",
            evidence=[EvidenceItem(evidence_text="Conversion dropped after price change")],
            confidence_level="medium",
            evidence_level="B",
        )
        is_valid, error = validate_evidence_level(finding)
        assert not is_valid
        assert "causal language" in error.lower()
        assert "evidence_level='C'" in error

    def test_causal_language_with_level_c_passes(self):
        """Causal language should pass with Level C and sufficient evidence."""
        finding = Finding(
            finding_id="F004",
            statement="Price increase caused conversion rate to drop",
            evidence=[
                EvidenceItem(evidence_text="Conversion dropped after price change"),
                EvidenceItem(evidence_text="Control group maintained conversion rate"),
            ],
            confidence_level="high",
            evidence_level="C",
        )
        is_valid, error = validate_evidence_level(finding)
        assert is_valid
        assert error == ""

    def test_causal_language_with_insufficient_evidence_fails(self):
        """Level C with only 1 evidence item should fail."""
        finding = Finding(
            finding_id="F005",
            statement="Price increase caused conversion rate to drop",
            evidence=[EvidenceItem(evidence_text="Conversion dropped after price change")],
            confidence_level="high",
            evidence_level="C",
        )
        is_valid, error = validate_evidence_level(finding)
        assert not is_valid
        assert "at least 2 pieces of evidence" in error.lower()

    def test_check_causal_language(self):
        """Test causal keyword detection."""
        assert len(check_causal_language("Price increase caused conversion drop")) > 0
        assert len(check_causal_language("Revenue is driven by North region")) > 0
        assert len(check_causal_language("价格上涨导致转化率下降")) > 0
        assert len(check_causal_language("Revenue is $420K")) == 0

    def test_check_correlation_language(self):
        """Test correlation keyword detection."""
        assert len(check_correlation_language("Revenue is correlated with orders")) > 0
        assert len(check_correlation_language("Revenue and orders are associated")) > 0
        assert len(check_correlation_language("Revenue is $420K")) == 0

    def test_suggest_evidence_level(self):
        """Test evidence level suggestion."""
        assert suggest_evidence_level("Price caused drop", 2) == "C"
        assert suggest_evidence_level("Revenue is correlated with orders", 2) == "B"
        assert suggest_evidence_level("Revenue is $420K", 1) == "A"

    def test_validate_findings_evidence_levels(self):
        """Test batch validation of findings."""
        findings = [
            Finding(
                finding_id="F001",
                statement="North region accounts for 42%",
                evidence=[EvidenceItem(evidence_text="Calculated")],
                confidence_level="high",
                evidence_level="A",
            ),
            Finding(
                finding_id="F002",
                statement="Price caused conversion drop",
                evidence=[EvidenceItem(evidence_text="Dropped after change")],
                confidence_level="high",
                evidence_level="B",  # Should be C
            ),
        ]
        errors = validate_findings_evidence_levels(findings)
        assert len(errors) == 1
        assert "F002" in errors[0]
        assert "causal language" in errors[0].lower()


class TestRecommendationValidator:
    """Test recommendation-evidence binding."""

    def test_classify_immediate_action(self):
        """High confidence + Level B/C + multiple evidence = immediate action."""
        finding = Finding(
            finding_id="F001",
            statement="North region has highest ROI",
            evidence=[
                EvidenceItem(evidence_text="ROI is 3.5x"),
                EvidenceItem(evidence_text="Consistent across 3 quarters"),
            ],
            confidence_level="high",
            evidence_level="B",
        )
        rec_type = classify_recommendation_type(finding)
        assert rec_type == RecommendationType.IMMEDIATE_ACTION

    def test_classify_validation(self):
        """Medium confidence = validation."""
        finding = Finding(
            finding_id="F002",
            statement="South region shows potential",
            evidence=[EvidenceItem(evidence_text="Growth trend observed")],
            confidence_level="medium",
            evidence_level="B",
        )
        rec_type = classify_recommendation_type(finding)
        assert rec_type == RecommendationType.VALIDATION

    def test_classify_observation(self):
        """Low confidence or Level A = observation."""
        finding = Finding(
            finding_id="F003",
            statement="West region revenue is $200K",
            evidence=[EvidenceItem(evidence_text="Calculated from data")],
            confidence_level="low",
            evidence_level="A",
        )
        rec_type = classify_recommendation_type(finding)
        assert rec_type == RecommendationType.OBSERVATION

    def test_strong_action_with_weak_evidence_fails(self):
        """Strong action language should fail with weak evidence."""
        finding = Finding(
            finding_id="F004",
            statement="Revenue is $200K",
            evidence=[EvidenceItem(evidence_text="Calculated")],
            confidence_level="low",
            evidence_level="A",
        )
        recommendation = "Should immediately increase investment in this region"
        is_valid, error = validate_recommendation_language(recommendation, finding)
        assert not is_valid
        assert "observation language" in error.lower()

    def test_strong_action_with_strong_evidence_passes(self):
        """Strong action language should pass with strong evidence."""
        finding = Finding(
            finding_id="F005",
            statement="North region has highest ROI",
            evidence=[
                EvidenceItem(evidence_text="ROI is 3.5x"),
                EvidenceItem(evidence_text="Consistent across quarters"),
            ],
            confidence_level="high",
            evidence_level="B",
        )
        recommendation = "Should prioritize investment in North region"
        is_valid, error = validate_recommendation_language(recommendation, finding)
        assert is_valid
        assert error == ""

    def test_validation_language_with_medium_evidence_passes(self):
        """Validation language should pass with medium evidence."""
        finding = Finding(
            finding_id="F006",
            statement="South region shows growth potential",
            evidence=[EvidenceItem(evidence_text="Growth trend observed")],
            confidence_level="medium",
            evidence_level="B",
        )
        recommendation = "建议进一步验证South region的增长潜力"
        is_valid, error = validate_recommendation_language(recommendation, finding)
        assert is_valid
        assert error == ""

    def test_validate_recommendations_against_findings(self):
        """Test report-level recommendation validation."""
        report = """
## Summary
Some analysis

## Recommendations
- Should immediately increase prices in North region
- Must prioritize South region investment
"""
        findings = [
            Finding(
                finding_id="F001",
                statement="Revenue is $200K",
                evidence=[EvidenceItem(evidence_text="Calculated")],
                confidence_level="low",
                evidence_level="A",
            )
        ]
        errors = validate_recommendations_against_findings(report, findings)
        assert len(errors) > 0
        assert "strong action recommendations" in errors[0].lower()

    def test_no_recommendations_passes(self):
        """Report without recommendations should pass."""
        report = """
## Summary
Some analysis

## Key Findings
- Finding 1
- Finding 2
"""
        findings = [
            Finding(
                finding_id="F001",
                statement="Revenue is $200K",
                evidence=[EvidenceItem(evidence_text="Calculated")],
                confidence_level="low",
                evidence_level="A",
            )
        ]
        errors = validate_recommendations_against_findings(report, findings)
        assert len(errors) == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

"""Tests for evidence binding validation."""

import pytest

from langgraph_langchain.evidence_binding import (
    get_evidence_summary,
    validate_evidence_binding,
    validate_evidence_completeness,
)
from langgraph_langchain.schemas import EvidenceItem, Finding


class TestEvidenceBindingValidation:
    """Test evidence binding validation rules."""

    def test_finding_without_evidence_fails(self):
        """Finding with no evidence should fail validation."""
        finding = Finding(
            finding_id="F001",
            statement="Revenue increased",
            evidence=[],
        )
        is_valid, errors = validate_evidence_binding(finding, {})
        assert not is_valid
        assert len(errors) == 1
        assert "no evidence" in errors[0].lower()

    def test_finding_with_empty_evidence_text_fails(self):
        """Evidence with empty text should fail validation."""
        import pytest
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            Finding(
                finding_id="F001",
                statement="Revenue increased",
                evidence=[
                    EvidenceItem(evidence_text=""),
                ],
            )

    def test_finding_with_valid_evidence_passes(self):
        """Finding with proper evidence should pass validation."""
        finding = Finding(
            finding_id="F001",
            statement="Revenue increased by 20%",
            evidence=[
                EvidenceItem(
                    evidence_text="Q3 revenue was $1.2M compared to Q2 revenue of $1.0M",
                    source_fields=["revenue", "quarter"],
                    stats={"q3_revenue": 1200000, "q2_revenue": 1000000},
                ),
            ],
        )
        is_valid, errors = validate_evidence_binding(finding, {})
        assert is_valid
        assert len(errors) == 0

    def test_evidence_referencing_unknown_artifact_fails(self):
        """Evidence referencing non-existent artifact should fail."""
        finding = Finding(
            finding_id="F001",
            statement="Revenue trend shows growth",
            evidence=[
                EvidenceItem(
                    evidence_text="See revenue chart",
                    source_artifacts=["revenue_chart.png"],
                ),
            ],
        )
        available_artifacts = {}  # Empty - artifact doesn't exist
        is_valid, errors = validate_evidence_binding(finding, available_artifacts)
        assert not is_valid
        assert any("unknown artifact" in e.lower() for e in errors)

    def test_evidence_referencing_valid_artifact_passes(self):
        """Evidence referencing existing artifact should pass."""
        from langgraph_langchain.schemas import ArtifactRef

        finding = Finding(
            finding_id="F001",
            statement="Revenue trend shows growth",
            evidence=[
                EvidenceItem(
                    evidence_text="Revenue increased from $1M to $1.2M as shown in chart",
                    source_artifacts=["revenue_chart.png"],
                ),
            ],
        )
        available_artifacts = {
            "revenue_chart.png": ArtifactRef(
                artifact_id="revenue_chart.png",
                artifact_type="chart",
                file_path="/workspace/revenue_chart.png",
            )
        }
        is_valid, errors = validate_evidence_binding(finding, available_artifacts)
        assert is_valid
        assert len(errors) == 0

    def test_high_confidence_finding_requires_strong_evidence(self):
        """High confidence finding should have multiple evidence items or detailed stats."""
        # Weak evidence for high confidence
        finding = Finding(
            finding_id="F001",
            statement="Revenue will increase",
            confidence_level="high",
            evidence=[
                EvidenceItem(evidence_text="Revenue looks good"),
            ],
        )
        is_valid, errors = validate_evidence_binding(finding, {})
        assert not is_valid
        assert any("high confidence but weak evidence" in e.lower() for e in errors)

    def test_high_confidence_with_multiple_evidence_passes(self):
        """High confidence with multiple evidence items should pass."""
        finding = Finding(
            finding_id="F001",
            statement="Revenue increased significantly",
            confidence_level="high",
            evidence=[
                EvidenceItem(evidence_text="Q3 revenue was $1.2M"),
                EvidenceItem(evidence_text="Q2 revenue was $1.0M"),
            ],
        )
        is_valid, errors = validate_evidence_binding(finding, {})
        assert is_valid

    def test_high_confidence_with_detailed_stats_passes(self):
        """High confidence with detailed stats should pass."""
        finding = Finding(
            finding_id="F001",
            statement="Revenue increased significantly",
            confidence_level="high",
            evidence=[
                EvidenceItem(
                    evidence_text="Revenue increased by 20%",
                    stats={"q3_revenue": 1200000, "q2_revenue": 1000000, "growth_rate": 0.2},
                ),
            ],
        )
        is_valid, errors = validate_evidence_binding(finding, {})
        assert is_valid

    def test_causal_claim_requires_strong_evidence(self):
        """Causal claim (level C) should have calculation method or detailed stats."""
        finding = Finding(
            finding_id="F001",
            statement="Price increase caused revenue drop",
            evidence_level="C",
            evidence=[
                EvidenceItem(evidence_text="Revenue dropped after price increase"),
            ],
        )
        is_valid, errors = validate_evidence_binding(finding, {})
        assert not is_valid
        assert any("causal claim" in e.lower() for e in errors)

    def test_causal_claim_with_calculation_method_passes(self):
        """Causal claim with calculation method should pass."""
        finding = Finding(
            finding_id="F001",
            statement="Price increase caused revenue drop",
            evidence_level="C",
            evidence=[
                EvidenceItem(
                    evidence_text="Revenue dropped 15% after 10% price increase",
                    calculation_method="Compared revenue 30 days before/after price change, controlled for seasonality",
                ),
            ],
        )
        is_valid, errors = validate_evidence_binding(finding, {})
        assert is_valid

    def test_causal_claim_with_detailed_stats_passes(self):
        """Causal claim with detailed stats should pass."""
        finding = Finding(
            finding_id="F001",
            statement="Price increase caused revenue drop",
            evidence_level="C",
            evidence=[
                EvidenceItem(
                    evidence_text="Revenue dropped after price increase",
                    stats={
                        "before_revenue": 1000000,
                        "after_revenue": 850000,
                        "price_increase": 0.1,
                        "revenue_drop": 0.15,
                    },
                ),
            ],
        )
        is_valid, errors = validate_evidence_binding(finding, {})
        assert is_valid


class TestEvidenceCompletenessValidation:
    """Test evidence completeness checks (warnings)."""

    def test_evidence_without_source_fields_warns(self):
        """Evidence without source_fields should generate warning."""
        finding = Finding(
            finding_id="F001",
            statement="Revenue increased",
            evidence=[
                EvidenceItem(evidence_text="Revenue went up"),
            ],
        )
        is_complete, warnings = validate_evidence_completeness(finding, {})
        assert not is_complete
        assert any("source_fields" in w.lower() for w in warnings)

    def test_quantitative_finding_without_stats_warns(self):
        """Quantitative finding without stats should generate warning."""
        finding = Finding(
            finding_id="F001",
            statement="Revenue increased by 20%",
            evidence=[
                EvidenceItem(
                    evidence_text="Revenue increased significantly",
                    source_fields=["revenue"],
                ),
            ],
        )
        is_complete, warnings = validate_evidence_completeness(finding, {})
        assert not is_complete
        assert any("quantitative" in w.lower() and "stats" in w.lower() for w in warnings)

    def test_time_based_finding_without_time_window_warns(self):
        """Time-based finding without time_window should generate warning."""
        finding = Finding(
            finding_id="F001",
            statement="Revenue trend shows growth over time",
            evidence=[
                EvidenceItem(
                    evidence_text="Revenue has been growing",
                    source_fields=["revenue", "date"],
                ),
            ],
        )
        is_complete, warnings = validate_evidence_completeness(finding, {})
        assert not is_complete
        assert any("time-based" in w.lower() and "time_window" in w.lower() for w in warnings)

    def test_grouped_finding_without_group_dimension_warns(self):
        """Grouped finding without group_dimension should generate warning."""
        finding = Finding(
            finding_id="F001",
            statement="Revenue varies by region",
            evidence=[
                EvidenceItem(
                    evidence_text="Different regions have different revenue",
                    source_fields=["revenue", "region"],
                ),
            ],
        )
        is_complete, warnings = validate_evidence_completeness(finding, {})
        assert not is_complete
        assert any("grouping" in w.lower() and "group_dimension" in w.lower() for w in warnings)

    def test_complete_evidence_passes(self):
        """Well-structured evidence should pass completeness check."""
        finding = Finding(
            finding_id="F001",
            statement="North region revenue increased by 20% in Q3",
            evidence=[
                EvidenceItem(
                    evidence_text="North region Q3 revenue was $1.2M vs Q2 $1.0M",
                    source_fields=["revenue", "region", "quarter"],
                    stats={"q3_revenue": 1200000, "q2_revenue": 1000000},
                    time_window="2025-Q3",
                    group_dimension="region",
                ),
            ],
        )
        is_complete, warnings = validate_evidence_completeness(finding, {})
        assert is_complete
        assert len(warnings) == 0


class TestEvidenceSummary:
    """Test evidence summary generation."""

    def test_get_evidence_summary_basic(self):
        """Should generate readable summary of evidence."""
        finding = Finding(
            finding_id="F001",
            statement="Revenue increased",
            confidence_level="high",
            evidence_level="B",
            evidence=[
                EvidenceItem(
                    evidence_text="Q3 revenue was $1.2M compared to Q2 revenue of $1.0M",
                    source_fields=["revenue", "quarter"],
                    stats={"q3_revenue": 1200000, "q2_revenue": 1000000},
                ),
            ],
        )
        summary = get_evidence_summary(finding)
        assert "F001" in summary
        assert "Revenue increased" in summary
        assert "high" in summary
        assert "Level: B" in summary
        assert "Q3 revenue" in summary
        assert "revenue, quarter" in summary

    def test_get_evidence_summary_with_artifacts(self):
        """Should include artifact references in summary."""
        finding = Finding(
            finding_id="F001",
            statement="Revenue trend",
            evidence=[
                EvidenceItem(
                    evidence_text="Revenue shows upward trend",
                    source_artifacts=["revenue_chart.png", "trend_analysis.csv"],
                ),
            ],
        )
        summary = get_evidence_summary(finding)
        assert "revenue_chart.png" in summary
        assert "trend_analysis.csv" in summary

    def test_get_evidence_summary_with_time_and_group(self):
        """Should include time window and group dimension in summary."""
        finding = Finding(
            finding_id="F001",
            statement="Regional revenue analysis",
            evidence=[
                EvidenceItem(
                    evidence_text="North region leads in Q3",
                    time_window="2025-Q3",
                    group_dimension="region",
                ),
            ],
        )
        summary = get_evidence_summary(finding)
        assert "2025-Q3" in summary
        assert "region" in summary

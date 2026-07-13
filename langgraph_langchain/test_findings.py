#!/usr/bin/env python
# coding=utf-8
"""
Test findings collection mechanism.
"""
import pytest
from langgraph_langchain.schemas import (
    AnalysisAssumption,
    EvidenceItem,
    Finding,
    MetricDefinition,
)


def test_finding_structure():
    """Test that Finding structure is correctly defined."""
    evidence = EvidenceItem(
        evidence_text="North region revenue is 420,000, accounting for 42% of total",
        source_fields=["region", "revenue"],
        source_artifacts=["revenue_by_region.png"],
        time_window="2025-Q3",
        group_dimension="region",
        filters=["revenue > 0"],
    )

    finding = Finding(
        statement="North region accounts for 42% of Q3 revenue",
        evidence=[evidence],
        confidence_level="high",
        evidence_level="A",
        hypothesis_flag=False,
    )

    assert finding.statement == "North region accounts for 42% of Q3 revenue"
    assert len(finding.evidence) == 1
    assert finding.evidence[0].source_fields == ["region", "revenue"]
    assert finding.confidence_level == "high"
    assert finding.evidence_level == "A"
    assert finding.hypothesis_flag is False


def test_metric_definition_structure():
    """Test that MetricDefinition structure is correctly defined."""
    metric = MetricDefinition(
        metric_name="revenue",
        definition_text="Sum of order_amount for completed orders",
        time_window="2025-Q3",
        dedup_rule="Deduplicated by order_id",
        denominator=None,
        semantic_uncertainty="Assuming 'status=completed' means paid orders",
    )

    assert metric.metric_name == "revenue"
    assert metric.definition_text == "Sum of order_amount for completed orders"
    assert metric.time_window == "2025-Q3"
    assert metric.dedup_rule == "Deduplicated by order_id"
    assert metric.semantic_uncertainty == "Assuming 'status=completed' means paid orders"


def test_assumption_structure():
    """Test that AnalysisAssumption structure is correctly defined."""
    assumption = AnalysisAssumption(
        assumption_text="Assuming 'region' field has no missing values",
        risk_level="low",
    )

    assert assumption.assumption_text == "Assuming 'region' field has no missing values"
    assert assumption.risk_level == "low"


def test_finding_serialization():
    """Test that Finding can be serialized to dict."""
    evidence = EvidenceItem(
        evidence_text="Test evidence",
        source_fields=["field1"],
    )

    finding = Finding(
        statement="Test statement",
        evidence=[evidence],
        confidence_level="medium",
    )

    data = finding.model_dump()
    assert data["statement"] == "Test statement"
    assert len(data["evidence"]) == 1
    assert data["confidence_level"] == "medium"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

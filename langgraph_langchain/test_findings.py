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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

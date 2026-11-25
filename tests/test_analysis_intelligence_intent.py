"""P2 intent understanding golden cases (semantic correctness, not JSON shape)."""
from __future__ import annotations

import pytest

from langgraph_langchain.analysis_intelligence.intent import (
    AnalysisType,
    IntentResolver,
    IntentStatus,
)
from langgraph_langchain.analysis_intelligence.semantic_contracts import (
    DimensionContract,
    DimensionRelationship,
    MetricContract,
    MetricKind,
    RelationshipType,
    SemanticRegistry,
    TrustLevel,
    ValidationStatus,
)


@pytest.fixture()
def resolver() -> IntentResolver:
    registry = SemanticRegistry()
    registry.register_metric(MetricContract(
        metric_id="monthly_revenue", name="monthly_revenue", aliases=["收入", "revenue"],
        metric_kind=MetricKind.BASIC, aggregation="sum", required_columns=["month", "amount"],
        compatible_dimensions=["department", "application", "person"],
        supported_granularity=["monthly"],
        source=TrustLevel.TRUSTED_DEFINITION, confidence=1.0,
    ))
    registry.register_metric(MetricContract(
        metric_id="order_count", name="order_count", aliases=["订单数", "order_count"],
        metric_kind=MetricKind.COUNT, aggregation="count", required_columns=["order_id"],
        compatible_dimensions=["department", "application", "person"],
        supported_granularity=["monthly", "daily"],
        source=TrustLevel.TRUSTED_DEFINITION, confidence=1.0,
    ))
    registry.register_metric(MetricContract(
        metric_id="conversion_rate", name="conversion_rate", aliases=["转化率"],
        metric_kind=MetricKind.RATIO, numerator="converted", denominator="total",
        aggregation="ratio", required_columns=["converted", "total"],
        source=TrustLevel.EXISTING_SKILL, confidence=0.9,
        validation_rules=["no-direct-average"],
    ))
    for dim_id, name, source, status, conf in [
        ("department", "department", "builtin", ValidationStatus.VERIFIED, 1.0),
        ("application", "application", "dataset_defined", ValidationStatus.DECLARED, 0.7),
        ("person", "person", "dataset_defined", ValidationStatus.DECLARED, 0.7),
    ]:
        registry.register_dimension(DimensionContract(
            dimension_id=dim_id, name=name, aliases=[{"department": "部门", "application": "应用", "person": "人员"}.get(dim_id, name)],
            source=source, source_column=dim_id, data_asset_id="asset_sales",
            confidence=conf, validation_status=status,
        ))
    registry.register_relationship(DimensionRelationship(
        relation_id="dept_to_app", parent_dimension="department", child_dimension="application",
        relationship_type=RelationshipType.PARENT_CHILD, mapping_key="department",
        validation_status=ValidationStatus.VERIFIED,
    ))
    return IntentResolver(registry, known_columns=["department", "application", "person", "month", "amount"])


def test_single_metric_is_ready_without_over_planning(resolver):
    intent = resolver.resolve("上个月的收入是多少？", intent_id="i1")
    assert intent.analysis_type is AnalysisType.AGGREGATION
    assert [m.entity_id for m in intent.metrics] == ["monthly_revenue"]
    assert intent.metrics[0].resolution_method == "alias"
    assert intent.metrics[0].provenance.value in {"resolved", "explicit"}
    assert intent.status is IntentStatus.READY


def test_multi_metric_resolution(resolver):
    intent = resolver.resolve("收入和订单数分别是多少？", intent_id="i2")
    assert [m.entity_id for m in intent.metrics] == ["monthly_revenue", "order_count"]
    assert intent.status is IntentStatus.READY


def test_metric_with_dimension(resolver):
    intent = resolver.resolve("每个部门的收入是多少？", intent_id="i3")
    assert [m.entity_id for m in intent.metrics] == ["monthly_revenue"]
    assert [d.entity_id for d in intent.dimensions] == ["department"]
    assert intent.dimensions[0].provenance.value == "resolved"
    assert intent.status is IntentStatus.READY


def test_ranking_with_top_n_and_collection_scope(resolver):
    intent = resolver.resolve("每个部门收入排名前3名", intent_id="i4")
    assert intent.analysis_type is AnalysisType.RANKING
    assert intent.ranking is not None
    assert intent.ranking.top_n == 3
    assert intent.ranking.direction == "desc"
    assert intent.ranking.scope_dimension == "department"
    assert intent.status is IntentStatus.READY


def test_ranking_without_n_records_assumption(resolver):
    intent = resolver.resolve("部门收入排名", intent_id="i5")
    assert intent.analysis_type is AnalysisType.RANKING
    assert intent.ranking.top_n is None
    assert intent.status is IntentStatus.READY_WITH_ASSUMPTIONS
    assert intent.assumptions


def test_trend_requires_time_exploration(resolver):
    intent = resolver.resolve("收入趋势如何？", intent_id="i6")
    assert intent.analysis_type is AnalysisType.TREND
    assert intent.time is not None and intent.time.granularity is None
    assert intent.status is IntentStatus.NEEDS_EXPLORATION


def test_trend_with_explicit_granularity_ready(resolver):
    intent = resolver.resolve("收入逐月趋势", intent_id="i7")
    assert intent.time is not None and intent.time.granularity == "monthly"
    assert intent.status is IntentStatus.READY


def test_relative_time_is_explicit(resolver):
    intent = resolver.resolve("最近3个月的收入趋势", intent_id="i8")
    assert intent.time is not None
    assert intent.time.relative_expression == "最近3个月"
    assert intent.time.provenance.value == "explicit"


def test_drill_down_with_verified_relationship(resolver):
    intent = resolver.resolve("先按部门再按应用下钻分析收入", intent_id="i9")
    assert intent.analysis_type is AnalysisType.DRILL_DOWN
    assert intent.drill_down is not None
    assert (intent.drill_down.parent_dimension, intent.drill_down.child_dimension) == ("department", "application")
    assert intent.status is IntentStatus.READY


def test_unknown_metric_requires_clarification(resolver):
    intent = resolver.resolve("客户终身价值是多少？", intent_id="i10")
    assert not intent.metrics
    assert intent.status is IntentStatus.NEEDS_CLARIFICATION


def test_ambiguous_dimension_requires_clarification(resolver):
    intent = resolver.resolve("每个片区的收入是多少？", intent_id="i11")
    assert intent.metrics
    assert intent.ambiguity and any("片区" in a for a in intent.ambiguity)
    assert intent.status is IntentStatus.NEEDS_CLARIFICATION


def test_dimension_candidate_in_data_needs_exploration(resolver):
    # "区域" is not registered but the schema contains a region-like column
    r = IntentResolver(
        resolver._registry,
        known_columns=["department", "销售区域", "month", "amount"],
    )
    intent = r.resolve("每个销售区域的收入是多少？", intent_id="i12")
    candidates = [d for d in intent.dimensions if d.entity_id is None]
    assert candidates and candidates[0].resolution_method == "candidate_in_data"
    assert intent.status is IntentStatus.NEEDS_EXPLORATION


def test_invalid_metric_dimension_combination_unsupported(resolver):
    # conversion_rate has no declared compatible dimensions -> CONDITIONAL, not blocking.
    # Use a metric whose compatibility with the dimension is explicitly invalid:
    from langgraph_langchain.analysis_intelligence.semantic_contracts import MetricContract, TrustLevel
    r = resolver
    r._registry.register_metric(MetricContract(
        metric_id="headcount_ratio", name="headcount_ratio", aliases=["人均比"],
        metric_kind=MetricKind.RATIO, numerator="a", denominator="b",
        required_columns=["a", "b"], incompatible_dimensions=["person"],
        source=TrustLevel.TRUSTED_DEFINITION, confidence=1.0,
        validation_rules=["no-direct-average"],
    ))
    intent = r.resolve("每个人员的人均比是多少？", intent_id="i13")
    assert intent.status is IntentStatus.UNSUPPORTED
    assert any("invalid metric-dimension combination" in reason for reason in intent.status_reasons)


def test_unsupported_analysis_type_rejected(resolver):
    intent = resolver.resolve("预测下个季度的收入", intent_id="i14")
    assert intent.analysis_type is AnalysisType.FORECAST
    assert intent.status is IntentStatus.UNSUPPORTED


def test_filters_detected(resolver):
    intent = resolver.resolve("收入大于100的部门", intent_id="i15")
    assert "收入" in intent.filters and intent.filters["收入"] == ">100"


def test_collection_scope_constraint_recorded(resolver):
    intent = resolver.resolve("每个部门所有人员的订单数", intent_id="i16")
    assert any("collection-scope" in c for c in intent.constraints)
    assert intent.status is IntentStatus.READY


def test_multi_step_goal_drill_down_implies_multiple_steps(resolver):
    intent = resolver.resolve("先按部门再按应用下钻分析收入", intent_id="i17")
    assert intent.drill_down is not None
    assert intent.analysis_type is AnalysisType.DRILL_DOWN
    # multi-step: plan phase must decompose; intent itself marks the hierarchy request

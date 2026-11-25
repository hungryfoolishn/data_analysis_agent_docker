"""P1 semantic contract tests (Analysis Intelligence V1.0)."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from langgraph_langchain.analysis_intelligence.semantic_contracts import (
    CompatibilityStatus,
    CompatibilityValidator,
    DimensionContract,
    DimensionRelationship,
    MetricContract,
    MetricKind,
    RelationshipType,
    SemanticRegistry,
    TrustLevel,
    ValidationStatus,
    derive_dimension_candidates,
)


@pytest.fixture()
def registry() -> SemanticRegistry:
    registry = SemanticRegistry()
    registry.register_metric(MetricContract(
        metric_id="monthly_revenue",
        name="monthly_revenue",
        aliases=["revenue", "income"],
        metric_kind=MetricKind.BASIC,
        aggregation="sum",
        required_columns=["month", "amount"],
        compatible_dimensions=["department", "application", "person"],
        incompatible_dimensions=["order_id"],
        supported_granularity=["monthly"],
        source=TrustLevel.TRUSTED_DEFINITION,
        confidence=1.0,
    ))
    registry.register_metric(MetricContract(
        metric_id="conversion_rate",
        name="conversion_rate",
        metric_kind=MetricKind.RATIO,
        numerator="converted_users",
        denominator="total_users",
        aggregation="ratio",
        required_columns=["converted_users", "total_users"],
        source=TrustLevel.EXISTING_SKILL,
        confidence=0.9,
        validation_rules=["no-direct-average", "recompute-from-numerator-and-denominator"],
    ))
    # An inferred candidate: must never pass deterministic validation.
    registry.register_metric(MetricContract(
        metric_id="llm_invented_metric",
        name="llm_invented_metric",
        required_columns=["x"],
        source=TrustLevel.INFERRED_CANDIDATE,
        confidence=0.0,
    ))
    registry.register_dimension(DimensionContract(
        dimension_id="department",
        name="department",
        aliases=["dept", "bu"],
        source="builtin",
        source_column="department",
        data_asset_id="asset_sales",
        cardinality=8,
        confidence=1.0,
        validation_status=ValidationStatus.VERIFIED,
    ))
    registry.register_dimension(DimensionContract(
        dimension_id="application",
        name="application",
        source="dataset_defined",
        source_column="application",
        data_asset_id="asset_sales",
        confidence=0.7,
        validation_status=ValidationStatus.DECLARED,
    ))
    registry.register_dimension(DimensionContract(
        dimension_id="person",
        name="person",
        source="dataset_defined",
        source_column="person",
        data_asset_id="asset_sales",
        confidence=0.7,
        validation_status=ValidationStatus.DECLARED,
    ))
    registry.register_dimension(DimensionContract(
        dimension_id="order_id",
        name="order_id",
        semantic_type="identifier",
        source="dataset_defined",
        confidence=0.2,
    ))
    registry.register_relationship(DimensionRelationship(
        relation_id="dept_to_app",
        parent_dimension="department",
        child_dimension="application",
        relationship_type=RelationshipType.PARENT_CHILD,
        mapping_key="department",
        applicable_data_asset="asset_sales",
        validation_status=ValidationStatus.VERIFIED,
    ))
    registry.register_relationship(DimensionRelationship(
        relation_id="app_to_person",
        parent_dimension="application",
        child_dimension="person",
        mapping_key="application",
        validation_status=ValidationStatus.INFERRED,
    ))
    return registry


def test_builtin_and_dynamic_dimensions_are_distinguishable(registry):
    dept = registry.dimension("department")
    app = registry.dimension("application")
    assert dept.source == "builtin" and dept.validation_status is ValidationStatus.VERIFIED
    assert app.source == "dataset_defined"
    assert dept.source != app.source


def test_metric_alias_and_exact_resolution(registry):
    metric, method = registry.resolve_metric("Revenue")
    assert metric is not None and metric.metric_id == "monthly_revenue" and method == "alias"
    metric2, method2 = registry.resolve_metric("monthly_revenue")
    assert method2 == "exact"
    missing, method3 = registry.resolve_metric("nonexistent")
    assert missing is None and method3 == "unknown"


def test_inferred_metric_never_validates_deterministically(registry):
    validator = CompatibilityValidator(registry)
    report = validator.check("llm_invented_metric", "department")
    assert report.status is CompatibilityStatus.UNKNOWN
    assert "inferred" in report.reason


def test_unknown_metric_and_dimension_return_unknown_not_valid(registry):
    validator = CompatibilityValidator(registry)
    assert validator.check("nope", "department").status is CompatibilityStatus.UNKNOWN
    assert validator.check("monthly_revenue", "nonexistent_dim").status is CompatibilityStatus.UNKNOWN


def test_incompatible_dimension_is_invalid(registry):
    validator = CompatibilityValidator(registry)
    report = validator.check("monthly_revenue", "order_id")
    assert report.status is CompatibilityStatus.INVALID


def test_undeclared_dimension_is_conditional_not_silently_valid(registry):
    registry.register_dimension(DimensionContract(
        dimension_id="region", name="region", source="runtime_discovered", confidence=0.4,
    ))
    validator = CompatibilityValidator(registry)
    # Explicitly declared boundary: an outside dimension is INVALID.
    assert validator.check("monthly_revenue", "region").status is CompatibilityStatus.INVALID
    # conversion_rate declares no compatible_dimensions: fit must be validated first,
    # so the status is CONDITIONAL, never silently VALID.
    report = validator.check("conversion_rate", "region")
    assert report.status is CompatibilityStatus.CONDITIONAL
    assert report.preconditions


def test_dimension_asset_scope_mismatch_is_invalid(registry):
    validator = CompatibilityValidator(registry)
    report = validator.check("monthly_revenue", "department", data_asset_id="asset_other")
    assert report.status is CompatibilityStatus.INVALID


def test_granularity_mismatch_is_invalid(registry):
    validator = CompatibilityValidator(registry)
    report = validator.check("monthly_revenue", "department", granularity="daily")
    assert report.status is CompatibilityStatus.INVALID
    assert validator.check("monthly_revenue", "department", granularity="monthly").status is CompatibilityStatus.VALID


def test_verified_drill_down_is_allowed_and_plan_is_complete(registry):
    validator = CompatibilityValidator(registry)
    report = validator.check_drill_down("monthly_revenue", "department", "application")
    assert report.status is CompatibilityStatus.VALID
    plan = report.drill_down
    assert plan is not None
    assert plan.relationship_id == "dept_to_app"
    assert plan.relationship_status is ValidationStatus.VERIFIED
    assert plan.filter_propagation is True


def test_inferred_relationship_blocks_drill_down(registry):
    validator = CompatibilityValidator(registry)
    report = validator.check_drill_down("monthly_revenue", "application", "person")
    assert report.status is CompatibilityStatus.UNKNOWN
    assert "verification required" in report.reason
    assert report.drill_down is None


def test_missing_relationship_blocks_drill_down_no_fabrication(registry):
    validator = CompatibilityValidator(registry)
    report = validator.check_drill_down("monthly_revenue", "department", "person")
    assert report.status is CompatibilityStatus.UNKNOWN
    assert "never fabricate" in report.reason


def test_ratio_contract_requires_denominator_and_rules():
    with pytest.raises(ValidationError):
        MetricContract(metric_id="bad_ratio", name="bad_ratio", metric_kind=MetricKind.RATIO, required_columns=["a"])
    with pytest.raises(ValidationError):
        MetricContract(
            metric_id="no_rules", name="no_rules", metric_kind=MetricKind.RATIO,
            numerator="a", denominator="b", required_columns=["a", "b"],
            source=TrustLevel.EXISTING_SKILL, confidence=0.5,
        )
    ok = MetricContract(
        metric_id="good_ratio", name="good_ratio", metric_kind=MetricKind.RATIO,
        numerator="a", denominator="b", required_columns=["a", "b"],
        source=TrustLevel.EXISTING_SKILL, confidence=0.5,
        validation_rules=["no-direct-average"],
    )
    assert ok.metric_kind is MetricKind.RATIO


def test_dimension_candidate_discovery_is_deterministic_and_never_trusted():
    candidates = derive_dimension_candidates({
        "department": "object",
        "order_id": "object",
        "order_month": "datetime64[ns]",
        "amount": "float64",
    }, cardinalities={"department": 8, "order_id": 9000}, data_asset_id="asset_sales")
    by_name = {c.name: c for c in candidates}
    assert by_name["department"].semantic_type == "categorical"
    assert by_name["department"].confidence == pytest.approx(0.7)
    assert by_name["department"].validation_status is ValidationStatus.UNKNOWN
    assert by_name["order_id"].semantic_type == "identifier"
    assert "not a business dimension" in by_name["order_id"].constraints[0]
    assert by_name["order_month"].semantic_type == "temporal"
    assert by_name["amount"].semantic_type == "categorical"  # numeric: low-confidence candidate
    assert all(c.confidence < 1.0 for c in candidates)

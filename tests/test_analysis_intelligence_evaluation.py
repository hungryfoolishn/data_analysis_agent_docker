"""P6 intelligence evaluation suite (golden + negative + boundary + dynamic)."""
from __future__ import annotations

import pandas as pd
import pytest

from langgraph_langchain.analysis_intelligence.evaluation import (
    CaseKind,
    ExpectedRejection,
    IntelligenceCase,
    IntelligenceEvaluator,
)
from langgraph_langchain.analysis_intelligence.intent import AnalysisType
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


def _registry() -> SemanticRegistry:
    registry = SemanticRegistry()
    registry.register_metric(MetricContract(
        metric_id="monthly_revenue", name="monthly_revenue", aliases=["收入"],
        metric_kind=MetricKind.BASIC, aggregation="sum", required_columns=["month", "amount"],
        # region is semantically compatible (domain fact); whether a region column
        # exists in the data is exactly what dynamic exploration must prove.
        compatible_dimensions=["department", "application", "person", "region"],
        supported_granularity=["monthly"],
        source=TrustLevel.TRUSTED_DEFINITION, confidence=1.0,
    ))
    registry.register_metric(MetricContract(
        metric_id="order_count", name="order_count", aliases=["订单数"],
        metric_kind=MetricKind.COUNT, aggregation="count", required_columns=["order_id"],
        compatible_dimensions=["department", "application", "person"],
        supported_granularity=["monthly", "daily"],
        source=TrustLevel.TRUSTED_DEFINITION, confidence=1.0,
    ))
    for dim_id, name, alias, status in [
        ("department", "department", "部门", ValidationStatus.VERIFIED),
        ("application", "application", "应用", ValidationStatus.DECLARED),
        ("person", "person", "人员", ValidationStatus.DECLARED),
    ]:
        registry.register_dimension(DimensionContract(
            dimension_id=dim_id, name=name, aliases=[alias], source="dataset_defined",
            source_column=dim_id, data_asset_id="asset_sales",
            confidence=1.0 if status is ValidationStatus.VERIFIED else 0.7,
            validation_status=status,
        ))
    registry.register_relationship(DimensionRelationship(
        relation_id="dept_to_app", parent_dimension="department", child_dimension="application",
        relationship_type=RelationshipType.PARENT_CHILD, mapping_key="department",
        validation_status=ValidationStatus.VERIFIED,
    ))
    return registry


def _frame() -> pd.DataFrame:
    return pd.DataFrame({
        "department": ["dept_a", "dept_a", "dept_b", "dept_b", "dept_a", "dept_b"],
        "application": ["app1", "app2", "app1", "app2", "app1", "app2"],
        "person": ["p1", "p2", "p3", "p4", "p5", "p6"],
        "region": ["east", "east", "west", "west", "east", "west"],
        "month": ["2026-01", "2026-01", "2026-01", "2026-02", "2026-02", "2026-02"],
        "order_id": ["o1", "o2", "o3", "o4", "o5", "o6"],
        "amount": [100.0, 200.0, None, 400.0, 50.0, 250.0],
    })


def _case(case_id, kind, question, **kwargs) -> IntelligenceCase:
    return IntelligenceCase(
        case_id=case_id, kind=kind, question=question,
        registry_factory=_registry, frame_factory=_frame, **kwargs
    )


CASES = [
    # --- positive: real computation against ground truth ---
    _case(
        "pos_group_agg", CaseKind.POSITIVE, "每个部门的收入是多少？",
        expected_metrics=["monthly_revenue"], expected_dimensions=["department"],
        expected_analysis_type=AnalysisType.AGGREGATION,
        expected_group_by="department", expected_value_column="amount",
        ground_truth={"dept_a": 350.0, "dept_b": 650.0},  # null treated as excluded by sum
    ),
    _case(
        "pos_ranking_topn", CaseKind.POSITIVE, "部门订单数排名前3名",
        expected_metrics=["order_count"], expected_dimensions=["department"],
        expected_analysis_type=AnalysisType.RANKING,
        expected_group_by="department",
        ground_truth={"dept_a": 3.0, "dept_b": 3.0},
    ),
    _case(
        "pos_trend", CaseKind.POSITIVE, "收入逐月趋势",
        expected_metrics=["monthly_revenue"], expected_analysis_type=AnalysisType.TREND,
        ground_truth={},
    ),
    _case(
        "pos_drilldown", CaseKind.MULTI_STEP, "先按部门再按应用下钻分析收入",
        expected_metrics=["monthly_revenue"], expected_analysis_type=AnalysisType.DRILL_DOWN,
        expected_group_by="application", ground_truth={},
    ),
    # --- dynamic: dimension discovered via exploration ---
    _case(
        "pos_dynamic_dimension", CaseKind.DYNAMIC, "每个region的收入是多少？",
        expected_metrics=["monthly_revenue"], expected_dimensions=["region"],
        expected_group_by="region", ground_truth={"east": 350.0, "west": 650.0},
    ),
    # --- negative: must be rejected at the right stage ---
    _case(
        "neg_unknown_metric", CaseKind.NEGATIVE, "客户终身价值是多少？",
        expected_rejection=ExpectedRejection(stage="intent", reason_contains="no metric"),
    ),
    _case(
        "neg_ambiguous_dimension", CaseKind.NEGATIVE, "每个片区的收入是多少？",
        expected_rejection=ExpectedRejection(stage="intent", reason_contains="unresolved dimension"),
    ),
    _case(
        "neg_unsupported_type", CaseKind.NEGATIVE, "预测下个季度的收入",
        expected_analysis_type=AnalysisType.FORECAST,
        expected_rejection=ExpectedRejection(stage="intent", reason_contains="not supported"),
    ),
    _case(
        "neg_unregistered_dimension", CaseKind.NEGATIVE, "每个zone的收入是多少？",
        expected_rejection=ExpectedRejection(stage="intent", reason_contains="unresolved dimension"),
    ),
    # --- boundary: null handling ---
    _case(
        "boundary_null_excluded_from_sum", CaseKind.BOUNDARY, "每个部门的收入是多少？",
        expected_metrics=["monthly_revenue"], expected_dimensions=["department"],
        expected_group_by="department", ground_truth={"dept_a": 350.0},
    ),
]


def test_evaluation_suite_runs_and_positive_cases_compute_correctly():
    report = IntelligenceEvaluator().run_suite(CASES)
    assert report.total_cases == len(CASES)
    pos = [r for r in report.results if r.case_id == "pos_group_agg"]
    assert pos and pos[0].passed
    assert pos[0].computed == {"dept_a": 350.0, "dept_b": 650.0}
    assert pos[0].plan_operations == ["inspect", "group_aggregate"]
    assert pos[0].executed


def test_null_handling_boundary_case():
    report = IntelligenceEvaluator().run_suite(CASES)
    boundary = [r for r in report.results if r.case_id == "boundary_null_excluded_from_sum"]
    assert boundary and boundary[0].passed
    # null amount row excluded from dept_b sum (documented deterministic rule)
    assert boundary[0].computed.get("dept_a") == 350.0


def test_negative_cases_rejected_at_correct_stage():
    report = IntelligenceEvaluator().run_suite(CASES)
    for case_id in ["neg_unknown_metric", "neg_ambiguous_dimension", "neg_unsupported_type", "neg_unregistered_dimension"]:
        result = next(r for r in report.results if r.case_id == case_id)
        assert result.passed, (case_id, result.failures, result.critical_gates)
        assert not result.executed


def test_wrong_number_claim_is_detected_by_evaluator():
    """Execution-success vs analysis-correctness separation."""
    report = IntelligenceEvaluator().run_suite(CASES)
    result = next(r for r in report.results if r.case_id == "pos_group_agg")
    assert result.critical_gates.get("wrong_analysis_detected") is True


def test_ranking_case_plan_shape():
    report = IntelligenceEvaluator().run_suite(CASES)
    result = next(r for r in report.results if r.case_id == "pos_ranking_topn")
    assert result.plan_operations == ["inspect", "group_aggregate", "sort"]


def test_drilldown_case_chain():
    report = IntelligenceEvaluator().run_suite(CASES)
    result = next(r for r in report.results if r.case_id == "pos_drilldown")
    assert result.plan_operations == ["inspect", "group_aggregate"]  # hierarchical [department, application]


def test_dynamic_dimension_case_resolves_via_exploration():
    report = IntelligenceEvaluator().run_suite(CASES)
    result = next(r for r in report.results if r.case_id == "pos_dynamic_dimension")
    assert result.passed, (result.failures, result.critical_gates)
    assert result.intent_status == "ready"
    assert result.plan_operations == ["inspect", "group_aggregate"]


def test_suite_summary_counts():
    report = IntelligenceEvaluator().run_suite(CASES)
    assert report.passed_cases + report.failed_cases == report.total_cases
    assert report.average_score >= 0.0

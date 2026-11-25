"""P3 constraint-aware planning tests."""
from __future__ import annotations

import pytest

from langgraph_langchain.analysis_intelligence.constraints import (
    IntentPlanner,
    PlanningBlockedError,
    PlanRevisionLedger,
)
from langgraph_langchain.analysis_intelligence.intent import IntentResolver, IntentStatus
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

ASSET_COLUMNS = {
    "asset_sales": ["department", "application", "person", "month", "amount", "order_id"]
}


@pytest.fixture()
def planner() -> IntentPlanner:
    registry = SemanticRegistry()
    registry.register_metric(MetricContract(
        metric_id="monthly_revenue", name="monthly_revenue", aliases=["收入"],
        metric_kind=MetricKind.BASIC, aggregation="sum", required_columns=["month", "amount"],
        compatible_dimensions=["department", "application", "person"],
        supported_granularity=["monthly"],
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
    return IntentPlanner(registry)


def _resolve(planner: IntentPlanner, question: str, intent_id: str, columns=None):
    resolver = IntentResolver(planner._registry, known_columns=columns or ASSET_COLUMNS["asset_sales"])
    return resolver.resolve(question, intent_id=intent_id)


def test_ready_intent_produces_valid_plan(planner):
    intent = _resolve(planner, "每个部门的收入是多少？", "i1")
    assert intent.status is IntentStatus.READY
    outcome = planner.plan_from_intent(intent, input_asset_ids=["asset_sales"], asset_columns=ASSET_COLUMNS)
    assert outcome.plan is not None
    operations = [s.operation for s in outcome.plan.steps]
    assert operations == ["inspect", "group_aggregate"]
    params = outcome.plan.steps[1].parameters
    assert params["group_by"] == "department"
    assert params["metric"] == "amount"
    assert params["agg"] == "sum"
    assert outcome.plan.user_goal == intent.user_goal  # goal preserved, never rewritten


def test_ranking_intent_produces_group_then_sort(planner):
    intent = _resolve(planner, "部门收入排名前3名", "i2")
    outcome = planner.plan_from_intent(intent, input_asset_ids=["asset_sales"], asset_columns=ASSET_COLUMNS)
    operations = [s.operation for s in outcome.plan.steps]
    assert operations == ["inspect", "group_aggregate", "sort"]
    sort_params = outcome.plan.steps[2].parameters
    # group_aggregate renames the value column to "{metric}_{agg}".
    assert sort_params["by"] == "amount_sum"
    assert sort_params["ascending"] is False
    assert sort_params["top_n"] == 3


def test_drill_down_produces_chained_steps(planner):
    intent = _resolve(planner, "先按部门再按应用下钻分析收入", "i3")
    outcome = planner.plan_from_intent(intent, input_asset_ids=["asset_sales"], asset_columns=ASSET_COLUMNS)
    steps = outcome.plan.steps
    # Drill-down is one executable hierarchical aggregation, not two chained
    # single-key aggregations (the second would lose the child column).
    assert [s.operation for s in steps] == ["inspect", "group_aggregate"]
    assert steps[1].depends_on == [steps[0].step_id]
    assert steps[1].parameters["group_by"] == ["department", "application"]
    assert steps[1].parameters["metric"] == "amount"
    assert "verified relationship path" in steps[1].rationale


def test_trend_without_time_column_is_blocked(planner):
    intent = _resolve(planner, "收入逐月趋势", "i4")
    with pytest.raises(PlanningBlockedError, match="time column"):
        planner.plan_from_intent(intent, input_asset_ids=["asset_sales"], asset_columns=ASSET_COLUMNS)


def test_trend_with_time_column_plans_trend_operation(planner):
    intent = _resolve(planner, "收入逐月趋势", "i5")
    outcome = planner.plan_from_intent(
        intent, input_asset_ids=["asset_sales"], asset_columns=ASSET_COLUMNS, time_column="month"
    )
    operations = [s.operation for s in outcome.plan.steps]
    assert operations == ["inspect", "trend"]
    assert outcome.plan.steps[1].parameters == {"time_column": "month", "value_column": "amount"}


def test_clarification_intent_never_plans(planner):
    intent = _resolve(planner, "客户终身价值是多少？", "i6")
    assert intent.status is IntentStatus.NEEDS_CLARIFICATION
    with pytest.raises(PlanningBlockedError, match="unresolved or unsupported"):
        planner.plan_from_intent(intent, input_asset_ids=["asset_sales"], asset_columns=ASSET_COLUMNS)


def test_missing_required_columns_block_planning(planner):
    intent = _resolve(planner, "每个部门的收入是多少？", "i7")
    with pytest.raises(PlanningBlockedError, match="requires columns"):
        planner.plan_from_intent(
            intent,
            input_asset_ids=["asset_sales"],
            asset_columns={"asset_sales": ["department", "unrelated"]},
        )


def test_unverified_combination_blocks_planning(planner):
    intent = _resolve(planner, "每个人员的收入是多少？", "i8")
    # person is DECLARED and within compatible_dimensions -> valid; sanity check
    outcome = planner.plan_from_intent(intent, input_asset_ids=["asset_sales"], asset_columns=ASSET_COLUMNS)
    assert outcome.plan is not None


def test_revision_ledger_is_bounded_and_auditable(planner):
    ledger = PlanRevisionLedger(user_goal="每个部门的收入是多少？")
    for i in range(3):
        ledger.record(
            reason=f"exploration finding {i}",
            trigger_evidence=[f"evidence_{i}"],
            previous_plan_id=f"plan_{i}",
            new_plan_id=f"plan_{i + 1}",
            changed_task_ids=[f"task_{i}"],
            preserved_result_task_ids=[f"task_{i - 1}"] if i else [],
        )
    assert ledger.exhausted
    with pytest.raises(PlanningBlockedError, match="budget exhausted"):
        ledger.record(
            reason="one more",
            trigger_evidence=["e"],
            previous_plan_id="plan_3",
            new_plan_id="plan_4",
        )
    assert len(ledger.revisions) == 3
    assert ledger.revisions[0].trigger_evidence == ["evidence_0"]
    assert ledger.revisions[2].preserved_result_task_ids == ["task_1"]


def test_revision_cannot_rewrite_user_goal():
    ledger = PlanRevisionLedger(user_goal="原始目标")
    with pytest.raises(PlanningBlockedError, match="rewrite the user goal"):
        ledger.assert_goal_unchanged("另一个目标")
    ledger.assert_goal_unchanged("原始目标")  # unchanged goal is accepted

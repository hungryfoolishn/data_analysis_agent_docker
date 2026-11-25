"""P4 dynamic exploration & feedback tests (divergent paths, bounded, tracked)."""
from __future__ import annotations

import pytest

from langgraph_langchain.analysis_intelligence.constraints import IntentPlanner
from langgraph_langchain.analysis_intelligence.exploration import (
    ExplorationDecision,
    ExplorationDecider,
    ExplorationLedger,
    ExplorationQuestion,
    SchemaExplorationEngine,
    SchemaProfile,
)
from langgraph_langchain.analysis_intelligence.intent import IntentResolver, IntentStatus
from langgraph_langchain.analysis_intelligence.semantic_contracts import (
    DimensionContract,
    MetricContract,
    MetricKind,
    SemanticRegistry,
    TrustLevel,
    ValidationStatus,
)


@pytest.fixture()
def registry() -> SemanticRegistry:
    registry = SemanticRegistry()
    registry.register_metric(MetricContract(
        metric_id="monthly_revenue", name="monthly_revenue", aliases=["收入"],
        metric_kind=MetricKind.BASIC, aggregation="sum", required_columns=["month", "amount"],
        compatible_dimensions=["department", "application", "person"],
        supported_granularity=["monthly"],
        source=TrustLevel.TRUSTED_DEFINITION, confidence=1.0,
    ))
    registry.register_dimension(DimensionContract(
        dimension_id="department", name="department", aliases=["部门"],
        source="builtin", source_column="department", data_asset_id="asset_sales",
        confidence=1.0, validation_status=ValidationStatus.VERIFIED,
    ))
    # A metric with NO declared compatibility contract: fit must be validated
    # through exploration preconditions, not by an explicit allowlist.
    registry.register_metric(MetricContract(
        metric_id="transaction_value", name="transaction_value", aliases=["value"],
        metric_kind=MetricKind.BASIC, aggregation="sum", required_columns=["amount"],
        source=TrustLevel.TRUSTED_DEFINITION, confidence=1.0,
    ))
    return registry


ASSET_COLUMNS = {"asset_sales": ["department", "region", "month", "amount"]}


def _intent(registry, question, columns):
    resolver = IntentResolver(registry, known_columns=columns)
    return resolver.resolve(question, intent_id="probe")


def test_dimension_candidate_explored_and_folded_back(registry):
    profile = SchemaProfile(
        data_asset_id="asset_sales",
        columns={"department": "object", "region": "object", "month": "datetime64[ns]", "amount": "float64"},
        cardinalities={"department": 8, "region": 12},
    )
    intent = _intent(registry, "每个region的value是多少？", ASSET_COLUMNS["asset_sales"])
    assert intent.status is IntentStatus.NEEDS_EXPLORATION

    decider = ExplorationDecider(registry)
    question = decider.next_question(intent, data_asset_id="asset_sales")
    assert question is not None and question.target == "dimension_candidates"

    engine = SchemaExplorationEngine()
    result = engine.explore(question, profile, registry)
    assert result.decision is ExplorationDecision.VALIDATE_CANDIDATE
    assert result.dimension_candidates

    ledger = ExplorationLedger()
    ledger.record(result)
    decision = decider.apply_result(intent, result, data_asset_id="asset_sales")
    assert decision is ExplorationDecision.VALIDATE_CANDIDATE
    # Real state change: dimension resolved, intent now READY
    assert intent.dimensions[0].entity_id is not None
    assert intent.status is IntentStatus.READY

    planner = IntentPlanner(registry)
    outcome = decider.build_plan_after_exploration(
        intent, planner,
        input_asset_ids=["asset_sales"],
        asset_columns={"asset_sales": ASSET_COLUMNS["asset_sales"]},
    )
    assert outcome.plan is not None
    assert outcome.plan.steps[1].parameters["group_by"] == "region"


def test_no_candidate_leads_to_ask_user_divergent_path(registry):
    profile = SchemaProfile(
        data_asset_id="asset_other",
        columns={"order_id": "object", "amount": "float64"},
        cardinalities={"order_id": 9000},
    )
    intent = _intent(registry, "每个区域的收入是多少？", ["department", "区域", "amount"])
    decider = ExplorationDecider(registry)
    question = decider.next_question(intent, data_asset_id="asset_other")
    result = SchemaExplorationEngine().explore(question, profile, registry)
    decision = decider.apply_result(intent, result, data_asset_id="asset_other")
    # DIVERGENT: same question, different data -> different path
    assert decision is ExplorationDecision.ASK_USER
    assert intent.status is IntentStatus.NEEDS_CLARIFICATION


def test_trend_time_exploration_enables_plan(registry):
    profile = SchemaProfile(
        data_asset_id="asset_sales",
        columns={"department": "object", "month": "datetime64[ns]", "amount": "float64"},
        cardinalities={"department": 8},
    )
    intent = _intent(registry, "收入趋势如何？", ASSET_COLUMNS["asset_sales"])
    assert intent.analysis_type.value == "trend"
    assert intent.status is IntentStatus.NEEDS_EXPLORATION

    decider = ExplorationDecider(registry)
    question = decider.next_question(intent, data_asset_id="asset_sales")
    assert question.target == "time_column"
    result = SchemaExplorationEngine().explore(question, profile, registry)
    assert result.decision is ExplorationDecision.BUILD_PLAN
    assert result.time_column == "month"
    ledger = ExplorationLedger()
    ledger.record(result)
    decider.apply_result(intent, result, data_asset_id="asset_sales")
    assert intent.status is IntentStatus.READY

    planner = IntentPlanner(registry)
    outcome = decider.build_plan_after_exploration(
        intent, planner,
        input_asset_ids=["asset_sales"],
        asset_columns={"asset_sales": ASSET_COLUMNS["asset_sales"]},
        time_column=result.time_column,
    )
    assert [s.operation for s in outcome.plan.steps] == ["inspect", "trend"]
    assert outcome.plan.steps[1].parameters["time_column"] == "month"


def test_time_column_missing_asks_user(registry):
    profile = SchemaProfile(
        data_asset_id="asset_sales",
        columns={"department": "object", "amount": "float64"},
        cardinalities={"department": 8},
    )
    intent = _intent(registry, "收入趋势如何？", ["department", "amount"])
    decider = ExplorationDecider(registry)
    question = decider.next_question(intent, data_asset_id="asset_sales")
    result = SchemaExplorationEngine().explore(question, profile, registry)
    decision = decider.apply_result(intent, result, data_asset_id="asset_sales")
    assert decision is ExplorationDecision.ASK_USER
    assert intent.status is IntentStatus.NEEDS_CLARIFICATION


def test_ledger_rejects_redundant_exploration(registry):
    profile = SchemaProfile(
        data_asset_id="asset_sales",
        columns={"region": "object"},
        cardinalities={"region": 5},
    )
    question = ExplorationQuestion(
        question_id="q_dim_区域", question="dimension?", target="dimension_candidates",
        data_asset_id="asset_sales",
    )
    engine = SchemaExplorationEngine()
    ledger = ExplorationLedger()
    ledger.record(engine.explore(question, profile, registry))
    with pytest.raises(ValueError, match="redundant exploration"):
        ledger.record(engine.explore(question, profile, registry))
    assert ledger.redundant_count == 1


def test_ledger_budget_is_bounded(registry):
    ledger = ExplorationLedger()
    profile = SchemaProfile(
        data_asset_id="asset_sales",
        columns={"region": "object", "zone": "object", "area": "object", "sector": "object", "domain": "object"},
        cardinalities={c: 5 for c in ["region", "zone", "area", "sector", "domain"]},
    )
    for i, col in enumerate(["region", "zone", "area", "sector", "domain"]):
        question = ExplorationQuestion(
            question_id=f"q_{i}_{col}", question=f"explore {col}?", target="dimension_candidates",
            data_asset_id="asset_sales",
        )
        ledger.record(SchemaExplorationEngine().explore(question, profile, registry))
    assert ledger.exhausted
    sixth = ExplorationQuestion(
        question_id="q_6_extra", question="explore extra?", target="dimension_candidates",
        data_asset_id="asset_sales",
    )
    with pytest.raises(ValueError, match="budget exhausted"):
        ledger.record(SchemaExplorationEngine().explore(sixth, profile, registry))
    assert len(ledger.results) == ExplorationLedger.MAX_EXPLORATIONS


def test_exploration_evidence_refs_are_tracked(registry):
    profile = SchemaProfile(
        data_asset_id="asset_sales",
        columns={"month": "datetime64[ns]"},
    )
    result = SchemaExplorationEngine().explore(
        ExplorationQuestion(question_id="q_t", question="time?", target="time_column"),
        profile, registry,
    )
    assert result.evidence_refs == ["schema:asset_sales:month"]
    assert result.exploration_id.startswith("expl_")


def test_explored_dimension_respects_declared_metric_boundary(registry):
    """End-to-end: a discovered dimension cannot silently join a metric that
    explicitly declares an incompatible compatibility boundary."""
    profile = SchemaProfile(
        data_asset_id="asset_sales",
        columns={"department": "object", "region": "object", "amount": "float64"},
        cardinalities={"department": 8, "region": 12},
    )
    intent = _intent(registry, "每个region的收入是多少？", ASSET_COLUMNS["asset_sales"])
    assert intent.status is IntentStatus.NEEDS_EXPLORATION
    decider = ExplorationDecider(registry)
    question = decider.next_question(intent, data_asset_id="asset_sales")
    result = SchemaExplorationEngine().explore(question, profile, registry)
    ledger = ExplorationLedger()
    ledger.record(result)
    decider.apply_result(intent, result, data_asset_id="asset_sales")
    planner = IntentPlanner(registry)
    with pytest.raises(Exception, match="allows dimensions"):
        decider.build_plan_after_exploration(
            intent, planner,
            input_asset_ids=["asset_sales"],
            asset_columns={"asset_sales": ASSET_COLUMNS["asset_sales"]},
        )

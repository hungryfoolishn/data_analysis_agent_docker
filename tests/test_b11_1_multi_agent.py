"""B11.1 Multi-Agent orchestration tests."""
import sys, pytest
from decimal import Decimal
from pathlib import Path
sys.path.insert(0, ".")

from langgraph_langchain.runtime.multi_agent import (
    AgentRole,
    ReviewDecision,
    ResearchTaskPlan,
    ReviewReport,
    OrchestratorResult,
    MultiAgentOrchestrator,
    SingleAgentRunner,
    compare_benchmark,
    default_planner,
    default_reviewer,
    PlannerAgentError,
)


class TestPlanner:
    def test_default_planner_produces_valid_plan(self):
        plan = default_planner("test goal", {"parameters": {"metric_id": "revenue"}})
        plan.validate()
        assert plan.plan_id
        assert plan.goal == "test goal"
        assert len(plan.steps) >= 1

    def test_invalid_plan_rejected(self):
        from langgraph_langchain.runtime.models import RuntimePlanStep
        step = RuntimePlanStep(step_id="s1", objective="test", method="m")
        plan = ResearchTaskPlan(plan_id="p1", goal="", steps=(step,), success_criteria=())
        with pytest.raises(PlannerAgentError):
            plan.validate()


class TestReviewer:
    def test_accept_with_evidence(self):
        class FakeResult:
            status = "calculated"
        review = default_reviewer(None, FakeResult(), ("evidence_1",))
        assert review.decision == ReviewDecision.ACCEPT

    def test_revise_without_evidence(self):
        class FakeResult:
            status = "calculated"
        review = default_reviewer(None, FakeResult(), ())
        assert review.decision == ReviewDecision.REVISE

    def test_reject_on_failure(self):
        class FakeResult:
            status = "failed"
        review = default_reviewer(None, FakeResult(), ())
        assert review.decision == ReviewDecision.REJECT


class TestOrchestrator:
    def test_orchestrator_accept_first_pass(self):
        class FakeResult:
            status = "calculated"
            value = Decimal("100")
            evidence_ids = ("ev1", "ev2")
        orch = MultiAgentOrchestrator(
            executor_fn=lambda plan: FakeResult(),
        )
        result = orch.run("test goal")
        assert result.decision == ReviewDecision.ACCEPT
        assert result.indicator_value == Decimal("100")
        assert result.evidence_ids == ("ev1", "ev2")
        assert result.total_retries == 0

    def test_orchestrator_retry_on_revise(self):
        calls = []
        class FakeResult:
            status = "calculated"
            value = None
            evidence_ids = ()
        def executor(plan):
            calls.append(1)
            return FakeResult()
        orch = MultiAgentOrchestrator(executor_fn=executor, max_retries=2)
        result = orch.run("retry goal")
        assert result.decision == ReviewDecision.REVISE
        assert result.total_retries == 2
        assert len(calls) == 3  # initial + 2 retries

    def test_orchestrator_reject_on_failure(self):
        class FakeResult:
            status = "failed"
            value = None
            evidence_ids = ()
        orch = MultiAgentOrchestrator(executor_fn=lambda plan: FakeResult())
        result = orch.run("fail goal")
        assert result.decision == ReviewDecision.REJECT
        assert result.total_retries == 2  # max_retries reached


class TestSingleAgent:
    def test_single_agent_baseline(self):
        class FakeResult:
            status = "calculated"
            value = Decimal("200")
            evidence_ids = ("ev1",)
        runner = SingleAgentRunner(executor_fn=lambda plan: FakeResult())
        result = runner.run("single goal")
        assert result["status"] == "calculated"
        assert result["value"] == Decimal("200")
        assert result["evidence_ids"] == ("ev1",)


class TestBenchmark:
    def test_compare_benchmark(self):
        single = {"status": "calculated", "value": Decimal("100"), "evidence_ids": ("ev1",), "elapsed_seconds": 1.0}
        class FakeResult:
            status = "calculated"
            value = Decimal("100")
            evidence_ids = ("ev1", "ev2")
        orch = MultiAgentOrchestrator(executor_fn=lambda plan: FakeResult())
        multi = orch.run("compare goal")
        comparison = compare_benchmark(single, multi)
        assert comparison["single_success"] is True
        assert comparison["multi_success"] is True
        assert comparison["multi_value_matches_single"] is True
        assert comparison["multi_has_evidence"] is True

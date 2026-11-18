"""B11.2-B11.6 Research-oriented multi-agent tests."""

import sys
import pytest
from pathlib import Path
sys.path.insert(0, ".")

from langgraph_langchain.runtime.research_agent import (
    SpecializedAgentRole,
    ResearchStep,
    ResearchPlan,
    ResearchFinding,
    ResearchSynthesis,
    ResearchPlanningAgent,
    DataAnalystAgent,
    ReportWriterAgent,
    QualityAuditorAgent,
    ResearchOrchestrator,
    run_research_comparison,
)
from langgraph_langchain.runtime.multi_agent import (
    AgentRole, ReviewDecision, ReviewReport,
)
from langgraph_langchain.runtime.models import RuntimePlanStep


# ---------------------------------------------------------------------------
# B11.2 Research Planning
# ---------------------------------------------------------------------------

class TestResearchPlanning:
    def test_plan_produces_multi_step_plan(self):
        agent = ResearchPlanningAgent()
        plan = agent.plan("analyze revenue trend", {"load_params": {"path": "x.csv"}})
        plan.validate()
        assert len(plan.steps) >= 3
        step_ids = {s.step_id for s in plan.steps}
        for step in plan.steps:
            for dep in step.depends_on:
                assert dep in step_ids
        assert plan.steps[0].agent_role == AgentRole.EXECUTOR
        assert plan.steps[-1].agent_role == AgentRole.REVIEWER

    def test_plan_to_runtime_steps(self):
        plan = ResearchPlanningAgent().plan("goal")
        runtime_steps = plan.to_runtime_plan()
        assert all(isinstance(s, RuntimePlanStep) for s in runtime_steps)
        assert runtime_steps[0].method == "load_data"

    def test_invalid_dependency_rejected(self):
        step = ResearchStep(
            step_id="s1", objective="x", agent_role=AgentRole.EXECUTOR,
            tool="t", depends_on=("missing",),
        )
        plan = ResearchPlan(
            plan_id="p", goal="g", steps=(step,), success_criteria=(),
        )
        with pytest.raises(Exception):
            plan.validate()

    def test_empty_plan_rejected(self):
        plan = ResearchPlan(plan_id="p", goal="g", steps=(), success_criteria=())
        with pytest.raises(Exception):
            plan.validate()


# ---------------------------------------------------------------------------
# B11.3 Specialized Agents
# ---------------------------------------------------------------------------

class FakeExecResult:
    status = "calculated"
    value = 42
    evidence_ids = ("ev_1", "ev_2")


class TestSpecializedAgents:
    def test_data_analyst_produces_structured_finding(self):
        analyst = DataAnalystAgent(executor_fn=lambda s: FakeExecResult())
        step = ResearchStep(
            step_id="s1", objective="compute revenue",
            agent_role=AgentRole.EXECUTOR, tool="run_analysis_operation",
            parameters={"metric_id": "revenue"},
        )
        finding = analyst.execute_step(step)
        assert finding.metric_id == "revenue"
        assert finding.value == 42
        assert finding.evidence_ids == ("ev_1", "ev_2")
        assert finding.confidence == 1.0
        assert finding.agent_role == SpecializedAgentRole.DATA_ANALYST.value

    def test_data_analyst_zero_confidence_without_executor(self):
        analyst = DataAnalystAgent()
        step = ResearchStep(
            step_id="s1", objective="x",
            agent_role=AgentRole.EXECUTOR, tool="t",
        )
        finding = analyst.execute_step(step)
        assert finding.confidence == 0.0
        assert finding.evidence_ids == ()

    def test_report_writer_merges_evidence(self):
        f1 = ResearchFinding(
            finding_id="f1", statement="a", metric_id="m1", value=1,
            evidence_ids=("e1",),
        )
        f2 = ResearchFinding(
            finding_id="f2", statement="b", metric_id="m2", value=2,
            evidence_ids=("e2", "e3"),
        )
        plan = ResearchPlanningAgent().plan("goal")
        writer = ReportWriterAgent()
        synthesis = writer.write(plan, [f1, f2])
        assert synthesis.evidence_ids == ("e1", "e2", "e3")
        assert synthesis.is_valid
        assert len(synthesis.findings) == 2

    def test_auditor_accepts_good_synthesis(self):
        f = ResearchFinding(
            finding_id="f1", statement="a", metric_id="m", value=1,
            evidence_ids=("e1",),
        )
        plan = ResearchPlanningAgent().plan("goal")
        synthesis = ReportWriterAgent().write(plan, [f])
        report = QualityAuditorAgent().review_synthesis(synthesis, plan)
        assert report.decision == ReviewDecision.ACCEPT
        assert report.score == 1.0

    def test_auditor_revise_on_missing_evidence(self):
        f = ResearchFinding(
            finding_id="f1", statement="a", metric_id="m", value=1,
            evidence_ids=(),
        )
        plan = ResearchPlanningAgent().plan("goal")
        synthesis = ReportWriterAgent().write(plan, [f])
        report = QualityAuditorAgent().review_synthesis(synthesis, plan)
        assert report.decision == ReviewDecision.REVISE

    def test_auditor_rejects_no_findings(self):
        plan = ResearchPlanningAgent().plan("goal")
        synthesis = ReportWriterAgent().write(plan, [])
        report = QualityAuditorAgent().review_synthesis(synthesis, plan)
        assert report.decision == ReviewDecision.REJECT


# ---------------------------------------------------------------------------
# B11.4 Evidence contract
# ---------------------------------------------------------------------------

class TestEvidenceContract:
    def test_finding_requires_evidence_ids_field(self):
        f = ResearchFinding(
            finding_id="f", statement="s", metric_id="m", value=1,
            evidence_ids=("e",),
        )
        assert f.evidence_ids == ("e",)

    def test_synthesis_collects_all_finding_evidence(self):
        f1 = ResearchFinding(
            finding_id="f1", statement="a", metric_id="m1", value=1,
            evidence_ids=("e1",),
        )
        f2 = ResearchFinding(
            finding_id="f2", statement="b", metric_id="m2", value=2,
            evidence_ids=("e2",),
        )
        plan = ResearchPlanningAgent().plan("g")
        s = ReportWriterAgent().write(plan, [f1, f2])
        assert set(s.evidence_ids) == {"e1", "e2"}


# ---------------------------------------------------------------------------
# B11.5 Advanced Orchestration
# ---------------------------------------------------------------------------

class TestResearchOrchestrator:
    def test_orchestrator_accept_first_pass(self):
        orch = ResearchOrchestrator(
            analyst=DataAnalystAgent(executor_fn=lambda s: FakeExecResult()),
        )
        result = orch.run("research goal")
        assert result.success
        assert result.decision == ReviewDecision.ACCEPT
        assert result.total_retries == 0
        assert result.synthesis is not None
        assert result.synthesis.evidence_ids == ("ev_1", "ev_2")
        assert len(result.reviews) == 1

    def test_orchestrator_step_executor_param_used(self):
        """Regression: step_executor passed to run() must reach the analyst."""
        calls = []
        orch = ResearchOrchestrator()
        result = orch.run(
            "goal",
            step_executor=lambda step: (calls.append(step.step_id), FakeExecResult())[1],
        )
        assert calls  # executor was actually invoked
        assert result.success

    def test_orchestrator_retry_with_flaky_auditor(self):
        class FlakyAuditor(QualityAuditorAgent):
            def __init__(self):
                super().__init__()
                self.calls = 0

            def review_synthesis(self, synthesis, plan):
                self.calls += 1
                report = super().review_synthesis(synthesis, plan)
                if self.calls == 1:
                    return ReviewReport(
                        decision=ReviewDecision.REVISE,
                        reason="first pass flag", score=0.5,
                    )
                return report

        orch = ResearchOrchestrator(
            analyst=DataAnalystAgent(executor_fn=lambda s: FakeExecResult()),
            auditor=FlakyAuditor(),
        )
        result = orch.run("goal")
        assert result.total_retries == 1
        assert result.success
        assert len(result.reviews) == 2

    def test_budget_exceeded_short_circuit(self):
        import time
        orch = ResearchOrchestrator(
            analyst=DataAnalystAgent(executor_fn=lambda s: FakeExecResult()),
            budget_seconds=0.0,
        )
        time.sleep(0.01)
        result = orch.run("goal")
        assert result.decision == ReviewDecision.REJECT
        assert len(result.reviews) == 0


# ---------------------------------------------------------------------------
# B11.6 Benchmark comparison
# ---------------------------------------------------------------------------

class TestResearchComparison:
    def test_comparison_returns_both_paths(self):
        result = run_research_comparison(
            "benchmark goal",
            step_executor=lambda step: FakeExecResult(),
        )
        assert "single" in result and "multi" in result
        assert result["single"]["findings_count"] >= 1
        assert result["single"]["has_evidence"]
        assert result["multi"]["review_accepted"]
        assert result["multi"]["has_evidence"]
        assert result["multi"]["has_synthesis"]
        assert result["multi"]["decision"] == "accept"

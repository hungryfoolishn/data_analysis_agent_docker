from pathlib import Path

from langgraph_langchain.evaluation import (
    EvaluationCase, EvaluationRunInput, EvaluationRunner, ReleaseGate,
    ReleaseGatePolicy, write_evaluation_reports,
)


def test_runner_writes_reports_and_gate_detects_regression(tmp_path: Path):
    case = EvaluationCase(case_id="critical", name="Critical", question="Q", critical=True)
    run = EvaluationRunInput(run_id="r", status="completed")
    current = EvaluationRunner().score("current", [(case, run)])
    json_path, html_path = write_evaluation_reports(current, tmp_path)
    assert json_path.exists() and html_path.exists()

    baseline = current.model_copy(deep=True)
    baseline.suite_id = "baseline"
    baseline.category_scores["workflow"] = 1.0
    current.category_scores["workflow"] = 0.5
    decision = ReleaseGate(ReleaseGatePolicy(minimum_category_scores={})).decide(current, baseline)
    assert not decision.allowed
    assert "workflow regressed" in decision.reasons[0]

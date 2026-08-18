from langgraph_langchain.evaluation import (
    ArtifactAssertion,
    DeterministicEvaluator,
    EvaluationCase,
    EvaluationRunInput,
    FactAssertion,
    ForbiddenClaim,
    LineageAssertion,
)


def _case():
    return EvaluationCase(
        case_id="eval-1",
        name="Deterministic example",
        question="What happened?",
        facts=[FactAssertion(
            assertion_id="revenue", description="Revenue", selector="payload.total_revenue",
            expected=100.0, absolute_tolerance=0.01,
        )],
        forbidden_claims=[ForbiddenClaim(
            assertion_id="causality", pattern=r"caused", reason="Correlation is not causality",
        )],
        artifacts=[ArtifactAssertion(
            assertion_id="chart", description="A chart", artifact_type="chart", require_content_hash=True,
        )],
        lineage=[LineageAssertion(assertion_id="lineage", description="Evidence must be traceable")],
        data_hashes={"orders.csv": "abc"},
        required_metrics=["revenue"],
        required_dimensions=["region"],
        required_time_windows=["2026-Q1"],
    )


def _run(report_text="Revenue by region increased in 2026-Q1"):
    return EvaluationRunInput(
        run_id="run-1", status="completed", report_text=report_text,
        payload={"total_revenue": 100.005}, data_hashes={"orders.csv": "abc"},
        artifacts=[{"artifact_type": "chart", "name": "revenue.png", "content_hash": "hash"}],
        findings=[{
            "execution_id": "exec-1", "input_asset_ids": ["asset-1"],
            "artifact_ids": ["artifact-1"],
        }],
    )


def test_deterministic_evaluation_passes_and_is_repeatable():
    evaluator = DeterministicEvaluator()
    first = evaluator.evaluate(_case(), _run())
    second = evaluator.evaluate(_case(), _run())
    assert first.passed
    assert first.model_dump(exclude={"evaluated_at"}) == second.model_dump(exclude={"evaluated_at"})


def test_deterministic_evaluation_classifies_calculation_overclaim_and_lineage_failures():
    run = _run("The campaign caused revenue by region to increase in 2026-Q1")
    run.payload["total_revenue"] = 50
    run.findings = [{}]
    result = DeterministicEvaluator().evaluate(_case(), run)
    assert not result.passed
    assert result.failure_counts == {"calculation": 1, "evidence": 1, "overclaim": 1}

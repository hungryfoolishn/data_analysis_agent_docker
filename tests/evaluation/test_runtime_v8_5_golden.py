"""Runtime V8.5 golden evaluation acceptance tests."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

from langgraph_langchain.evaluation import DeterministicEvaluator
from langgraph_langchain.evaluation.models import (
    EvaluationCase,
    EvaluationRunInput,
    FactAssertion,
)
from langgraph_langchain.execution.task_models import TaskExecutionRequest
from langgraph_langchain.runtime.evaluation import (
    GoldenCandidateResult,
    GoldenCaseLoader,
    GoldenEvaluationRunner,
    MetricAnswer,
)
from langgraph_langchain.runtime.evaluation.comparator import compare_metric
from langgraph_langchain.runtime.evaluation.models import MetricExpectation
from langgraph_langchain.runtime.learning import TaskEvaluation, build_skill_quality
from langgraph_langchain.schemas import AnalysisTask

REPO_ROOT = Path(__file__).resolve().parents[2]
CASES_DIR = REPO_ROOT / "tests" / "evaluation" / "golden_cases"


def test_golden_dataset_has_fifty_valid_cases():
    loader = GoldenCaseLoader(CASES_DIR)
    cases = loader.load()
    assert len(cases) >= 50
    assert len({case.case_id for case in cases}) == len(cases)
    assert loader.validate() == []
    assert sum(case.critical for case in cases) >= 1


def test_comparator_rejects_wrong_percent_scale_and_accepts_tolerance():
    expectation = MetricExpectation(
        metric="达标率",
        value=0.823,
        tolerance=0.001,
        period="2026-07",
    )
    exact = compare_metric(expectation, 82.3)
    assert exact.passed is True
    assert exact.absolute_error == pytest.approx(0.0)

    wrong = compare_metric(expectation, 82.5)
    assert wrong.passed is False
    assert wrong.absolute_error == pytest.approx(0.002)


def test_golden_runner_scores_numeric_correctness_independently():
    loader = GoldenCaseLoader(CASES_DIR)
    case = loader.load()[0]
    expectation = case.expected_metrics[0]

    correct = GoldenCandidateResult(
        case_id=case.case_id,
        run_id="golden_run_1",
        status="succeeded",
        verification_passed=True,
        metric_answers=[
            MetricAnswer(
                metric=expectation.metric,
                value=expectation.value,
                period=expectation.period,
                dimension=expectation.dimension,
                group=expectation.group,
                filters=expectation.filters,
            )
        ],
        evidence_count=1,
        verified_evidence_count=1,
        finding_count=1,
        sql_text="SELECT 1",
        report_text=" ".join(case.report_must_contain),
        skill_name="golden-skill",
        skill_version="1.0.0",
        skill_hash="sha256:test",
    )
    evaluation = GoldenEvaluationRunner().run_case(case, correct)
    assert evaluation.passed is True
    assert evaluation.score.execution_score == 1.0
    assert evaluation.score.verification_score == 1.0
    assert evaluation.score.numeric_score == 1.0
    assert evaluation.score.evidence_score == 1.0
    assert evaluation.score.finding_score == 1.0
    assert evaluation.score.report_score == 1.0
    assert evaluation.score.total_score == 1.0

    # Runtime may succeed, but the golden result must fail when the number is wrong.
    wrong_number = correct.model_copy(deep=True)
    wrong_number.metric_answers = [
        MetricAnswer(
            metric=expectation.metric,
            value=float(expectation.value) + (0.01 if abs(expectation.value) <= 1 else 1000),
            period=expectation.period,
            dimension=expectation.dimension,
            group=expectation.group,
            filters=expectation.filters,
        )
    ]
    wrong = GoldenEvaluationRunner().run_case(case, wrong_number)
    assert wrong.passed is False
    assert wrong.score.execution_score == 1.0
    assert wrong.score.verification_score == 1.0
    assert wrong.score.numeric_score == 0.0
    assert wrong.failures
    assert any(item.startswith("numeric:") for item in wrong.failures)


def test_golden_run_aggregates_by_task_executor_and_skill():
    loader = GoldenCaseLoader(CASES_DIR)
    cases = loader.load()[:3]
    candidates = {}
    for index, case in enumerate(cases):
        expectation = case.expected_metrics[0]
        candidates[case.case_id] = GoldenCandidateResult(
            case_id=case.case_id,
            run_id="golden_run_2",
            status="succeeded" if index < 2 else "failed",
            verification_passed=index < 2,
            metric_answers=[
                MetricAnswer(
                    metric=expectation.metric,
                    value=expectation.value if index < 2 else expectation.value + 1,
                    period=expectation.period,
                    dimension=expectation.dimension,
                    group=expectation.group,
                    filters=expectation.filters,
                )
            ],
            evidence_count=1,
            verified_evidence_count=1,
            finding_count=1,
            sql_text="SELECT 1",
            report_text=" ".join(case.report_must_contain),
            skill_name="skill-a" if index == 0 else "skill-b",
            skill_version="1.0.0",
            skill_hash="sha256:test",
        )

    run = GoldenEvaluationRunner().run_dataset(cases, candidates, run_id="golden_run_2")
    assert run.total_cases == 3
    assert run.passed_cases == 2
    assert run.failed_cases == 1
    assert run.pass_rate == pytest.approx(2 / 3)
    assert run.by_task_type
    assert run.by_executor
    assert set(run.by_skill) == {"skill-a", "skill-b"}
    assert len(run.failures) == 1
    assert run.failures[0].case_id == cases[2].case_id


def test_skill_quality_uses_bayesian_smoothing_and_confidence():
    base = TaskEvaluation(
        run_id="run_v8_5",
        task_id="task_v8_5",
        session_id="session_v8_5",
        execution_id="exec_v8_5",
        task_type="metric",
        executor_type="structured",
        method="analyze_metric",
        skill_id="small-sample",
        skill_name="small-sample",
        skill_version="1.0.0",
        skill_hash="sha256:small",
        status="succeeded",
        verification_status="passed",
        verification_count=1,
        passed=True,
    )
    small = build_skill_quality([base, base.model_copy(update={"task_id": "task_2"})])[0]
    assert small.attempts == 2
    assert small.success_rate == 1.0
    assert small.smoothed_success_rate == 0.75
    assert small.confidence == 0.2857
    assert small.recommendation == "neutral"

    large_evals = [
        base.model_copy(
            update={
                "evaluation_id": f"eval_large_{index}",
                "task_id": f"task_large_{index}",
                "execution_id": f"exec_large_{index}",
                "passed": index < 94,
                "failure_kind": None if index < 94 else "execution",
                "failure_reason": None if index < 94 else "failed",
            }
        )
        for index in range(100)
    ]
    large = build_skill_quality(large_evals)[0]
    assert large.attempts == 100
    assert large.successes == 94
    assert large.smoothed_success_rate == 0.9314
    assert large.confidence == 0.9524
    assert large.recommendation == "reliable"


def test_execution_result_carries_skill_version_and_hash():
    from langgraph_langchain.execution.task_models import build_execution_result

    task = AnalysisTask(
        session_id="session_v8_5",
        question="Analyze revenue",
        task_type="metric",
        executor_type="structured",
        method="analyze_metric",
    )
    request = TaskExecutionRequest(
        task=task,
        run_id="run_v8_5",
        metadata={
            "skill": {
                "name": "test-skill",
                "version": "1.2.3",
                "hash": "sha256:abc123",
            }
        },
    )
    result = build_execution_result(
        request,
        status="succeeded",
        tool_name=task.method,
    )
    assert result.skill_name == "test-skill"
    assert result.skill_version == "1.2.3"
    assert result.skill_hash == "sha256:abc123"

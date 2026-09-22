"""Runtime V8.5 advanced evaluation tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from langgraph_langchain.data.assets import ColumnSpec, DataAsset, SchemaSnapshot
from langgraph_langchain.execution.task_models import build_execution_result
from langgraph_langchain.runtime.context import AnalysisRuntime
from langgraph_langchain.runtime.evaluation.cross_task import (
    ConsistencyIssue,
    CrossTaskConsistencyVerifier,
)
from langgraph_langchain.runtime.learning import (
    FailureTaxonomy,
    LearningMemoryStore,
    MetricObservation,
    TaskEvaluation,
    build_failure_cases,
    build_task_evaluation,
    infer_failure_taxonomy,
)
from langgraph_langchain.skills_loader import SkillsLoader
from langgraph_langchain.execution.task_models import TaskExecutionRequest
from langgraph_langchain.runtime.models import RuntimeArtifact, RuntimePlanStep
from langgraph_langchain.runtime.plans import AnalysisPlan
from langgraph_langchain.runtime.report_claims import (
    ReportClaim,
    extract_report_claims,
    trace_claim_lineage,
)
from langgraph_langchain.schemas import AnalysisTask, EvidenceItem, Finding, VerificationResult


def _task_evaluation(
    *,
    task_id: str,
    execution_id: str,
    observations: list[MetricObservation],
) -> TaskEvaluation:
    return TaskEvaluation(
        run_id=f"run_{task_id}",
        task_id=task_id,
        session_id="session_v8_5_advanced",
        execution_id=execution_id,
        task_type="metric",
        executor_type="structured",
        method="analyze_metric",
        status="succeeded",
        verification_status="passed",
        verification_count=1,
        passed=True,
        metric_observations=observations,
    )


def test_cross_task_verifier_detects_contradictory_metric_values():
    first = _task_evaluation(
        task_id="task_1",
        execution_id="exec_1",
        observations=[
            MetricObservation(
                run_id="run_1",
                task_id="task_1",
                execution_id="exec_1",
                metric="q2_1_attainment",
                value=0.82,
                tolerance=0.001,
                period="2026-07",
                dimension="department",
                group="杭州开发二部",
                filters={"department": "杭州开发二部"},
            )
        ],
    )
    second = _task_evaluation(
        task_id="task_2",
        execution_id="exec_2",
        observations=[
            MetricObservation(
                run_id="run_2",
                task_id="task_2",
                execution_id="exec_2",
                metric="q2_1_attainment",
                value=0.79,
                tolerance=0.001,
                period="2026-07",
                dimension="department",
                group="杭州开发二部",
                filters={"department": "杭州开发二部"},
            )
        ],
    )
    issues = CrossTaskConsistencyVerifier().verify([first, second])
    assert len(issues) == 1
    issue = issues[0]
    assert issue.task_ids == ["task_1", "task_2"]
    assert issue.values == [0.79, 0.82]
    assert issue.max_delta == 0.03
    assert issue.filters == {"department": "杭州开发二部"}

    # The metric name is normalized case-insensitively, so this is still a contradiction.
    assert any(item.metric == "q2_1_attainment" for item in issues)


def test_cross_task_verifier_ignores_compatible_and_different_contexts():
    first = _task_evaluation(
        task_id="task_1",
        execution_id="exec_1",
        observations=[
            MetricObservation(
                run_id="run_1",
                task_id="task_1",
                execution_id="exec_1",
                metric="q2_1_attainment",
                value=0.8205,
                tolerance=0.001,
                period="2026-07",
                group="杭州开发二部",
            )
        ],
    )
    same = _task_evaluation(
        task_id="task_2",
        execution_id="exec_2",
        observations=[
            MetricObservation(
                run_id="run_2",
                task_id="task_2",
                execution_id="exec_2",
                metric="q2_1_attainment",
                value=0.8212,
                tolerance=0.001,
                period="2026-07",
                group="杭州开发二部",
            )
        ],
    )
    different_period = _task_evaluation(
        task_id="task_3",
        execution_id="exec_3",
        observations=[
            MetricObservation(
                run_id="run_3",
                task_id="task_3",
                execution_id="exec_3",
                metric="q2_1_attainment",
                value=0.10,
                tolerance=0.001,
                period="2026-08",
                group="杭州开发二部",
            )
        ],
    )
    assert CrossTaskConsistencyVerifier().verify([first, same, different_period]) == []


def test_cross_task_does_not_confuse_different_group_contexts():
    evaluations = []
    for index, (task_id, group, value) in enumerate(
        [
            ("task_east", "East", 100),
            ("task_west", "West", 80),
        ],
        start=1,
    ):
        evaluations.append(
            _task_evaluation(
                task_id=task_id,
                execution_id=f"exec_{task_id}",
                observations=[
                    MetricObservation(
                        run_id=f"run_{task_id}",
                        task_id=task_id,
                        execution_id=f"exec_{task_id}",
                        metric="revenue",
                        value=value,
                        tolerance=0.001,
                        period="2026-07",
                        dimension="department",
                        group=group,
                        filters={"department": group},
                    )
                ],
            )
        )

    assert CrossTaskConsistencyVerifier().verify(evaluations) == []


def test_cross_task_uses_pairwise_tolerance():
    observations = [
        MetricObservation(
            run_id=f"run_{task_id}",
            task_id=task_id,
            execution_id=f"exec_{task_id}",
            metric="revenue",
            value=value,
            tolerance=tolerance,
            period="2026-07",
            group=group,
        )
        for task_id, value, tolerance, group in [
            ("task_a", 10, 0.1, "A"),
            ("task_b", 20, 100.0, "A"),
            ("task_c", 30, 0.1, "A"),
        ]
    ]
    evaluations = [
        _task_evaluation(
            task_id=observation.task_id,
            execution_id=observation.execution_id,
            observations=[observation],
        )
        for observation in observations
    ]
    issues = CrossTaskConsistencyVerifier().verify(evaluations)

    # The permissive tolerance on B must not hide the A/C contradiction.
    assert {(issue.task_ids[0], issue.task_ids[1]) for issue in issues} == {
        ("task_a", "task_c")
    }
    assert issues[0].task_ids == ["task_a", "task_c"]
    assert issues[0].tolerance == 0.1
    assert issues[0].values == [10, 30]
    assert issues[0].max_delta == 20


def test_failure_case_uses_structured_taxonomy():
    evaluation = TaskEvaluation(
        run_id="run_failed",
        task_id="task_failed",
        session_id="session_v8_5_advanced",
        execution_id="exec_failed",
        task_type="metric",
        executor_type="structured",
        method="analyze_metric",
        status="failed",
        verification_status="failed",
        verification_failure_count=1,
        passed=False,
        failure_kind="verification",
        failure_reason="Metric value differs by 0.002; tolerance is 0.001",
        metadata={"error_type": "RuntimeVerificationError"},
    )
    taxonomy = infer_failure_taxonomy(
        evaluation.failure_kind,
        evaluation.failure_reason,
        evaluation.metadata,
    )
    assert taxonomy.stage == "verification"
    assert taxonomy.category == "numeric_consistency"
    assert taxonomy.root_cause == "calculation_mismatch"
    assert taxonomy.symptom == "numeric_value_mismatch"

    cases = build_failure_cases([evaluation])
    assert len(cases) == 1
    failure = cases[0]
    assert failure.failure_kind == "verification"
    assert failure.stage == "verification"
    assert failure.category == "numeric_consistency"
    assert failure.root_cause == "calculation_mismatch"
    assert failure.symptom == "numeric_value_mismatch"
    assert failure.taxonomy == taxonomy

    # The original V8 field remains available for backward compatibility.
    assert failure.failure_kind == "verification"


def test_report_claims_and_lineage_trace_to_execution():
    evidence = EvidenceItem(
        verification_status="verified",
        verification_result_id="verify_1",
        evidence_text="North department revenue declined by 10500 in 2026-04.",
        source_fields=["department", "revenue"],
        source_artifact_ids=["artifact_1"],
        source_execution_ids=["exec_1"],
        source_step_ids=["step_1"],
        stats={"revenue_change": -10500},
        calculation_method="sum(revenue) by department/month",
    )
    finding = Finding(
        finding_id="finding_1",
        statement="North department revenue declined by 10500.",
        evidence=[evidence],
        supported_by=[evidence.evidence_id],
    )
    artifact = RuntimeArtifact(
        artifact_id="artifact_1",
        artifact_type="table",
        name="revenue_by_month.csv",
        path="revenue_by_month.csv",
        relative_path="revenue_by_month.csv",
        url="/workspace/files/revenue_by_month.csv",
        execution_id="exec_1",
    )
    execution = build_execution_result(
        TaskExecutionRequest(
            task=AnalysisTask(
                session_id="session_v8_5_advanced",
                question="Analyze North revenue change",
                task_type="metric",
                executor_type="structured",
                method="analyze_metric",
            ),
            run_id="run_claim",
            metadata={
                "skill": {
                    "name": "revenue-decline",
                    "id": "skill_stable_001",
                    "version": "1.0.0",
                    "hash": "sha256:skill",
                }
            },
        ),
        status="succeeded",
        tool_name="analyze_metric",
    )
    assert execution.skill_name == "revenue-decline"
    assert execution.skill_id == "skill_stable_001"
    # build_execution_result does not force an execution ID, so set the expected one.
    execution.execution_id = "exec_1"

    report = (
        "## Key Findings\n\n"
        "- North department revenue declined by 10500.\n"
        "- This unrelated sentence has no matching Runtime finding.\n"
    )
    claims = extract_report_claims(report, [finding], [evidence])
    assert len(claims) == 2
    matched = claims[0]
    unmatched = claims[1]
    assert matched.finding_ids == ["finding_1"]
    assert matched.evidence_ids == [evidence.evidence_id]
    assert matched.verified is True
    assert unmatched.finding_ids == []
    assert unmatched.verified is False

    lineage = trace_claim_lineage(
        matched,
        [finding],
        [evidence],
        [artifact],
        [execution],
    )
    assert lineage.claim_id == matched.claim_id
    assert lineage.evidence_ids == [evidence.evidence_id]
    assert lineage.artifact_ids == ["artifact_1"]
    assert lineage.execution_ids == ["exec_1"]
    assert lineage.skill_names == ["revenue-decline"]
    assert lineage.complete is True
    assert lineage.missing == []

    incomplete = trace_claim_lineage(
        unmatched,
        [finding],
        [evidence],
        [artifact],
        [execution],
    )
    assert incomplete.complete is False
    assert "finding" in incomplete.missing
    assert "evidence" in incomplete.missing


def test_learning_memory_persists_cross_task_issues(tmp_path: Path):
    first = _task_evaluation(
        task_id="task_1",
        execution_id="exec_1",
        observations=[
            MetricObservation(
                run_id="run_1",
                task_id="task_1",
                execution_id="exec_1",
                metric="q2_1_attainment",
                value=0.82,
                tolerance=0.001,
                period="2026-07",
                group="杭州开发二部",
            )
        ],
    )
    second = _task_evaluation(
        task_id="task_2",
        execution_id="exec_2",
        observations=[
            MetricObservation(
                run_id="run_2",
                task_id="task_2",
                execution_id="exec_2",
                metric="q2_1_attainment",
                value=0.79,
                tolerance=0.001,
                period="2026-07",
                group="杭州开发二部",
            )
        ],
    )
    path = tmp_path / "learning.json"
    memory = LearningMemoryStore(path)
    snapshot = memory.record_evaluations([first, second])
    assert len(snapshot.consistency_issues) == 1
    assert snapshot.consistency_issues[0].task_ids == ["task_1", "task_2"]

    restored = LearningMemoryStore(path)
    assert restored.snapshot().consistency_issues[0].max_delta == 0.03

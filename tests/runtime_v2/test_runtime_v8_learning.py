"""Runtime V8 evaluation and learning acceptance tests."""

from __future__ import annotations

from pathlib import Path


from langgraph_langchain.execution.task_models import build_execution_result
from langgraph_langchain.runtime.context import AnalysisRuntime
from langgraph_langchain.runtime.learning import (
    LearningMemoryStore,
    TaskEvaluation,
    build_skill_quality,
)
from langgraph_langchain.runtime.models import RuntimePlanStep
from langgraph_langchain.runtime.plans import AnalysisPlan
from langgraph_langchain.runtime.skill_retriever import SkillRetriever
from langgraph_langchain.skills_loader import SkillMeta
from langgraph_langchain.schemas import AnalysisTask, EvidenceItem, Finding, VerificationResult
from langgraph_langchain.runtime.verification_policy import VerificationPolicy


QUESTION = "Analyze the verified revenue trend and learn from task outcomes."


class MetadataExecutor:
    """Executor that preserves skill metadata for learning tests."""

    def __init__(self, *, failed: bool = False) -> None:
        self.failed = failed

    def execute(self, request):
        if self.failed:
            return build_execution_result(
                request,
                status="failed",
                tool_name=request.task.method,
                error={"type": "ToolExecutionError", "message": "analysis failed"},
            )
        return build_execution_result(
            request,
            status="succeeded",
            tool_name=request.task.method,
            stdout_preview="analysis completed",
        )


def _plan() -> AnalysisPlan:
    return AnalysisPlan(
        goal=QUESTION,
        steps=[
            RuntimePlanStep(
                objective="Analyze revenue",
                method="analyze_metric",
                expected_outputs=["analysis_result"],
            )
        ],
        require_confirmation=False,
    )


def _runtime(tmp_path: Path, session_id: str) -> AnalysisRuntime:
    return AnalysisRuntime(
        workspace_dir=tmp_path / session_id,
        session_id=session_id,
        question=QUESTION,
    )


def test_successful_task_creates_evaluation_and_skill_quality(tmp_path: Path):
    runtime = _runtime(tmp_path, "v8-success")
    runtime.initialize_plan(_plan())
    runtime.build_execution_controller(
        structured_executor=MetadataExecutor(),
        verification_policy=VerificationPolicy(),
    )

    result = runtime.execute_next_task()
    assert result.status == "succeeded"

    evaluation = runtime.evaluations[0]
    assert evaluation.task_id == result.task_id
    assert evaluation.execution_id == result.execution_id
    assert evaluation.status == "succeeded"
    assert evaluation.passed is True
    assert evaluation.verification_status == "passed"
    assert evaluation.verification_count >= 1
    assert evaluation.skill_fallback is True

    reevaluated = runtime.record_task_evaluation(result, persist=False)
    assert reevaluated.task_id == evaluation.task_id
    snapshot = runtime.learning_summary()
    assert len(snapshot.evaluations) == 1
    assert snapshot.evaluations[0].task_id == evaluation.task_id
    assert not snapshot.failure_cases
    assert snapshot.skill_quality
    quality = snapshot.skill_quality[0]
    assert quality.skill_name is None
    assert quality.attempts == 1
    assert quality.successes == 1
    assert quality.success_rate == 1.0
    assert quality.recommendation == "neutral"

    assert (runtime.workspace_dir / ".analysis_learning.json").is_file()

    restored = AnalysisRuntime(
        workspace_dir=runtime.workspace_dir,
        session_id="v8-success",
        question=QUESTION,
    )
    assert restored.evaluations[0].evaluation_id == evaluation.evaluation_id
    assert restored.learning_summary().skill_quality[0].attempts == 1


def test_verification_failure_enters_failure_memory_and_restarts_snapshot(tmp_path: Path):
    runtime = _runtime(tmp_path, "v8-failure")
    runtime.initialize_plan(_plan())
    task = runtime.scheduler.tasks[0]
    task.constraints["verification_contract"] = {
        "numeric_consistency": {"expected": -10500, "actual": -10000},
    }
    runtime.build_execution_controller(
        structured_executor=MetadataExecutor(),
        verification_policy=VerificationPolicy(),
    )

    result = runtime.execute_next_task()
    assert result.status == "failed"

    evaluation = runtime.evaluations[0]
    assert evaluation.passed is False
    assert evaluation.failure_kind == "verification"
    assert "Values differ" in (evaluation.failure_reason or "")

    snapshot = runtime.learning_summary()
    assert len(snapshot.failure_cases) == 1
    failure = snapshot.failure_cases[0]
    assert failure.failure_kind == "verification"
    assert failure.occurrences == 1
    assert "verification contract" in failure.recommendation
    assert snapshot.skill_quality[0].failures == 1
    assert snapshot.skill_quality[0].recommendation == "neutral"

    restored = AnalysisRuntime(
        workspace_dir=runtime.workspace_dir,
        session_id="v8-failure",
        question=QUESTION,
    )
    assert restored.evaluations[0].failure_kind == "verification"
    assert restored.learning_summary().failure_cases[0].failure_kind == "verification"


def test_failure_cases_and_skill_quality_aggregate_across_runs():
    first = TaskEvaluation(
        run_id="run_1",
        task_id="task_1",
        session_id="session_v8",
        execution_id="exec_1",
        task_type="comparison",
        executor_type="structured",
        method="compare_groups",
        skill_name="cohort-analysis",
        skill_fallback=False,
        status="succeeded",
        verification_status="passed",
        verification_count=1,
        passed=True,
    )
    second = first.model_copy(
        update={
            "evaluation_id": "eval_2",
            "run_id": "run_2",
            "task_id": "task_2",
            "execution_id": "exec_2",
            "status": "failed",
            "verification_status": "failed",
            "verification_failure_count": 1,
            "passed": False,
            "failure_kind": "verification",
            "failure_reason": "Values differ by 500",
        }
    )
    third = first.model_copy(
        update={
            "evaluation_id": "eval_3",
            "run_id": "run_3",
            "task_id": "task_3",
            "execution_id": "exec_3",
            "status": "failed",
            "verification_status": "failed",
            "verification_failure_count": 1,
            "passed": False,
            "failure_kind": "verification",
            "failure_reason": "Values differ by 500",
        }
    )

    memory = LearningMemoryStore(Path("unused-learning.json"))
    snapshot = memory.record_evaluations([first, second, third], save=False)

    assert len(snapshot.failure_cases) == 1
    failure = snapshot.failure_cases[0]
    assert failure.failure_kind == "verification"
    assert failure.occurrences == 2

    quality = snapshot.skill_quality[0]
    assert quality.attempts == 3
    assert quality.successes == 1
    assert quality.failures == 2
    assert quality.success_rate == 0.3333
    assert quality.recommendation == "needs_review"


def test_skill_retriever_applies_learning_memory(tmp_path: Path):
    good_evals = [
        TaskEvaluation(
            evaluation_id=f"eval_good_{index}",
            run_id=f"run_good_{index}",
            task_id=f"task_good_{index}",
            session_id="session_v8",
            execution_id=f"exec_good_{index}",
            task_type="trend",
            executor_type="structured",
            method="analyze_time_trend",
            skill_name="good-skill",
            skill_fallback=False,
            status="succeeded",
            verification_status="passed",
            verification_count=1,
            passed=True,
        )
        for index in range(3)
    ]
    bad_evals = [
        TaskEvaluation(
            evaluation_id=f"eval_bad_{index}",
            run_id=f"run_bad_{index}",
            task_id=f"task_bad_{index}",
            session_id="session_v8",
            execution_id=f"exec_bad_{index}",
            task_type="trend",
            executor_type="structured",
            method="analyze_time_trend",
            skill_name="bad-skill",
            skill_fallback=False,
            status="failed",
            verification_status="failed",
            verification_failure_count=1,
            passed=False,
            failure_kind="verification",
            failure_reason="Values differ",
        )
        for index in range(2)
    ]

    memory = LearningMemoryStore(tmp_path / "learning.json")
    memory.record_evaluations([*good_evals, *bad_evals], save=False)
    assert memory.recommendation_for_skill("good-skill") == "reliable"
    assert memory.recommendation_for_skill("bad-skill") == "needs_review"

    class FakeLoader:
        def skills_list(self):
            return [
                SkillMeta(
                    name="bad-skill",
                    description="Trend analysis",
                    tags=["trend"],
                    trigger_keywords=["trend"],
                ),
                SkillMeta(
                    name="good-skill",
                    description="Trend analysis",
                    tags=["trend"],
                    trigger_keywords=["trend"],
                ),
            ]

    task = AnalysisTask(
        session_id="session_v8",
        question="Analyze the revenue trend",
        task_type="trend",
        method="analyze_time_trend",
    )
    match = SkillRetriever(FakeLoader(), learning_memory=memory).retrieve_for_task(task)
    assert match.skill_name == "good-skill"
    assert "learning memory marks this skill reliable" in match.reason


def test_direct_finding_and_evidence_are_counted_by_evaluation(tmp_path: Path):
    runtime = _runtime(tmp_path, "v8-lineage")
    runtime.initialize_plan(_plan())
    task = runtime.scheduler.tasks[0]
    runtime.start_task(task.task_id)

    execution = build_execution_result(
        runtime.build_execution_request(task),
        status="succeeded",
        tool_name=task.method,
        stdout_preview="analysis completed",
    )
    runtime.record_execution_result(execution)
    runtime.complete_task(task.task_id)

    verification = VerificationResult(
        run_id=runtime.run.run_id,
        task_id=task.task_id,
        step_id=execution.step_id,
        execution_id=execution.execution_id,
        check_type="custom",
        status="passed",
        passed=True,
        message="Output is consistent",
    )
    runtime.record_verification(verification)
    evidence = EvidenceItem(
        verification_status="verified",
        verification_result_id=verification.verification_id,
        evidence_text="The analysis output is consistent.",
        source_execution_ids=[execution.execution_id],
        source_step_ids=[execution.step_id],
    )
    runtime.record_evidence(evidence)
    finding = Finding(
        finding_id="F001",
        statement="The verified analysis supports the requested conclusion.",
        evidence=[evidence],
        supported_by=[evidence.evidence_id],
        recorded_by_execution_id=execution.execution_id,
        recorded_by_step_id=execution.step_id,
    )
    runtime.record_finding(finding)

    evaluation = runtime.record_task_evaluation(execution, persist=False)
    assert evaluation.passed is True
    assert evaluation.verification_count == 1
    assert evaluation.evidence_count == 1
    assert evaluation.verified_evidence_count == 1
    assert evaluation.finding_count == 1
    assert build_skill_quality([evaluation])[0].success_rate == 1.0


import pytest

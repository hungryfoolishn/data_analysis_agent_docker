from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from langgraph_langchain.execution.structured_executor import StructuredTaskExecutor
from langgraph_langchain.execution.task_models import TaskExecutionRequest
from langgraph_langchain.runtime.executor import TaskExecutor
from langgraph_langchain.runtime.models import ExecutionResult, RuntimeArtifact
from langgraph_langchain.runtime.runner import TaskRunner
from langgraph_langchain.runtime.scheduler import TaskScheduler
from langgraph_langchain.schemas import AnalysisTask, EvidenceItem, VerificationResult


class FakeTool:
    def __init__(self, result: Any) -> None:
        self.result = result

    def invoke(self, arguments: dict[str, Any]) -> Any:
        return self.result


def make_task(task_id: str = "task_1", *, method: str = "compare_groups") -> AnalysisTask:
    return AnalysisTask(
        task_id=task_id,
        session_id="session_1",
        question="Compare groups",
        task_type="comparison",
        executor_type="structured",
        method=method,
    )


def make_request(task: AnalysisTask) -> TaskExecutionRequest:
    return TaskExecutionRequest(task=task, run_id="run_1")


def make_scheduler(task: AnalysisTask) -> TaskScheduler:
    return TaskScheduler([task])


def make_executor() -> TaskExecutor:
    return TaskExecutor(
        structured=StructuredTaskExecutor(
            tools={
                "compare_groups": FakeTool(
                    json.dumps({"artifact_id": "artifact_1"})
                )
            }
        )
    )


def passing_verifier(result: ExecutionResult) -> list[VerificationResult]:
    return [
        VerificationResult(
            run_id=result.run_id,
            task_id=result.task_id,
            step_id=result.step_id,
            execution_id=result.execution_id,
            artifact_id=result.output_artifact_ids[0],
            check_type="artifact_existence",
            status="passed",
            passed=True,
            message="artifact exists",
        )
    ]


def failing_verifier(result: ExecutionResult) -> list[VerificationResult]:
    return [
        VerificationResult(
            run_id=result.run_id,
            task_id=result.task_id,
            step_id=result.step_id,
            execution_id=result.execution_id,
            check_type="custom",
            status="failed",
            passed=False,
            message="artifact missing",
        )
    ]


def evidence_factory(
    result: ExecutionResult,
    verifications: list[VerificationResult],
) -> EvidenceItem:
    return EvidenceItem(
        evidence_text="Verified execution produced a table.",
        verification_status="verified",
        verification_result_id=verifications[0].verification_id,
        source_artifact_ids=list(result.output_artifact_ids),
        source_execution_ids=[result.execution_id],
        source_step_ids=[result.step_id] if result.step_id else [],
    )


def test_runner_without_hooks_preserves_legacy_success(tmp_path: Path):
    task = make_task()
    runner = TaskRunner(
        scheduler=make_scheduler(task),
        executor=make_executor(),
    )

    step = runner.execute_next(run_id="run_1", request_factory=make_request)

    assert step.action == "executed"
    assert step.result is not None
    assert step.result.status == "succeeded"
    assert step.result.verification_results == []
    assert step.result.evidence is None
    assert runner.scheduler.get_task("task_1").status == "succeeded"


def test_runner_runs_passing_verification_and_generates_evidence(tmp_path: Path):
    path = tmp_path / "result.csv"
    path.write_text("group,value\nA,1\n", encoding="utf-8")
    artifact = RuntimeArtifact(
        artifact_id="artifact_1",
        artifact_type="table",
        name="result.csv",
        path=str(path),
        relative_path="result.csv",
        url="/workspace/files/result.csv",
        execution_id="execution_tool",
        step_id="step_1",
    )
    task = make_task()
    scheduler = make_scheduler(task)
    runner = TaskRunner(
        scheduler=scheduler,
        executor=make_executor(),
        verifier=passing_verifier,
        evidence_factory=evidence_factory,
    )

    step = runner.execute_next(run_id="run_1", request_factory=make_request)

    assert step.action == "executed"
    assert step.result is not None
    assert step.result.status == "succeeded"
    assert len(step.result.verification_results) == 1
    assert step.result.verification_results[0].passed is True
    assert step.result.evidence is not None
    assert step.result.evidence.verification_status == "verified"
    assert step.result.evidence.verification_result_id == (
        step.result.verification_results[0].verification_id
    )
    assert scheduler.get_task("task_1").status == "succeeded"


def test_runner_failed_verification_marks_task_failed():
    task = make_task()
    scheduler = make_scheduler(task)
    runner = TaskRunner(
        scheduler=scheduler,
        executor=make_executor(),
        verifier=failing_verifier,
        evidence_factory=evidence_factory,
    )

    step = runner.execute_next(run_id="run_1", request_factory=make_request)

    assert step.action == "failed"
    assert "artifact missing" in step.reason
    assert step.result is not None
    assert step.result.status == "failed"
    assert step.result.evidence is None
    assert scheduler.get_task("task_1").status == "failed"


def test_runner_evidence_failure_marks_task_failed():
    task = make_task()
    scheduler = make_scheduler(task)

    def bad_evidence_factory(result, verifications):
        raise RuntimeError("evidence link missing")

    runner = TaskRunner(
        scheduler=scheduler,
        executor=make_executor(),
        verifier=passing_verifier,
        evidence_factory=bad_evidence_factory,
    )

    step = runner.execute_next(run_id="run_1", request_factory=make_request)

    assert step.action == "failed"
    assert "evidence link missing" in step.reason
    assert step.result is not None
    assert step.result.status == "failed"
    assert step.result.evidence is None
    assert scheduler.get_task("task_1").status == "failed"


def test_runner_does_not_generate_evidence_without_artifacts():
    class NoArtifactTool:
        def invoke(self, arguments: dict[str, Any]) -> Any:
            return "status only"

    task = make_task()
    scheduler = make_scheduler(task)
    executor = TaskExecutor(
        structured=StructuredTaskExecutor(tools={"compare_groups": NoArtifactTool()})
    )
    calls: list[Any] = []

    def no_artifact_verifier(result):
        return [
            VerificationResult(
                run_id=result.run_id,
                task_id=result.task_id,
                step_id=result.step_id,
                execution_id=result.execution_id,
                check_type="custom",
                status="passed",
                passed=True,
                message="status-only result is valid",
            )
        ]

    runner = TaskRunner(
        scheduler=scheduler,
        executor=executor,
        verifier=no_artifact_verifier,
        evidence_factory=lambda result, verifications: calls.append(result) or evidence_factory(
            result,
            verifications,
        ),
    )

    step = runner.execute_next(run_id="run_1", request_factory=make_request)

    assert step.action == "executed"
    assert step.result is not None
    assert step.result.output_artifact_ids == []
    assert len(step.result.verification_results) == 1
    assert step.result.verification_results[0].passed is True
    assert step.result.evidence is None
    assert calls == []
    assert scheduler.get_task("task_1").status == "succeeded"


def test_runner_verification_hook_failure_marks_task_failed():
    task = make_task()
    scheduler = make_scheduler(task)

    def bad_verifier(result):
        raise RuntimeError("verifier crashed")

    runner = TaskRunner(
        scheduler=scheduler,
        executor=make_executor(),
        verifier=bad_verifier,
    )

    step = runner.execute_next(run_id="run_1", request_factory=make_request)

    assert step.action == "failed"
    assert "verifier crashed" in step.reason
    assert scheduler.get_task("task_1").status == "failed"


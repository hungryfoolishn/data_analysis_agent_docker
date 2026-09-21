from __future__ import annotations

from typing import Any

import pytest

from langgraph_langchain.execution.task_models import TaskExecutionRequest
from langgraph_langchain.runtime.context import AnalysisRuntime
from langgraph_langchain.runtime.models import ExecutionResult, RuntimePlanStep
from langgraph_langchain.runtime.plans import AnalysisPlan
from langgraph_langchain.runtime.scheduler import TaskScheduler
from langgraph_langchain.schemas import EvidenceItem, VerificationResult


class FakeExecutor:
    def __init__(self, results: list[ExecutionResult]) -> None:
        self.results = list(results)
        self.requests: list[TaskExecutionRequest] = []

    def execute(self, request: TaskExecutionRequest) -> ExecutionResult:
        self.requests.append(request)
        if not self.results:
            raise AssertionError("FakeExecutor has no remaining results")
        return self.results.pop(0)


def make_runtime(tmp_path, *, session_id: str = "session_v5") -> AnalysisRuntime:
    return AnalysisRuntime(
        workspace_dir=tmp_path / session_id,
        session_id=session_id,
        question="Analyze department revenue changes",
    )


def make_plan() -> AnalysisPlan:
    first = RuntimePlanStep(
        objective="Load and inspect source data",
        method="load_data",
        expected_outputs=["schema_snapshot"],
    )
    second = RuntimePlanStep(
        objective="Compare departments",
        method="compare_groups",
        expected_outputs=["group_comparison"],
        depends_on=[first.step_id],
    )
    return AnalysisPlan(
        goal="Analyze department revenue changes",
        steps=[first, second],
    )


def make_result(
    task_id: str,
    *,
    status: str = "succeeded",
    execution_id: str = "execution_1",
    verification_id: str | None = None,
    evidence_id: str | None = None,
) -> ExecutionResult:
    execution_serial = execution_id.removeprefix("execution_")
    verification_id = verification_id or f"verification_{execution_serial}"
    evidence_id = evidence_id or f"evidence_{execution_serial}"
    verification = VerificationResult(
        verification_id=verification_id,
        run_id="run_1",
        task_id=task_id,
        step_id=task_id,
        execution_id=execution_id,
        check_type="artifact_existence",
        status="passed" if status == "succeeded" else "failed",
        passed=status == "succeeded",
        message="artifact exists" if status == "succeeded" else "artifact missing",
    )
    evidence = None
    if status == "succeeded":
        evidence = EvidenceItem(
            evidence_id=evidence_id,
            evidence_text="Verified execution artifact exists.",
            verification_status="verified",
            verification_result_id=verification.verification_id,
            source_execution_ids=[execution_id],
            source_step_ids=[task_id],
        )
    return ExecutionResult(
        execution_id=execution_id,
        run_id="run_1",
        task_id=task_id,
        step_id=task_id,
        tool_name="compare_groups",
        status=status,  # type: ignore[arg-type]
        output_artifact_ids=["artifact_1"],
        verification_results=[verification],
        evidence=evidence,
    )


def test_initialize_plan_hands_scheduling_to_runtime(tmp_path):
    runtime = make_runtime(tmp_path)
    plan = make_plan()

    scheduler = runtime.initialize_plan(plan, max_running_tasks=1)

    assert isinstance(scheduler, TaskScheduler)
    assert runtime.scheduler is scheduler
    assert runtime.plan.goal == "Analyze department revenue changes"
    assert runtime.run.plan["goal"] == "Analyze department revenue changes"
    assert [step.step_id for step in runtime.run.steps] == [
        step.step_id for step in plan.steps
    ]
    assert scheduler.execution_queue() == (
        plan.steps[0].step_id,
        plan.steps[1].step_id,
    )
    assert scheduler.next_task().task_id == plan.steps[0].step_id


def test_runtime_task_facade_updates_plan_steps(tmp_path):
    runtime = make_runtime(tmp_path)
    plan = make_plan()
    scheduler = runtime.initialize_plan(plan)
    first_id = plan.steps[0].step_id
    second_id = plan.steps[1].step_id

    assert runtime.next_task().task_id == first_id
    runtime.start_task(first_id)

    first_step = runtime.run.steps[0]
    assert first_step.status == "running"
    assert first_step.started_at is not None
    assert runtime.run.current_step_id == first_id
    assert runtime.status == "running"

    runtime.complete_task(first_id)
    second_step = runtime.run.steps[1]

    assert first_step.status == "succeeded"
    assert first_step.completed_at is not None
    assert runtime.run.current_step_id is None
    assert runtime.next_task().task_id == second_id

    runtime.start_task(second_id)
    runtime.fail_task(second_id, error="bad column")
    assert scheduler.get_task(second_id).status == "failed"
    assert second_step.status == "failed"
    assert second_step.error == "bad column"
    assert runtime.status == "failed"
    assert runtime.failures[-1]["task_id"] == second_id


def test_configure_executor_and_execute_next_task(tmp_path):
    runtime = make_runtime(tmp_path)
    plan = make_plan()
    runtime.initialize_plan(plan)
    first_id = plan.steps[0].step_id
    second_id = plan.steps[1].step_id
    executor = FakeExecutor(
        [
            make_result(first_id, execution_id="execution_1"),
            make_result(second_id, execution_id="execution_2"),
        ]
    )
    runtime.configure_executor(executor)

    first_result = runtime.execute_next_task()
    second_result = runtime.execute_next_task()

    assert first_result is not None
    assert first_result.task_id == first_id
    assert first_result.verification_results[0].passed is True
    assert first_result.evidence.verification_status == "verified"
    assert second_result is not None
    assert second_result.task_id == second_id

    assert [task.task_id for task in runtime.scheduler.tasks] == [first_id, second_id]
    assert [task.status for task in runtime.scheduler.tasks] == ["succeeded", "succeeded"]
    assert [step.status for step in runtime.run.steps] == ["succeeded", "succeeded"]
    assert [result.execution_id for result in runtime.executions] == [
        "execution_1",
        "execution_2",
    ]
    assert [item.verification_id for item in runtime.verifications] == [
        "verification_1",
        "verification_2",
    ]
    assert [item.evidence_id for item in runtime.evidence] == [
        "evidence_1",
        "evidence_2",
    ]
    assert runtime.status == "completed"
    assert runtime.run.status == "completed"
    assert runtime.run.steps[-1].status == "succeeded"
    assert runtime.run.current_step_id is None


def test_execute_next_task_records_failure_lineage(tmp_path):
    runtime = make_runtime(tmp_path)
    plan = make_plan()
    runtime.initialize_plan(plan)
    first_id = plan.steps[0].step_id
    executor = FakeExecutor(
        [make_result(first_id, status="failed", execution_id="execution_failed")]
    )
    runtime.configure_executor(executor)

    result = runtime.execute_next_task()

    assert result is not None
    assert result.status == "failed"
    assert result.verification_results[0].passed is False
    assert result.evidence is None
    assert runtime.scheduler.get_task(first_id).status == "failed"
    assert runtime.run.steps[0].status == "failed"
    assert runtime.run.status == "failed"
    assert runtime.status == "failed"
    assert runtime.failures[-1]["task_id"] == first_id


def test_record_execution_result_is_idempotent(tmp_path):
    runtime = make_runtime(tmp_path)
    plan = make_plan()
    runtime.initialize_plan(plan)
    task_id = plan.steps[0].step_id
    result = make_result(task_id)

    runtime.record_execution_result(result)
    runtime.record_execution_result(result)

    assert len(runtime.executions) == 1
    assert len(runtime.verifications) == 1
    assert len(runtime.evidence) == 1


def test_build_execution_request_uses_runtime_context(tmp_path):
    runtime = make_runtime(tmp_path)
    plan = make_plan()
    runtime.initialize_plan(plan)
    task = runtime.scheduler.get_task(plan.steps[0].step_id)
    task.constraints["arguments"] = {"dimension": "department"}
    task.constraints["source_path"] = "/tmp/source.csv"
    task.constraints["timeout_seconds"] = 12

    request = runtime.build_execution_request(task)

    assert request.run_id == runtime.run.run_id
    assert request.arguments == {"dimension": "department"}
    assert request.workspace_dir == str(runtime.workspace_dir)
    assert request.source_path == "/tmp/source.csv"
    assert request.timeout_seconds == 12


def test_runtime_snapshot_restores_v2_lineage(tmp_path):
    runtime = make_runtime(tmp_path)
    plan = make_plan()
    runtime.initialize_plan(plan)
    task_id = plan.steps[0].step_id
    result = make_result(task_id)
    runtime.record_execution_result(result)

    restored = AnalysisRuntime(
        workspace_dir=runtime.workspace_dir,
        session_id=runtime.session_id,
        question=runtime.task.question,
    )

    assert restored.plan is not None
    assert restored.plan.goal == plan.goal
    assert [item.execution_id for item in restored.executions] == [
        result.execution_id
    ]
    assert restored.verifications[0].verification_id == (
        result.verification_results[0].verification_id
    )
    assert restored.evidence[0].evidence_id == result.evidence.evidence_id
    assert restored.status == runtime.run.status
    assert restored.run.steps[0].status == "succeeded"

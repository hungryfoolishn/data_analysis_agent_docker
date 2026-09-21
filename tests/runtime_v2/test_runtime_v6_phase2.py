"""Runtime V6 Phase 2 acceptance tests."""

from __future__ import annotations

from pathlib import Path

from langgraph_langchain.config import RUNTIME_V2_FALLBACK_ENABLED
from langgraph_langchain.execution.task_models import build_execution_result
from langgraph_langchain.runtime.models import RuntimePlanStep
from langgraph_langchain.runtime.plans import AnalysisPlan
from langgraph_langchain.schemas import EvidenceItem, Finding, VerificationResult


QUESTION = "Analyze the revenue trend and report the verified conclusion."


class RecordingExecutor:
    """Small worker that records whether V2 dispatched its executor type."""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        self.calls: list[str] = []

    def execute(self, request):
        self.calls.append(request.task.task_id)
        return build_execution_result(
            request,
            status="succeeded",
            tool_name=request.task.method,
            output_artifact_ids=[f"artifact_{self.kind}_{request.task.task_id}"],
            stdout_preview=f"{self.kind} executed",
        )


def _plan() -> AnalysisPlan:
    structured_step = RuntimePlanStep(
        objective="Run a deterministic structured analysis",
        method="structured_step",
        expected_outputs=["structured_result"],
    )
    react_step = RuntimePlanStep(
        objective="Run a bounded reasoning task",
        method="react_step",
        expected_outputs=["react_result"],
    )
    python_step = RuntimePlanStep(
        objective="Run isolated Python analysis",
        method="python_repl",
        expected_outputs=["python_result"],
    )
    return AnalysisPlan(
        goal=QUESTION,
        steps=[structured_step, react_step, python_step],
        require_confirmation=False,
    )


def _verifier(result):
    return [
        VerificationResult(
            run_id=result.run_id,
            task_id=result.task_id,
            execution_id=result.execution_id,
            check_type="custom",
            status="passed",
            passed=True,
            message=f"{result.tool_name} output is internally consistent",
        )
    ]


def _evidence_factory(result, verifications):
    verification = verifications[0]
    return EvidenceItem(
        verification_status="verified",
        verification_result_id=verification.verification_id,
        evidence_text=f"Verified output from {result.tool_name}: {result.stdout_preview}",
        source_fields=["revenue"],
        source_artifact_ids=list(result.output_artifact_ids),
        source_execution_ids=[result.execution_id],
        source_step_ids=[result.step_id or result.task_id],
        run_id=result.run_id,
    )


def test_runtime_v6_routes_three_executors_and_persists_lineage(tmp_path: Path):
    from langgraph_langchain.runtime.context import AnalysisRuntime

    workspace = tmp_path / "runtime-v6"
    workspace.mkdir()
    runtime = AnalysisRuntime(
        workspace_dir=workspace,
        session_id="v6-phase2",
        question=QUESTION,
    )
    runtime.initialize_plan(_plan())

    structured = RecordingExecutor("structured")
    react = RecordingExecutor("react")
    python = RecordingExecutor("python")
    controller = runtime.build_execution_controller(
        structured_executor=structured,
        react_executor=react,
        python_executor=python,
        verifier=_verifier,
        evidence_factory=_evidence_factory,
    )

    assert runtime.controller is controller
    assert controller.analysis_runtime is runtime
    assert controller.runner.executor is runtime.executor
    controller.graph.invoke({})

    assert structured.calls and react.calls and python.calls
    assert len(structured.calls) == 1
    assert len(react.calls) == 1
    assert len(python.calls) == 1
    assert [task.executor_type for task in runtime.scheduler.tasks] == [
        "structured",
        "react",
        "python",
    ]
    assert all(task.status == "succeeded" for task in runtime.scheduler.tasks)
    assert runtime.status == "completed"

    assert len(runtime.executions) == 3
    assert all(result.status == "succeeded" for result in runtime.executions)
    assert len(runtime.verifications) == 3
    assert runtime.verifications == runtime.verification_results
    assert len(runtime.evidence) == 3
    assert all(item.verification_status == "verified" for item in runtime.evidence)
    assert {item.source_step_ids[0] for item in runtime.evidence} == {
        task.task_id for task in runtime.scheduler.tasks
    }


def test_runtime_v6_state_is_restored_with_finding_provenance(tmp_path: Path):
    from langgraph_langchain.runtime.context import AnalysisRuntime

    workspace = tmp_path / "runtime-v6-restore"
    workspace.mkdir()
    first = AnalysisRuntime(
        workspace_dir=workspace,
        session_id="v6-phase2-restore",
        question=QUESTION,
    )
    first.initialize_plan(_plan())
    first.build_execution_controller(
        structured_executor=RecordingExecutor("structured"),
        react_executor=RecordingExecutor("react"),
        python_executor=RecordingExecutor("python"),
        verifier=_verifier,
        evidence_factory=_evidence_factory,
    )
    first.execute_all()

    verification = first.verification_results[0]
    evidence = next(
        item
        for item in first.evidence
        if item.verification_result_id == verification.verification_id
    )
    finding = Finding(
        finding_id="F001",
        statement="The verified runtime outputs support the requested conclusion.",
        evidence=[evidence],
        confidence_level="medium",
        evidence_level="B",
        category="trend",
        finding_type="trend",
        run_id=first.run.run_id,
        recorded_by_execution_id=evidence.source_execution_ids[0],
        recorded_by_step_id=evidence.source_step_ids[0],
        supported_by=[evidence.evidence_id],
    )
    first.record_finding(finding)

    restored = AnalysisRuntime(
        workspace_dir=workspace,
        session_id="v6-phase2-restore",
        question=QUESTION,
    )
    assert len(restored.executions) == 3
    assert len(restored.verification_results) == 3
    assert len(restored.evidence) == 3
    assert len(restored.findings) == 1

    # The report contract intentionally draws only from findings whose evidence
    # IDs exist in Runtime's verified lineage.
    verified_ids = {item.verification_id for item in restored.verification_results}
    evidence_ids = {
        item.evidence_id
        for item in restored.evidence
        if item.verification_status == "verified"
    }
    report_finding = Finding.model_validate(restored.findings[0])
    accepted_findings = [
        item
        for item in [report_finding]
        if item.evidence
        and all(source in evidence_ids for source in item.supported_by)
    ]
    report = "\n".join(
        f"- {item.statement}" for item in accepted_findings
    )
    assert "verified runtime outputs" in report
    assert accepted_findings[0].evidence[0].verification_result_id in verified_ids


def test_runtime_v6_business_failure_stops_without_evidence_path(tmp_path: Path):
    from langgraph_langchain.runtime.context import AnalysisRuntime

    class FailingExecutor:
        def execute(self, request):
            return build_execution_result(
                request,
                status="failed",
                tool_name=request.task.method,
                error={"type": "ToolExecutionError", "message": "analysis failed"},
            )

    workspace = tmp_path / "runtime-v6-failure"
    workspace.mkdir()
    runtime = AnalysisRuntime(
        workspace_dir=workspace,
        session_id="v6-phase2-failure",
        question=QUESTION,
    )
    plan = _plan()
    runtime.initialize_plan(plan)
    runtime.build_execution_controller(
        structured_executor=FailingExecutor(),
        react_executor=RecordingExecutor("react"),
        python_executor=RecordingExecutor("python"),
    )

    results = runtime.execute_all(stop_on_failure=True)
    assert len(results) == 1
    assert results[0].status == "failed"
    assert runtime.status == "failed"
    assert runtime.scheduler.tasks[0].status == "failed"
    assert runtime.scheduler.tasks[1].status == "pending"
    assert isinstance(RUNTIME_V2_FALLBACK_ENABLED, bool)

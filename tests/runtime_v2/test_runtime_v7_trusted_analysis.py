"""Runtime V7 trusted-analysis acceptance tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from langgraph_langchain.data.assets import ColumnSpec, DataAsset, SchemaSnapshot
from langgraph_langchain.execution.task_models import build_execution_result
from langgraph_langchain.runtime.context import AnalysisRuntime
from langgraph_langchain.runtime.finding_builder import FindingBuilder, FindingProvenanceError
from langgraph_langchain.runtime.models import ExecutionResult, RuntimePlanStep
from langgraph_langchain.runtime.plans import AnalysisPlan
from langgraph_langchain.runtime.report_validator import validate_report
from langgraph_langchain.runtime.verification_policy import VerificationPolicy
from langgraph_langchain.schemas import AnalysisTask, EvidenceItem, Finding, VerificationResult


QUESTION = "Report the verified revenue trend and its contribution."


class ContractExecutor:
    """Return a successful V2 execution for policy gate tests."""

    def __init__(self, value: float | None = None) -> None:
        self.value = value
        self.calls = 0

    def execute(self, request):
        self.calls += 1
        output_artifacts = [f"artifact_{request.task.task_id}"] if self.value is None else []
        return build_execution_result(
            request,
            status="succeeded",
            tool_name=request.task.method,
            output_artifact_ids=output_artifacts,
            stdout_preview="analysis completed",
        )


def _plan(*, method: str = "analyze_metric") -> AnalysisPlan:
    return AnalysisPlan(
        goal=QUESTION,
        steps=[
            RuntimePlanStep(
                objective="Analyze the metric",
                method=method,
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


def _execution(runtime: AnalysisRuntime, task, *, asset_id: str | None = None) -> ExecutionResult:
    return ExecutionResult(
        run_id=runtime.run.run_id,
        task_id=task.task_id,
        step_id=task.plan_step_id,
        tool_name=task.method,
        status="succeeded",
        input_asset_ids=[asset_id] if asset_id else list(task.input_asset_ids),
        stdout_preview="North revenue decreased by 10500",
    )


def _verified_lineage(runtime: AnalysisRuntime, task, execution: ExecutionResult, asset_id: str):
    verification = VerificationResult(
        run_id=runtime.run.run_id,
        task_id=task.task_id,
        step_id=execution.step_id,
        execution_id=execution.execution_id,
        check_type="numeric_consistency",
        status="passed",
        passed=True,
        expected=-10500,
        actual=-10500,
        message="Contribution matches component sum",
    )
    runtime.record_verification(verification)
    evidence = EvidenceItem(
        verification_status="verified",
        verification_result_id=verification.verification_id,
        evidence_text="North revenue declined by 10500 between 2026-01 and 2026-02",
        source_fields=["date", "department", "revenue"],
        source_execution_ids=[execution.execution_id],
        source_step_ids=[execution.step_id],
        source_asset_ids=[asset_id],
        stats={"change": -10500},
        calculation_method="sum(revenue) by department/month",
    )
    runtime.record_evidence(evidence)
    return verification, evidence


def test_verification_policy_numeric_gate_fails_the_task(tmp_path: Path):
    runtime = _runtime(tmp_path, "v7-numeric-failure")
    runtime.initialize_plan(_plan())
    task = runtime.scheduler.tasks[0]
    task.constraints["verification_contract"] = {
        "numeric_consistency": {"expected": -10500, "actual": -10000, "tolerance": 0.0},
    }
    executor = ContractExecutor()

    runtime.build_execution_controller(
        structured_executor=executor,
        verification_policy=VerificationPolicy(),
    )
    result = runtime.execute_next_task()

    assert result.status == "failed"
    assert result.error["type"] == "RuntimeVerificationError"
    assert "Values differ" in result.error["message"]
    assert task.status == "failed"
    assert runtime.status == "failed"
    assert runtime.evidence == []
    assert runtime.findings == []
    assert any(item.passed is False for item in result.verification_results)


def test_verification_policy_group_and_schema_checks(tmp_path: Path):
    task = AnalysisTask(
        session_id="v7-policy",
        question="Check contribution groups and schema",
        task_type="comparison",
        method="compare_groups",
        constraints={
            "verification_contract": {
                "group_consistency": {
                    "expected": {"North": -10500, "East": 6000},
                    "actual": {"North": -10500, "East": 6000},
                },
                "schema_consistency": {
                    "expected_fields": ["date", "department", "revenue"],
                    "actual_fields": ["date", "department", "revenue"],
                },
            }
        },
    )
    execution = ExecutionResult(
        run_id="run_v7",
        task_id=task.task_id,
        tool_name="compare_groups",
        status="succeeded",
    )

    policy = VerificationPolicy(
        required_by_task_type={
            "comparison": ("group_consistency", "schema_consistency"),
        }
    )
    checks = policy.verify(task, execution)
    group = next(item for item in checks if item.check_type == "group_consistency")
    schema = next(item for item in checks if item.check_type == "schema_consistency")
    assert group.passed is True
    assert schema.passed is True


def test_evidence_generation_failure_blocks_task_and_finding(tmp_path: Path):
    runtime = _runtime(tmp_path, "v7-evidence-failure")
    runtime.initialize_plan(_plan(method="analyze_metric"))

    def failing_evidence_factory(result, verifications):
        raise ValueError("evidence registry is unavailable")

    artifact_path = runtime.workspace_dir / "analysis-result.csv"
    artifact_path.write_text("department,change\nNorth,-10500\n", encoding="utf-8")
    artifact = runtime.register_artifact(artifact_path, created_by_tool="analyze_metric")
    artifact_id = artifact.artifact_id

    class RegisteredArtifactExecutor:
        def execute(self, request):
            return build_execution_result(
                request,
                status="succeeded",
                tool_name=request.task.method,
                output_artifact_ids=[artifact_id],
                stdout_preview="analysis completed",
            )

    runtime.build_execution_controller(
        structured_executor=RegisteredArtifactExecutor(),
        verification_policy=VerificationPolicy(),
        evidence_factory=failing_evidence_factory,
    )
    result = runtime.execute_next_task()

    assert result.status == "failed"
    assert "Evidence generation failed" in result.error["message"]
    assert runtime.status == "failed"
    assert runtime.evidence == []
    assert runtime.findings == []


def test_finding_builder_accepts_only_complete_verified_lineage(tmp_path: Path):
    runtime = _runtime(tmp_path, "v7-provenance")
    runtime.initialize_plan(_plan(method="analyze_metric"))
    task = runtime.scheduler.tasks[0]

    source = tmp_path / "v7-provenance" / "source.csv"
    source.write_text("date,department,revenue\n2026-01,North,24000\n", encoding="utf-8")
    asset = DataAsset(
        name="source.csv",
        source_type="csv",
        location=str(source),
        schema_snapshot=SchemaSnapshot(
            row_count=1,
            column_count=3,
            columns=[
                ColumnSpec(name="date", dtype="object"),
                ColumnSpec(name="department", dtype="object"),
                ColumnSpec(name="revenue", dtype="int64"),
            ],
        ),
    )
    runtime.assets[asset.asset_id] = asset

    runtime.start_task(task.task_id)
    execution = _execution(runtime, task, asset_id=asset.asset_id)
    runtime.record_execution_result(execution)
    runtime.complete_task(task.task_id)
    verification, evidence = _verified_lineage(runtime, task, execution, asset.asset_id)

    finding = FindingBuilder(runtime).build_from_verified_evidence(
        task=task,
        execution=execution,
        evidence=evidence,
        statement="North revenue declined by 10500.",
        finding_id="F001",
        confidence_level="medium",
        evidence_level="A",
        category="contribution",
    )
    assert finding.supported_by == [evidence.evidence_id]
    assert runtime.record_verified_finding(finding)["finding_id"] == "F001"

    unsupported = Finding(
        finding_id="F002",
        statement="An unsupported claim.",
        evidence=[],
        supported_by=[],
    )
    with pytest.raises(FindingProvenanceError):
        runtime.record_verified_finding(unsupported)

    unverified = Finding(
        finding_id="F003",
        statement="Another unsupported claim.",
        evidence=[EvidenceItem(evidence_text="claim")],
        supported_by=[],
    )
    with pytest.raises(FindingProvenanceError):
        runtime.record_verified_finding(unverified)

    restored = AnalysisRuntime(
        workspace_dir=tmp_path / "v7-provenance",
        session_id="v7-provenance",
        question=QUESTION,
    )
    assert restored.executions[0].execution_id == execution.execution_id
    assert restored.verification_results[0].verification_id == verification.verification_id
    assert restored.evidence[0].evidence_id == evidence.evidence_id
    assert restored.findings[0]["finding_id"] == "F001"


def test_report_validator_rejects_unverified_and_unsupported_numbers():
    evidence = EvidenceItem(
        verification_status="verified",
        verification_result_id="verify_v7",
        evidence_text="North revenue declined by 10500 in 2026-02.",
        source_fields=["revenue"],
        source_execution_ids=["exec_v7"],
        source_asset_ids=["asset_v7"],
        stats={"change": -10500},
        calculation_method="sum(revenue) by department/month",
    )
    finding = Finding(
        finding_id="F001",
        statement="North revenue declined by 10500.",
        evidence=[evidence],
        supported_by=[evidence.evidence_id],
    )
    valid = validate_report(
        "## Key Findings\n- North revenue declined by 10500.",
        [finding],
        [evidence],
    )
    assert valid.is_valid is True

    signed_narrative = validate_report(
        "## Key Findings\n- North revenue declined by 10500.",
        [finding],
        [evidence],
    )
    assert signed_narrative.is_valid is True

    unknown_number = validate_report(
        "## Key Findings\n- North revenue declined by 10500 and grew by 99999.",
        [finding],
        [evidence],
    )
    assert unknown_number.is_valid is False
    assert "99999" in unknown_number.unsupported_numbers

    unverified_evidence = evidence.model_copy(
        update={"verification_status": "unverified", "verification_result_id": None}
    )
    unverified = validate_report(
        "## Key Findings\n- North revenue declined by 10500.",
        [finding],
        [unverified_evidence],
    )
    assert unverified.is_valid is False
    assert any("unverified evidence" in error for error in unverified.errors)

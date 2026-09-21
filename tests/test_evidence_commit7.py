from __future__ import annotations

from pathlib import Path

import pytest

from langgraph_langchain.evidence import EvidenceCollectionError, EvidenceCollector
from langgraph_langchain.runtime.models import ExecutionResult, RuntimeArtifact
from langgraph_langchain.schemas import EvidenceItem, VerificationResult


def make_execution(
    *,
    status: str = "succeeded",
    output_artifact_ids: list[str] | None = None,
    execution_id: str = "execution_1",
    run_id: str = "run_1",
    task_id: str = "task_1",
    step_id: str = "step_1",
) -> ExecutionResult:
    return ExecutionResult(
        execution_id=execution_id,
        run_id=run_id,
        task_id=task_id,
        step_id=step_id,
        tool_name="compare_groups",
        status=status,  # type: ignore[arg-type]
        input_asset_ids=["asset_1"],
        output_artifact_ids=output_artifact_ids or [],
    )


def make_verification(
    *,
    status: str = "passed",
    passed: bool = True,
    verification_id: str = "verification_1",
    execution_id: str = "execution_1",
    run_id: str = "run_1",
    task_id: str = "task_1",
    step_id: str = "step_1",
) -> VerificationResult:
    return VerificationResult(
        verification_id=verification_id,
        run_id=run_id,
        task_id=task_id,
        step_id=step_id,
        execution_id=execution_id,
        check_type="aggregation_consistency",
        status=status,  # type: ignore[arg-type]
        passed=passed,
        expected=100,
        actual=100,
        message="totals match",
    )


def make_artifact(tmp_path: Path, *, artifact_id: str = "artifact_1") -> RuntimeArtifact:
    path = tmp_path / "result.csv"
    path.write_text("group,value\nA,1\n", encoding="utf-8")
    return RuntimeArtifact(
        artifact_id=artifact_id,
        artifact_type="table",
        name="result.csv",
        path=str(path),
        relative_path="result.csv",
        url="/workspace/files/result.csv",
        execution_id="execution_1",
        step_id="step_1",
    )


def test_collector_creates_verified_evidence_from_passed_execution(tmp_path: Path):
    execution = make_execution(output_artifact_ids=["artifact_1"])
    verification = make_verification()
    artifact = make_artifact(tmp_path)
    collector = EvidenceCollector()

    evidence = collector.collect(
        execution=execution,
        verification_results=[verification],
        artifacts={"artifact_1": artifact},
        evidence_text="North revenue decreased by 10%.",
        source_fields=["department", "revenue"],
        group_dimension="department",
        stats={"north_change": -0.10},
    )

    assert isinstance(evidence, EvidenceItem)
    assert evidence.verification_status == "verified"
    assert evidence.verification_result_id == "verification_1"
    assert evidence.evidence_text == "North revenue decreased by 10%."
    assert evidence.source_execution_ids == ["execution_1"]
    assert evidence.source_step_ids == ["step_1"]
    assert evidence.source_artifact_ids == ["artifact_1"]
    assert evidence.source_artifacts == ["result.csv"]
    assert evidence.source_asset_ids == ["asset_1"]
    assert evidence.group_dimension == "department"
    assert evidence.stats == {"north_change": -0.10}
    assert EvidenceItem.model_validate(evidence.model_dump()) == evidence


def test_collector_creates_deterministic_text_without_custom_text():
    execution = make_execution(output_artifact_ids=[])
    verification = make_verification()
    evidence = EvidenceCollector().collect(
        execution=execution,
        verification_results=[verification],
    )

    assert evidence.evidence_text == (
        "compare_groups execution execution_1 succeeded and passed "
        "1 verification check(s)."
    )
    assert evidence.calculation_method == "runtime_verified:compare_groups"


def test_collector_rejects_failed_execution():
    execution = make_execution(status="failed")
    verification = make_verification()

    with pytest.raises(EvidenceCollectionError, match="expected 'succeeded'"):
        EvidenceCollector().collect(
            execution=execution,
            verification_results=[verification],
        )


def test_collector_rejects_missing_verification():
    execution = make_execution()

    with pytest.raises(EvidenceCollectionError, match="No VerificationResult is related"):
        EvidenceCollector().collect(execution=execution, verification_results=[])


def test_collector_rejects_unrelated_verification():
    execution = make_execution()
    verification = make_verification(execution_id="execution_other")

    with pytest.raises(EvidenceCollectionError, match="No VerificationResult is related"):
        EvidenceCollector().collect(
            execution=execution,
            verification_results=[verification],
        )


def test_collector_rejects_failed_verification():
    execution = make_execution()
    verification = make_verification(status="failed", passed=False)

    with pytest.raises(EvidenceCollectionError, match="verification checks failed"):
        EvidenceCollector().collect(
            execution=execution,
            verification_results=[verification],
        )


def test_collector_rejects_missing_artifact(tmp_path: Path):
    execution = make_execution(output_artifact_ids=["artifact_missing"])
    verification = make_verification()

    with pytest.raises(EvidenceCollectionError, match="is not registered"):
        EvidenceCollector().collect(
            execution=execution,
            verification_results=[verification],
            artifacts={"artifact_1": make_artifact(tmp_path)},
        )


def test_collector_rejects_nonexistent_artifact(tmp_path: Path):
    execution = make_execution(output_artifact_ids=["artifact_1"])
    verification = make_verification()
    missing_artifact = make_artifact(tmp_path)
    missing_artifact.path = str(tmp_path / "missing.csv")

    with pytest.raises(EvidenceCollectionError, match="does not exist"):
        EvidenceCollector().collect(
            execution=execution,
            verification_results=[verification],
            artifacts={"artifact_1": missing_artifact},
        )


def test_collector_rejects_run_id_mismatch():
    execution = make_execution()
    verification = make_verification(run_id="run_other")

    with pytest.raises(EvidenceCollectionError, match="belongs to run"):
        EvidenceCollector().collect(
            execution=execution,
            verification_results=[verification],
        )


def test_collector_rejects_duplicate_verification_ids():
    execution = make_execution()
    verification = make_verification()
    duplicate = make_verification()

    with pytest.raises(EvidenceCollectionError, match="Duplicate verification_id"):
        EvidenceCollector().collect(
            execution=execution,
            verification_results=[verification, duplicate],
        )


def test_collector_rejects_blank_custom_text():
    execution = make_execution()
    verification = make_verification()

    with pytest.raises(EvidenceCollectionError, match="evidence_text cannot be blank"):
        EvidenceCollector().collect(
            execution=execution,
            verification_results=[verification],
            evidence_text="   ",
        )

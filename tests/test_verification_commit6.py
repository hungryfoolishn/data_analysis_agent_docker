from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from langgraph_langchain.runtime.models import ExecutionResult, RuntimeArtifact
from langgraph_langchain.schemas import EvidenceItem, VerificationResult
from langgraph_langchain.verification import (
    verify_aggregation_consistency,
    verify_artifact_existence,
    verify_evidence_existence,
    verify_execution_result,
    verify_numeric_consistency,
    verify_time_consistency,
)


def test_numeric_consistency_passes_within_tolerance():
    result = verify_numeric_consistency(
        expected=100.0,
        actual=100.02,
        tolerance=0.05,
        context={"run_id": "run_1", "task_id": "task_1"},
    )

    assert isinstance(result, VerificationResult)
    assert result.passed is True
    assert result.status == "passed"
    assert result.details["difference"] == pytest.approx(0.02)
    assert result.run_id == "run_1"
    assert VerificationResult.model_validate(result.model_dump()) == result


def test_numeric_consistency_fails_outside_tolerance():
    result = verify_numeric_consistency(expected=100, actual=90, tolerance=1)
    assert result.passed is False
    assert result.status == "failed"
    assert result.details["difference"] == 10


@pytest.mark.parametrize(("expected", "actual", "tolerance"), [(1, "x", 0), (True, 1, 0), (1, 2, -1)])
def test_numeric_consistency_rejects_invalid_inputs(expected, actual, tolerance):
    result = verify_numeric_consistency(expected=expected, actual=actual, tolerance=tolerance)
    assert result.passed is False


def test_time_consistency_accepts_iso_strings_and_normalises_timezones():
    result = verify_time_consistency(
        start_time="2026-09-20T08:00:00+08:00",
        end_time="2026-09-20T09:00:00+08:00",
    )
    assert result.passed is True


def test_time_consistency_fails_when_end_precedes_start():
    result = verify_time_consistency(
        start_time="2026-09-20T10:00:00+00:00",
        end_time="2026-09-20T09:00:00+00:00",
    )
    assert result.passed is False
    assert result.message == "end_time is before start_time"


def test_time_consistency_rejects_invalid_datetime():
    result = verify_time_consistency(start_time="not-a-time", end_time="2026-09-20")
    assert result.passed is False


def test_aggregation_consistency_supports_models_and_mappings():
    result = verify_aggregation_consistency(
        total=100,
        components=[
            {"value": 60},
            {"value": 40},
        ],
        tolerance=0.001,
    )
    assert result.passed is True
    assert result.details["component_count"] == 2


def test_aggregation_consistency_fails_mismatched_components():
    result = verify_aggregation_consistency(
        total=100,
        components=[{"value": 60}, {"value": 30}],
        tolerance=0,
    )
    assert result.passed is False
    assert result.details["difference"] == 10


def test_artifact_existence_passes_for_model_file(tmp_path: Path):
    path = tmp_path / "result.csv"
    path.write_text("group,value\nA,1\n", encoding="utf-8")
    artifact = RuntimeArtifact(
        artifact_id="artifact_1",
        artifact_type="table",
        name="result.csv",
        path=str(path),
        relative_path="result.csv",
        url="/workspace/files/result.csv",
    )

    result = verify_artifact_existence(artifact)
    assert result.passed is True
    assert result.artifact_id == "artifact_1"


def test_artifact_existence_resolves_relative_path(tmp_path: Path):
    path = tmp_path / "chart.png"
    path.write_bytes(b"fake-png")
    artifact = {"artifact_id": "artifact_2", "path": "chart.png"}

    result = verify_artifact_existence(artifact, workspace_dir=tmp_path)
    assert result.passed is True


def test_artifact_existence_fails_for_missing_file(tmp_path: Path):
    artifact = {"artifact_id": "artifact_3", "path": str(tmp_path / "missing.csv")}
    result = verify_artifact_existence(artifact)
    assert result.passed is False


def test_evidence_existence_passes_for_valid_item():
    evidence = EvidenceItem(
        evidence_id="evidence_1",
        evidence_text="North revenue decreased by 10%.",
        source_artifact_ids=["artifact_1"],
        source_execution_ids=["execution_1"],
    )
    result = verify_evidence_existence(evidence)
    assert result.passed is True
    assert result.details["source_artifact_count"] == 1


def test_evidence_existence_fails_for_blank_text():
    result = verify_evidence_existence({"evidence_id": "evidence_2", "evidence_text": "   "})
    assert result.passed is False


def test_execution_result_verifies_registered_artifacts(tmp_path: Path):
    path = tmp_path / "result.csv"
    path.write_text("group,value\nA,1\n", encoding="utf-8")
    artifact = RuntimeArtifact(
        artifact_id="artifact_1",
        artifact_type="table",
        name="result.csv",
        path=str(path),
        relative_path="result.csv",
        url="/workspace/files/result.csv",
    )
    execution = ExecutionResult(
        run_id="run_1",
        task_id="task_1",
        step_id="step_1",
        tool_name="compare_groups",
        status="succeeded",
        output_artifact_ids=["artifact_1"],
    )

    checks = verify_execution_result(
        execution,
        artifacts={"artifact_1": artifact},
        context={"run_id": "run_1", "task_id": "task_1"},
    )

    assert len(checks) == 1
    assert checks[0].passed is True
    assert checks[0].artifact_id == "artifact_1"


def test_execution_result_fails_for_missing_artifact_and_failed_status():
    execution = ExecutionResult(
        run_id="run_1",
        task_id="task_1",
        step_id="step_1",
        tool_name="python_repl",
        status="failed",
        output_artifact_ids=["artifact_missing"],
    )

    checks = verify_execution_result(execution, artifacts={})

    assert len(checks) == 2
    assert all(check.passed is False for check in checks)
    assert {check.check_type for check in checks} == {"custom", "artifact_existence"}

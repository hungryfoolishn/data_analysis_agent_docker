"""Tests for the first DeepAnalyze platform-runtime slice."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from langgraph_langchain.data.assets import DataAsset, SchemaSnapshot, hash_file
from langgraph_langchain.runtime.context import (
    AnalysisRuntime,
    SessionExecutionRecorder,
    register_session_artifact,
)
from langgraph_langchain.runtime.events import (
    RuntimeStreamContextReader,
    build_analysis_event,
    load_stream_context,
)


def test_schema_snapshot_observes_dataframe_shape_and_columns():
    dataframe = pd.DataFrame(
        {
            "order_id": [1, 2, 3],
            "amount": [10.0, None, 30.0],
            "ordered_at": pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03"]),
        }
    )

    snapshot = SchemaSnapshot.from_dataframe(dataframe)

    assert snapshot.row_count == 3
    assert snapshot.column_count == 3
    assert snapshot.candidate_keys == ["order_id", "ordered_at"]
    assert snapshot.time_columns == ["ordered_at"]
    amount = next(column for column in snapshot.columns if column.name == "amount")
    assert amount.nullable is True
    assert amount.null_count == 1
    assert amount.unique_count == 2


def test_data_asset_records_source_hash_and_schema(tmp_path):
    source = tmp_path / "orders.csv"
    source.write_text("order_id,amount\n1,10\n2,20\n", encoding="utf-8")
    dataframe = pd.read_csv(source)

    asset = DataAsset.from_dataframe(dataframe=dataframe, source_path=source)

    assert asset.name == "orders.csv"
    assert asset.source_type == "csv"
    assert asset.content_hash == hash_file(source)
    assert asset.schema_snapshot.row_count == 2


def test_runtime_persists_and_restores_metadata(tmp_path):
    source = tmp_path / "orders.csv"
    source.write_text("order_id,amount\n1,10\n2,20\n", encoding="utf-8")
    dataframe = pd.read_csv(source)
    runtime = AnalysisRuntime(
        workspace_dir=tmp_path,
        session_id="session_test",
        question="summarize orders",
    )

    asset = runtime.register_dataframe(dataframe=dataframe, source_path=source)
    execution = runtime.record_execution(
        tool_name="load_data",
        status="succeeded",
        input_asset_ids=[asset.asset_id],
        stdout_preview="2 rows",
        duration_ms=12.5,
    )

    restored = AnalysisRuntime(
        workspace_dir=tmp_path,
        session_id="session_test",
        question="summarize orders",
    )
    raw = json.loads((tmp_path / ".analysis_runtime.json").read_text(encoding="utf-8"))

    assert restored.task.task_id == runtime.task.task_id
    assert restored.run.run_id == runtime.run.run_id
    assert asset.asset_id in restored.assets
    assert restored.executions[0].execution_id == execution.execution_id
    assert raw["version"] == 1


def test_runtime_deduplicates_same_source_asset(tmp_path):
    source = tmp_path / "orders.csv"
    source.write_text("order_id,amount\n1,10\n2,20\n", encoding="utf-8")
    dataframe = pd.read_csv(source)
    runtime = AnalysisRuntime(
        workspace_dir=tmp_path,
        session_id="session_test",
        question="summarize orders",
    )

    first = runtime.register_dataframe(dataframe=dataframe, source_path=source)
    second = runtime.register_dataframe(dataframe=dataframe, source_path=source)

    assert second.asset_id == first.asset_id
    assert runtime.task.input_asset_ids == [first.asset_id]
    assert len(runtime.assets) == 1


def test_artifact_registration_keeps_legacy_frontend_fields(tmp_path):
    workspace = tmp_path / "session_test"
    workspace.mkdir()
    chart = workspace / "trend.png"
    chart.write_bytes(b"fake-png")
    runtime = AnalysisRuntime(
        workspace_dir=workspace,
        session_id="session_test",
        question="find trend",
    )
    session = SimpleNamespace(
        workspace_dir=workspace,
        analysis_runtime=runtime,
        new_artifacts=[],
    )

    metadata = register_session_artifact(
        session,
        chart,
        created_by_tool="python_repl",
        execution_id="exec_test",
    )

    assert metadata["artifact_type"] == "chart"
    assert metadata["created_by_tool"] == "python_repl"
    assert metadata["execution_id"] == "exec_test"
    assert metadata["name"] == "trend.png"
    assert metadata["url"].endswith("session_test\\trend.png") or metadata["url"].endswith(
        "session_test/trend.png"
    )
    assert session.new_artifacts == [metadata]


def test_artifact_registration_falls_back_for_legacy_session(tmp_path):
    workspace = tmp_path / "session_test"
    workspace.mkdir()
    table = workspace / "summary.csv"
    table.write_text("metric,value\norders,2\n", encoding="utf-8")
    session = SimpleNamespace(workspace_dir=workspace, new_artifacts=[])

    metadata = register_session_artifact(
        session,
        table,
        created_by_tool="python_repl",
    )

    assert set(metadata) == {"name", "path", "relative_path", "url"}
    assert session.new_artifacts == [metadata]


def test_execution_recorder_is_idempotent_and_links_artifacts(tmp_path):
    workspace = tmp_path / "session_test"
    workspace.mkdir()
    runtime = AnalysisRuntime(
        workspace_dir=workspace,
        session_id="session_test",
        question="create chart",
    )
    step = runtime.start_step(
        objective="Create a chart",
        method="python_repl",
        expected_outputs=["chart"],
    )
    session = SimpleNamespace(
        workspace_dir=workspace,
        analysis_runtime=runtime,
        new_artifacts=[],
        current_runtime_step_id=step.step_id,
    )
    chart = workspace / "chart.png"
    chart.write_bytes(b"fake-png")
    recorder = SessionExecutionRecorder(
        session,
        tool_name="python_repl",
        code_or_query="print('done')",
    )

    metadata = recorder.register_artifact(chart)
    first = recorder.succeed("done")
    second = recorder.fail("must be ignored")

    assert first is not None
    assert second is None
    assert len(runtime.executions) == 1
    assert runtime.executions[0].output_artifact_ids == [metadata["artifact_id"]]
    assert runtime.executions[0].step_id == step.step_id
    assert metadata["execution_id"] == runtime.executions[0].execution_id
    assert metadata["step_id"] == step.step_id


def test_runtime_step_lifecycle_persists_and_restores(tmp_path):
    runtime = AnalysisRuntime(
        workspace_dir=tmp_path,
        session_id="session_test",
        question="inspect quality",
    )

    succeeded = runtime.start_step(
        objective="Load source data",
        method="load_data",
        expected_outputs=["schema_snapshot"],
    )
    runtime.complete_step(succeeded.step_id)
    failed = runtime.start_step(
        objective="Run computation",
        method="python_repl",
        depends_on=[succeeded.step_id],
    )
    runtime.complete_step(failed.step_id, succeeded=False, error="invalid column")

    restored = AnalysisRuntime(
        workspace_dir=tmp_path,
        session_id="session_test",
        question="inspect quality",
    )

    assert restored.run.current_step_id is None
    assert [step.status for step in restored.run.steps] == ["succeeded", "failed"]
    assert restored.run.steps[0].started_at is not None
    assert restored.run.steps[0].completed_at is not None
    assert restored.run.steps[1].error == "invalid column"
    assert restored.run.steps[1].depends_on == [succeeded.step_id]


def test_report_validation_revision_is_not_a_failed_step(tmp_path):
    runtime = AnalysisRuntime(
        workspace_dir=tmp_path,
        session_id="session_revision",
        question="Create report",
    )
    step = runtime.start_step(
        objective="Validate report",
        method="finish_report",
    )

    runtime.complete_step(
        step.step_id,
        status="needs_revision",
        error="missing Data Context",
    )

    assert runtime.run.steps[0].status == "needs_revision"
    assert runtime.run.steps[0].error == "missing Data Context"
    assert runtime.run.status == "running"


def test_runtime_persists_findings_with_run_snapshot(tmp_path):
    from langgraph_langchain.schemas import EvidenceItem, Finding

    runtime = AnalysisRuntime(
        workspace_dir=tmp_path,
        session_id="session_findings",
        question="Find salary pattern",
    )
    finding = Finding(
        finding_id="F001",
        statement="Mean salary is 15",
        evidence=[EvidenceItem(evidence_text="Observed mean=15")],
        run_id=runtime.run.run_id,
    )
    runtime.record_finding(finding)

    restored = AnalysisRuntime(
        workspace_dir=tmp_path,
        session_id="session_findings",
        question="Find salary pattern",
    )

    assert restored.findings[0]["finding_id"] == "F001"
    assert restored.snapshot()["findings"][0]["run_id"] == runtime.run.run_id


def test_runtime_persists_metric_definitions_and_assumptions(tmp_path):
    from langgraph_langchain.schemas import AnalysisAssumption, MetricDefinition

    runtime = AnalysisRuntime(
        workspace_dir=tmp_path,
        session_id="session_context",
        question="Define salary metrics",
    )
    runtime.record_metric_definition(MetricDefinition(
        metric_name="mean_salary",
        definition_text="AVG(salary)",
        dedup_rule="deduplicate by employee id",
    ))
    runtime.record_assumption(AnalysisAssumption(
        assumption_text="salary uses one currency",
        risk_level="medium",
    ))

    restored = AnalysisRuntime(
        workspace_dir=tmp_path,
        session_id="session_context",
        question="Define salary metrics",
    )

    assert restored.metric_definitions[0]["metric_name"] == "mean_salary"
    assert restored.assumptions[0]["assumption_text"] == "salary uses one currency"


def test_terminal_run_status_closes_running_steps(tmp_path):
    runtime = AnalysisRuntime(
        workspace_dir=tmp_path,
        session_id="session_test",
        question="cancel me",
    )
    step = runtime.start_step(objective="Long computation", method="python_repl")

    runtime.set_run_status("cancelled", error="client disconnected")

    assert runtime.run.status == "cancelled"
    assert runtime.run.current_step_id is None
    assert runtime.run.steps[0].step_id == step.step_id
    assert runtime.run.steps[0].status == "failed"
    assert runtime.run.steps[0].error == "client disconnected"


def test_stream_event_metadata_is_additive_and_restorable(tmp_path):
    runtime = AnalysisRuntime(
        workspace_dir=tmp_path,
        session_id="session_test",
        question="create chart",
    )
    step = runtime.start_step(objective="Create chart", method="python_repl")
    context = load_stream_context(tmp_path)
    event = build_analysis_event(
        session_id="session_test",
        stream_context=context,
        text="chart created",
        artifacts=[
            {
                "artifact_id": "artifact_test",
                "execution_id": "exec_test",
                "step_id": step.step_id,
                "name": "chart.png",
            }
        ],
    )

    assert context == {
        "task_id": runtime.task.task_id,
        "run_id": runtime.run.run_id,
        "step_id": step.step_id,
        "run_status": "running",
        "plan_status": "active",
        "plan_version": 1,
        "step": step.model_dump(mode="json"),
        "semantic_provider": None,
        "semantic_context_version": None,
    }
    assert event["type"] == "artifacts_created"
    assert event["artifact_ids"] == ["artifact_test"]
    assert event["execution_ids"] == ["exec_test"]
    assert event["step_id"] == step.step_id
    assert event["run_status"] == "running"
    assert event["step"]["method"] == "python_repl"
    assert event["step_ids"] == [step.step_id]
    assert event["has_content"] is True


def test_stream_context_reader_refreshes_after_runtime_transition(tmp_path):
    runtime = AnalysisRuntime(
        workspace_dir=tmp_path,
        session_id="session_test",
        question="track steps",
    )
    reader = RuntimeStreamContextReader(tmp_path)
    initial = reader.read()

    step = runtime.start_step(objective="Load data", method="load_data")
    running = reader.read()
    runtime.complete_step(step.step_id)
    completed = reader.read()

    assert initial["step_id"] is None
    assert running["step_id"] == step.step_id
    assert completed["step_id"] == step.step_id

"""Persistent run history, retry lineage, and observability tests."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from langgraph_langchain.runtime.context import AnalysisRuntime
from langgraph_langchain.runtime.history import RunHistoryStore


def _failed_run(workspace, *, context=None):
    runtime = AnalysisRuntime(
        workspace_dir=workspace,
        session_id=workspace.name,
        question="Why did revenue decline?",
        external_context=context,
    )
    step = runtime.start_step(
        objective="Compute revenue contribution",
        method="python_repl",
        expected_outputs=["contribution_table"],
    )
    runtime.record_execution(
        tool_name="python_repl",
        status="failed",
        step_id=step.step_id,
        error={"type": "KeyError", "message": "revenue"},
    )
    runtime.complete_step(step.step_id, succeeded=False, error="missing revenue column")
    runtime.set_run_status("failed", error="missing revenue column")
    return runtime, step


def test_run_archive_survives_current_snapshot_replacement(tmp_path):
    workspace = tmp_path / "session_history"
    parent, _ = _failed_run(workspace)

    replacement = AnalysisRuntime(
        workspace_dir=workspace,
        session_id=workspace.name,
        question="A different task",
    )
    history = RunHistoryStore(tmp_path)

    assert (workspace / ".analysis_runs" / f"{parent.run.run_id}.json").exists()
    assert history.get_run(parent.run.run_id)["run"]["status"] == "failed"
    assert {item["run_id"] for item in history.list_runs(session_id=workspace.name)} == {
        parent.run.run_id,
        replacement.run.run_id,
    }


def test_retry_run_preserves_task_and_semantic_context_with_lineage(tmp_path):
    workspace = tmp_path / "session_retry"
    context = {
        "contract_version": "1.0",
        "provider": "mock",
        "context_version": "sales-v3",
    }
    parent, failed_step = _failed_run(workspace, context=context)

    retry = AnalysisRuntime(
        workspace_dir=workspace,
        session_id=workspace.name,
        question=parent.task.question,
        external_context=context,
        force_new_run=True,
        parent_run_id=parent.run.run_id,
        retry_of_step_id=failed_step.step_id,
    )

    assert retry.task.task_id == parent.task.task_id
    assert retry.task.external_context == context
    assert retry.run.parent_run_id == parent.run.run_id
    assert retry.run.retry_of_step_id == failed_step.step_id
    assert retry.run.attempt == 2
    assert retry.run.steps == []
    assert retry.executions == []
    assert retry.artifacts == {}
    assert RunHistoryStore(tmp_path).get_run(parent.run.run_id)["run"]["status"] == "failed"


def test_retry_rejects_step_from_another_run(tmp_path):
    workspace = tmp_path / "session_invalid_retry"
    parent, _ = _failed_run(workspace)

    with pytest.raises(ValueError, match="does not belong"):
        AnalysisRuntime(
            workspace_dir=workspace,
            session_id=workspace.name,
            question=parent.task.question,
            force_new_run=True,
            parent_run_id=parent.run.run_id,
            retry_of_step_id="step_unknown",
        )


def test_history_metrics_aggregate_runs_steps_retries_and_tools(tmp_path):
    workspace = tmp_path / "session_metrics"
    parent, failed_step = _failed_run(workspace)
    retry = AnalysisRuntime(
        workspace_dir=workspace,
        session_id=workspace.name,
        question=parent.task.question,
        force_new_run=True,
        parent_run_id=parent.run.run_id,
        retry_of_step_id=failed_step.step_id,
    )
    retried_step = retry.start_step(
        objective=failed_step.objective,
        method=failed_step.method,
    )
    retry.complete_step(retried_step.step_id)
    retry.set_run_status("completed")

    metrics = RunHistoryStore(tmp_path).metrics()

    assert metrics["total_runs"] == 2
    assert metrics["failed_runs"] == 1
    assert metrics["completed_runs"] == 1
    assert metrics["retry_runs"] == 1
    assert metrics["successful_retry_runs"] == 1
    assert metrics["retry_success_rate"] == 1.0
    assert metrics["total_steps"] == 2
    assert metrics["failed_steps"] == 1
    assert metrics["failure_counts_by_method"] == {"python_repl": 1}
    assert metrics["failure_counts_by_tool"] == {"python_repl": 1}


def test_history_metrics_separate_report_revisions_from_failures(tmp_path):
    workspace = tmp_path / "session_revision_metrics"
    runtime = AnalysisRuntime(
        workspace_dir=workspace,
        session_id=workspace.name,
        question="Create report",
    )
    step = runtime.start_step(objective="Validate report", method="finish_report")
    runtime.complete_step(
        step.step_id,
        status="needs_revision",
        error="missing time range",
    )
    runtime.set_run_status("completed")

    metrics = RunHistoryStore(tmp_path).metrics()

    assert metrics["revision_steps"] == 1
    assert metrics["failed_steps"] == 0
    assert metrics["revision_counts_by_method"] == {"finish_report": 1}
    assert metrics["failure_counts_by_method"] == {}


def test_history_normalizes_legacy_report_rejections_without_rewriting_snapshot(tmp_path):
    workspace = tmp_path / "session_legacy_revision"
    runtime = AnalysisRuntime(
        workspace_dir=workspace,
        session_id=workspace.name,
        question="Create report",
    )
    step = runtime.start_step(objective="Validate report", method="finish_report")
    rejection = "REPORT REJECTED. missing Data Context"
    runtime.record_execution(
        tool_name="finish_report",
        status="failed",
        step_id=step.step_id,
        error={"type": "ReportValidationError", "message": rejection},
    )
    runtime.complete_step(step.step_id, succeeded=False, error=rejection)
    runtime.set_run_status("completed")

    snapshot_path = workspace / ".analysis_runs" / f"{runtime.run.run_id}.json"
    original = snapshot_path.read_text(encoding="utf-8")
    loaded = RunHistoryStore(tmp_path).get_run(runtime.run.run_id)

    assert loaded["run"]["steps"][0]["status"] == "needs_revision"
    assert loaded["executions"][0]["status"] == "needs_revision"
    assert loaded["executions"][0]["error"]["type"] == "ReportValidationFeedback"
    assert snapshot_path.read_text(encoding="utf-8") == original


@pytest.mark.asyncio
async def test_analysis_history_query_functions(monkeypatch, tmp_path):
    from langgraph_langchain import api_server_langgraph as api

    workspace = tmp_path / "session_api_history"
    runtime, _ = _failed_run(workspace)
    monkeypatch.setattr(api, "WORKSPACE_DIR", tmp_path)

    detail = await api.get_analysis_run(runtime.run.run_id)
    artifacts = await api.get_analysis_run_artifacts(runtime.run.run_id)
    metrics = await api.get_analysis_metrics()

    assert detail["task"]["session_id"] == workspace.name
    assert artifacts == {"run_id": runtime.run.run_id, "artifacts": [], "count": 0}
    assert metrics["total_runs"] == 1


@pytest.mark.asyncio
async def test_retry_api_rejects_non_failed_step(monkeypatch, tmp_path):
    from langgraph_langchain import api_server_langgraph as api

    workspace = tmp_path / "session_retry_conflict"
    runtime = AnalysisRuntime(
        workspace_dir=workspace,
        session_id=workspace.name,
        question="Summarize revenue",
    )
    step = runtime.start_step(objective="Load data", method="load_data")
    runtime.complete_step(step.step_id)
    monkeypatch.setattr(api, "WORKSPACE_DIR", tmp_path)

    with pytest.raises(HTTPException) as exc_info:
        await api.retry_analysis_step(runtime.run.run_id, step.step_id)

    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_retry_api_starts_new_linked_run_and_preserves_context(monkeypatch, tmp_path):
    from langgraph_langchain import api_server_langgraph as api

    workspace = tmp_path / "session_retry_api"
    context = {
        "contract_version": "1.0",
        "provider": "mock",
        "context_version": "finance-v2",
    }
    parent, failed_step = _failed_run(workspace, context=context)
    source = workspace / "revenue.csv"
    source.write_text("month,revenue\n2026-01,10\n", encoding="utf-8")
    session = {
        "files": [{"path": str(source)}],
        "artifacts": [],
        "workspace": str(workspace),
        "created_at": api.datetime.now().isoformat(),
        "last_accessed_at": api.datetime.now().isoformat(),
    }
    monkeypatch.setattr(api, "WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(api, "DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(api, "SESSIONS", {workspace.name: session})

    async def fake_stream(**kwargs):
        retry = AnalysisRuntime(
            workspace_dir=workspace,
            session_id=kwargs["session_id"],
            question=kwargs["task_question"],
            external_context=kwargs["semantic_context"],
            force_new_run=kwargs["force_new_run"],
            parent_run_id=kwargs["parent_run_id"],
            retry_of_step_id=kwargs["retry_of_step_id"],
        )
        retry.set_run_status("completed")
        yield "retry complete", []

    monkeypatch.setattr(api, "_run_analysis_stream", fake_stream)
    response = await api.retry_analysis_step(
        parent.run.run_id,
        failed_step.step_id,
        api.StepRetryRequest(reason="Use the governed revenue field"),
    )
    chunks = [chunk async for chunk in response.body_iterator]
    history = RunHistoryStore(tmp_path).list_runs(session_id=workspace.name)
    retry_summary = next(item for item in history if item["parent_run_id"])

    assert any("[DONE]" in (chunk.decode() if isinstance(chunk, bytes) else chunk) for chunk in chunks)
    assert retry_summary["parent_run_id"] == parent.run.run_id
    assert retry_summary["retry_of_step_id"] == failed_step.step_id
    retry_snapshot = RunHistoryStore(tmp_path).get_run(retry_summary["run_id"])
    assert retry_snapshot["task"]["external_context"] == context

from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from langgraph_langchain.runtime.context import AnalysisRuntime
from langgraph_langchain.runtime.events import RuntimeStreamContextReader, build_analysis_event


def _runtime(tmp_path):
    workspace = tmp_path / "session_plan"
    return AnalysisRuntime(
        workspace_dir=workspace,
        session_id=workspace.name,
        question="Analyze revenue",
    )


def test_pause_revise_confirm_resume_preserves_completed_work(tmp_path):
    runtime = _runtime(tmp_path)
    completed = runtime.start_step(objective="Load data", method="load_data")
    runtime.complete_step(completed.step_id)
    interrupted = runtime.start_step(objective="Explore", method="python_repl")

    runtime.pause_plan("Narrow the scope")
    assert runtime.run.status == "paused"
    assert runtime.run.plan_status == "paused"
    assert next(step for step in runtime.run.steps if step.step_id == interrupted.step_id).status == "paused"

    runtime.revise_plan(
        reason="Use formal methods only",
        revised_by="analyst",
        steps=[
            {"objective": "Reload data", "method": "load_data"},
            {"objective": "Compare regions", "method": "compare_groups"},
        ],
    )
    assert runtime.run.plan_version == 2
    assert runtime.run.plan_status == "awaiting_confirmation"
    assert [step.method for step in runtime.run.steps] == [
        "load_data",
        "load_data",
        "compare_groups",
    ]
    assert runtime.run.steps[0].status == "succeeded"
    assert runtime.run.plan_revisions[0].reason == "Use formal methods only"

    runtime.confirm_plan(confirmed_by="reviewer", note="Approved")
    assert runtime.run.status == "paused"
    assert runtime.run.plan_status == "confirmed"
    assert runtime.run.plan_confirmation.version == 2

    runtime.resume_plan()
    assert runtime.run.status == "running"
    assert runtime.run.plan_status == "active"


def test_confirmed_pending_step_is_consumed_instead_of_duplicated(tmp_path):
    runtime = _runtime(tmp_path)
    runtime.pause_plan("Prepare plan")
    runtime.revise_plan(
        reason="Add explicit comparison",
        steps=[{"objective": "Compare regions", "method": "compare_groups"}],
    )
    planned_id = runtime.run.steps[0].step_id
    runtime.confirm_plan()
    runtime.resume_plan()

    started = runtime.start_step(
        objective="Compare a metric across groups",
        method="compare_groups",
        expected_outputs=["table"],
    )

    assert started.step_id == planned_id
    assert len(runtime.run.steps) == 1
    assert started.status == "running"
    assert started.expected_outputs == ["table"]


def test_plan_revision_requires_pause_and_valid_dependencies(tmp_path):
    runtime = _runtime(tmp_path)
    with pytest.raises(ValueError, match="only be revised"):
        runtime.revise_plan(
            reason="Too early",
            steps=[{"objective": "Compare", "method": "compare_groups"}],
        )

    runtime.pause_plan("Review")
    with pytest.raises(ValueError, match="unknown or forward dependencies"):
        runtime.revise_plan(
            reason="Invalid dependency",
            steps=[{
                "objective": "Compare",
                "method": "compare_groups",
                "depends_on": ["step_missing"],
            }],
        )


def test_runtime_events_expose_plan_control_state(tmp_path):
    runtime = _runtime(tmp_path)
    runtime.pause_plan("Review", require_confirmation=True)
    context = RuntimeStreamContextReader(runtime.workspace_dir).read()
    event = build_analysis_event(session_id=runtime.session_id, stream_context=context)

    assert event["run_status"] == "awaiting_confirmation"
    assert event["plan_status"] == "awaiting_confirmation"
    assert event["plan_version"] == 1


@pytest.mark.asyncio
async def test_plan_api_revision_confirmation_and_archive_guard(monkeypatch, tmp_path):
    from langgraph_langchain import api_server_langgraph as api

    runtime = _runtime(tmp_path)
    runtime.pause_plan("Review")
    monkeypatch.setattr(api, "WORKSPACE_DIR", tmp_path)

    revised = await api.revise_analysis_plan(
        runtime.run.run_id,
        api.PlanRevisionRequest(
            reason="Use formal comparison",
            steps=[
                api.PlanStepRequest(
                    objective="Compare regions",
                    method="compare_groups",
                )
            ],
        ),
    )
    confirmed = await api.confirm_analysis_plan(runtime.run.run_id)
    plan = await api.get_analysis_plan(runtime.run.run_id)

    assert revised["plan_status"] == "awaiting_confirmation"
    assert confirmed["plan_status"] == "confirmed"
    assert plan["confirmation"]["version"] == 2

    replacement = AnalysisRuntime(
        workspace_dir=runtime.workspace_dir,
        session_id=runtime.session_id,
        question="Different task",
    )
    with pytest.raises(HTTPException) as exc_info:
        await api.confirm_analysis_plan(runtime.run.run_id)
    assert exc_info.value.status_code == 409
    assert replacement.run.run_id != runtime.run.run_id


@pytest.mark.asyncio
async def test_active_pause_api_sets_boundary_signal(monkeypatch, tmp_path):
    from langgraph_langchain import api_server_langgraph as api

    runtime = _runtime(tmp_path)
    event = asyncio.Event()
    monkeypatch.setattr(api, "WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(api, "_ACTIVE_PAUSES", {runtime.session_id: event})

    response = await api.pause_analysis_plan(
        runtime.run.run_id,
        api.PlanPauseRequest(reason="Inspect current results", require_confirmation=True),
    )

    assert response["plan_status"] == "pause_requested"
    assert event.is_set()
    assert event.reason == "Inspect current results"
    assert event.require_confirmation is True

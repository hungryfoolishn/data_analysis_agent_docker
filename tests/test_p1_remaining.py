import asyncio
from types import SimpleNamespace

from langgraph_langchain.runtime.quality import check_analysis_quality
from webui.workbench import empty_workbench_state, render_quality_html


def test_quality_checker_detects_broken_lineage_and_percentage_disclosure():
    result = check_analysis_quality({
        "run": {"run_id": "run_test", "status": "completed"},
        "findings": [{
            "finding_id": "f1",
            "statement": "North share is 42%",
            "evidence": [{"source_execution_ids": ["missing"], "stats": {}}],
        }],
        "executions": [],
        "artifacts": [],
    })
    assert result.passed is False
    codes = {item.code for item in result.issues}
    assert "evidence_execution_missing" in codes
    assert "percentage_without_denominator" in codes
    assert "evidence_step_missing" in codes


def test_quality_checker_requires_existing_runtime_step():
    result = check_analysis_quality({
        "run": {"run_id": "run_test", "status": "completed", "steps": [{"step_id": "step_1"}]},
        "findings": [{
            "finding_id": "f1",
            "statement": "North revenue is higher.",
            "evidence": [{"source_step_ids": ["step_missing"], "source_execution_ids": ["exec_1"]}],
        }],
        "executions": [{"execution_id": "exec_1", "status": "succeeded"}],
        "artifacts": [],
    })
    assert result.passed is False
    assert "evidence_step_unknown" in {item.code for item in result.issues}


def test_quality_checker_accepts_null_evidence_stats():
    result = check_analysis_quality({
        "run": {"run_id": "run_test", "status": "completed", "steps": [{"step_id": "step_1"}]},
        "findings": [{
            "finding_id": "f1",
            "statement": "North share is 42%.",
            "evidence": [{"source_step_ids": ["step_1"], "source_execution_ids": ["exec_1"], "stats": None}],
        }],
        "executions": [{"execution_id": "exec_1", "status": "succeeded"}],
        "artifacts": [],
    })
    assert "percentage_without_denominator" in {item.code for item in result.issues}


def test_quality_render_is_escaped_and_visible():
    rendered = render_quality_html({"passed": False, "checked_findings": 1, "issues": [{"severity": "error", "code": "<bad>", "message": "<unsafe>"}]})
    assert "<unsafe>" not in rendered
    assert "&lt;bad&gt;" in rendered


def test_task_api_routes_are_registered():
    from langgraph_langchain.api_server_langgraph import app
    paths = {route.path for route in app.routes}
    assert "/analysis/tasks" in paths
    assert "/analysis/tasks/{task_id}/events" in paths
    assert "/analysis/runs/{run_id}/quality" in paths


def test_runtime_execution_step_backfill_updates_artifact_lineage():
    from langgraph_langchain.langgraph_agent import _bind_runtime_execution_step

    artifact = SimpleNamespace(step_id=None)
    execution = SimpleNamespace(
        tool_name="compare_groups",
        step_id=None,
        output_artifact_ids=["artifact_1"],
    )
    runtime = SimpleNamespace(
        executions=[execution],
        artifacts={"artifact_1": artifact},
        persist=lambda: None,
    )
    session = SimpleNamespace(analysis_runtime=runtime)

    _bind_runtime_execution_step(session, "compare_groups", "step_1")

    assert execution.step_id == "step_1"
    assert artifact.step_id == "step_1"

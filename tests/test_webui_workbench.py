from webui.workbench import (
    apply_analysis_event,
    apply_runtime_snapshot,
    empty_workbench_state,
    render_executions_html,
    render_artifacts_html,
    render_findings_html,
    render_run_summary_html,
    render_steps_html,
    resolve_artifact_url,
)


def test_analysis_event_merges_step_updates_and_artifacts():
    state = empty_workbench_state()
    running = apply_analysis_event(
        state,
        {
            "run_id": "run_test",
            "run_status": "running",
            "step": {
                "step_id": "step_test",
                "objective": "Load data",
                "method": "load_data",
                "status": "running",
            },
        },
    )
    completed = apply_analysis_event(
        running,
        {
            "step": {
                "step_id": "step_test",
                "objective": "Load data",
                "method": "load_data",
                "status": "succeeded",
            }
        },
        [{"artifact_id": "artifact_test", "name": "profile.csv"}],
    )

    assert len(completed["steps"]) == 1
    assert completed["steps"][0]["status"] == "succeeded"
    assert completed["artifacts"][0]["artifact_id"] == "artifact_test"


def test_runtime_snapshot_adds_execution_details():
    state = apply_runtime_snapshot(
        empty_workbench_state(),
        {
            "task": {"task_id": "task_test", "question": "Find trend"},
            "run": {
                "run_id": "run_test",
                "status": "paused",
                "plan_status": "awaiting_confirmation",
                "plan_version": 2,
                "steps": [],
            },
            "executions": [{"execution_id": "exec_test", "tool_name": "python_repl"}],
        },
    )

    assert state["question"] == "Find trend"
    assert state["run_status"] == "paused"
    assert state["plan_status"] == "awaiting_confirmation"
    assert state["plan_version"] == 2
    assert state["executions"][0]["execution_id"] == "exec_test"


def test_workbench_summary_shows_plan_version_and_confirmation_state():
    state = empty_workbench_state()
    state.update({"plan_version": 3, "plan_status": "awaiting_confirmation"})

    rendered = render_run_summary_html(state)

    assert "v3" in rendered
    assert "待确认" in rendered


def test_workbench_html_escapes_runtime_content():
    steps_html = render_steps_html(
        [{"step_id": "step_test", "objective": "<script>x</script>", "status": "failed"}]
    )
    executions_html = render_executions_html(
        [{"tool_name": "python_repl", "code_or_query": "<b>unsafe</b>"}]
    )

    assert "<script>" not in steps_html
    assert "&lt;script&gt;" in steps_html
    assert "<b>unsafe</b>" not in executions_html


def test_resolve_artifact_url_uses_configured_file_server():
    assert resolve_artifact_url(
        "http://container:8888/workspace/files/chart.png", "http://host:18888"
    ) == "http://host:18888/workspace/files/chart.png"


def test_workbench_renders_report_revision_separately_from_failure():
    state = empty_workbench_state()
    state["steps"] = [
        {
            "step_id": "step_revision",
            "objective": "Validate report",
            "method": "finish_report",
            "status": "needs_revision",
            "error": "missing time range",
        }
    ]

    summary = render_run_summary_html(state)
    steps = render_steps_html(state["steps"])

    assert "修正</span><strong>1" in summary
    assert "失败</span><strong>0" in summary
    assert "待修正" in steps
    assert "step-needs_revision" in steps


def test_completed_run_exposes_analysis_package_download():
    rendered = render_artifacts_html(
        [],
        "http://host:18888",
        run_id="run_0123456789abcdef0123456789abcdef",
        run_status="completed",
    )

    assert "可复现分析包" in rendered
    assert "http://host:18888/analysis/runs/run_0123456789abcdef0123456789abcdef/package" in rendered
    assert "http://host:18888/analysis/runs/run_0123456789abcdef0123456789abcdef/report/rebuild" in rendered


def test_finding_row_links_to_expanded_lineage_api():
    rendered = render_findings_html(
        [{
            "finding_id": "F001",
            "statement": "Department B has higher salary",
            "evidence": [{
                "source_execution_ids": ["exec_1"],
                "source_asset_ids": ["asset_1"],
                "source_artifact_ids": ["artifact_1"],
            }],
        }],
        "http://host:18888",
        "run_0123456789abcdef0123456789abcdef",
    )

    assert "1 次执行" in rendered
    assert "1 个数据版本" in rendered
    assert "/findings/F001" in rendered

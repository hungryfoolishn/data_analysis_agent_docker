from __future__ import annotations

import hashlib

import pandas as pd
import pytest

from langgraph_langchain.runtime.context import AnalysisRuntime
from langgraph_langchain.runtime.report_rebuild import (
    rebuild_report_markdown,
    write_rebuilt_report,
)
from langgraph_langchain.schemas import (
    AnalysisAssumption,
    EvidenceItem,
    Finding,
    MetricDefinition,
)


def _snapshot(tmp_path):
    workspace_root = tmp_path / "workspace"
    workspace = workspace_root / "session_rebuild"
    workspace.mkdir(parents=True)
    source = workspace / "employees.csv"
    dataframe = pd.DataFrame({"department": ["A", "B"], "salary": [10, 20]})
    dataframe.to_csv(source, index=False)
    runtime = AnalysisRuntime(
        workspace_dir=workspace,
        session_id=workspace.name,
        question="Compare employee salary",
    )
    asset = runtime.register_dataframe(dataframe=dataframe, source_path=source)
    runtime.record_metric_definition(MetricDefinition(
        metric_name="mean_salary",
        definition_text="AVG(salary) grouped by department",
        dedup_rule="one row per employee; no duplicate rows observed",
    ))
    runtime.record_assumption(AnalysisAssumption(
        assumption_text="salary values share one currency",
        risk_level="medium",
    ))
    step = runtime.start_step(objective="Compare salary", method="python_repl")
    execution = runtime.record_execution(
        tool_name="python_repl",
        status="succeeded",
        step_id=step.step_id,
        code_or_query="df.groupby('department')['salary'].mean()",
        stdout_preview="A 10.0 B 20.0",
    )
    runtime.complete_step(step.step_id)
    runtime.record_finding(Finding(
        finding_id="F001",
        statement="Department B mean salary is 20 versus A at 10",
        category="comparison",
        evidence=[EvidenceItem(
            evidence_text="Grouped means are B=20 and A=10",
            source_fields=["department", "salary"],
            source_execution_ids=[execution.execution_id],
            source_step_ids=[step.step_id],
            source_asset_ids=[asset.asset_id],
            filters=["salary is not null"],
            calculation_method="GROUP BY department, AVG(salary)",
        )],
        run_id=runtime.run.run_id,
    ))
    runtime.set_run_status("completed")
    return workspace_root, runtime.snapshot()


def test_rebuilt_report_is_deterministic_and_does_not_need_original_report(tmp_path):
    workspace_root, snapshot = _snapshot(tmp_path)

    first = rebuild_report_markdown(snapshot)
    second = rebuild_report_markdown(snapshot)

    assert first == second
    assert "Department B mean salary is 20 versus A at 10" in first
    assert "AVG(salary) grouped by department" in first
    assert "salary is not null" in first
    assert "static cross-sectional analysis" in first
    assert snapshot["assets"][0]["content_hash"] in first
    assert "Original report content: not used" in first
    assert not (workspace_root / "session_rebuild" / "data_analysis_report.md").exists()


def test_rebuilt_report_is_written_atomically_with_content_hash(tmp_path):
    workspace_root, snapshot = _snapshot(tmp_path)

    path, content_hash = write_rebuilt_report(snapshot, workspace_root)

    content = path.read_text(encoding="utf-8")
    assert path.name == f"{snapshot['run']['run_id']}.md"
    assert hashlib.sha256(content.encode("utf-8")).hexdigest() == content_hash


def test_rebuild_refuses_to_invent_report_without_findings(tmp_path):
    workspace_root, snapshot = _snapshot(tmp_path)
    snapshot["findings"] = []

    with pytest.raises(ValueError, match="no structured findings"):
        rebuild_report_markdown(snapshot)


@pytest.mark.asyncio
async def test_rebuild_api_returns_deterministic_markdown_file(monkeypatch, tmp_path):
    from langgraph_langchain import api_server_langgraph as api

    workspace_root, snapshot = _snapshot(tmp_path)
    monkeypatch.setattr(api, "WORKSPACE_DIR", workspace_root)

    response = await api.download_rebuilt_analysis_report(snapshot["run"]["run_id"])

    assert response.media_type.startswith("text/markdown")
    assert response.headers["x-report-rebuild-mode"] == "deterministic"
    assert response.headers["x-content-sha256"]

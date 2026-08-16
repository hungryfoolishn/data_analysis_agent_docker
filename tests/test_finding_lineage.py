from __future__ import annotations

import pandas as pd

from langgraph_langchain.runtime.context import AnalysisRuntime
from langgraph_langchain.runtime.finding_lineage import (
    build_runtime_lineage_graph,
    expand_finding_lineage,
)
from langgraph_langchain.schemas import EvidenceItem, Finding


def _lineage_runtime(tmp_path):
    source = tmp_path / "employees.csv"
    dataframe = pd.DataFrame({"department": ["A", "B"], "salary": [10, 20]})
    dataframe.to_csv(source, index=False)
    runtime = AnalysisRuntime(
        workspace_dir=tmp_path,
        session_id=tmp_path.name,
        question="Compare salary",
    )
    asset = runtime.register_dataframe(dataframe=dataframe, source_path=source)
    step = runtime.start_step(objective="Compare department salary", method="python_repl")
    execution = runtime.record_execution(
        tool_name="python_repl",
        status="succeeded",
        step_id=step.step_id,
        code_or_query="df.groupby('department')['salary'].mean()",
        stdout_preview="A 10.0 B 20.0",
    )
    chart = tmp_path / "salary.png"
    chart.write_bytes(b"chart")
    artifact = runtime.register_artifact(
        chart,
        created_by_tool="python_repl",
        execution_id=execution.execution_id,
        step_id=step.step_id,
    )
    runtime.complete_step(step.step_id)
    finding = Finding(
        finding_id="F001",
        statement="Department B mean salary is twice A",
        evidence=[EvidenceItem(
            evidence_text="B=20, A=10",
            source_fields=["department", "salary"],
            source_artifacts=[chart.name],
            source_artifact_ids=[artifact.artifact_id],
            source_execution_ids=[execution.execution_id],
            source_step_ids=[step.step_id],
            source_asset_ids=[asset.asset_id],
            group_dimension="department",
            filters=["salary is not null"],
            calculation_method="GROUP BY department, AVG(salary)",
        )],
        run_id=runtime.run.run_id,
    )
    runtime.record_finding(finding)
    return runtime, asset, step, execution, artifact


def test_finding_expands_to_code_data_version_fields_filters_and_artifact(tmp_path):
    runtime, asset, step, execution, artifact = _lineage_runtime(tmp_path)

    detail = expand_finding_lineage(runtime.snapshot(), "F001")

    evidence = detail["evidence_lineage"][0]
    assert detail["is_complete"] is True
    assert evidence["assets"][0]["content_hash"] == asset.content_hash
    assert evidence["steps"][0]["step_id"] == step.step_id
    assert "groupby" in evidence["executions"][0]["code_or_query"]
    assert evidence["artifacts"][0]["artifact_id"] == artifact.artifact_id
    assert evidence["evidence"]["source_fields"] == ["department", "salary"]
    assert evidence["evidence"]["filters"] == ["salary is not null"]


def test_runtime_lineage_graph_connects_asset_execution_evidence_finding_and_report(tmp_path):
    runtime, asset, _, execution, artifact = _lineage_runtime(tmp_path)
    graph = build_runtime_lineage_graph(runtime.snapshot())
    relationships = {
        (item["source"], item["target"], item["relationship"])
        for item in graph["edges"]
    }
    evidence_id = runtime.findings[0]["evidence"][0]["evidence_id"]

    assert (f"asset:{asset.asset_id}", f"execution:{execution.execution_id}", "input_to") in relationships
    assert (f"artifact:{artifact.artifact_id}", f"evidence:{evidence_id}", "supports") in relationships
    assert (f"evidence:{evidence_id}", "finding:F001", "supports") in relationships


def test_finding_without_runtime_step_is_explicitly_partial(tmp_path):
    runtime, _, _, execution, _ = _lineage_runtime(tmp_path)
    snapshot = runtime.snapshot()
    snapshot["findings"][0]["evidence"][0]["source_step_ids"] = []
    snapshot["executions"] = [
        {**item, "step_id": None} if item["execution_id"] == execution.execution_id else item
        for item in snapshot["executions"]
    ]
    snapshot["artifacts"] = [
        {**item, "step_id": None}
        if item["artifact_id"] in snapshot["findings"][0]["evidence"][0]["source_artifact_ids"]
        else item
        for item in snapshot["artifacts"]
    ]

    detail = expand_finding_lineage(snapshot, "F001")

    assert detail["is_complete"] is False
    assert "evidence is not linked to a runtime step" in detail["warnings"]

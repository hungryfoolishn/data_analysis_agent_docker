from __future__ import annotations

import hashlib
import json
import zipfile

import pandas as pd

from langgraph_langchain.runtime.context import AnalysisRuntime
from langgraph_langchain.runtime.package import build_analysis_package
from langgraph_langchain.schemas import EvidenceItem, Finding


def _completed_runtime(workspace):
    workspace.mkdir(parents=True)
    source = workspace / "employees.csv"
    dataframe = pd.DataFrame({"department": ["A", "B"], "salary": [10, 20]})
    dataframe.to_csv(source, index=False)
    runtime = AnalysisRuntime(
        workspace_dir=workspace,
        session_id=workspace.name,
        question="Summarize employee data",
    )
    runtime.register_dataframe(dataframe=dataframe, source_path=source)
    step = runtime.start_step(objective="Compute summary", method="python_repl")
    execution = runtime.record_execution(
        tool_name="python_repl",
        status="succeeded",
        step_id=step.step_id,
        code_or_query="print(df['salary'].mean())",
        stdout_preview="15.0",
    )
    report = workspace / "data_analysis_report.md"
    report.write_text("# Report\n\nMean salary: 15", encoding="utf-8")
    runtime.register_artifact(
        report,
        created_by_tool="finish_report",
        execution_id=execution.execution_id,
        step_id=step.step_id,
    )
    runtime.record_finding(Finding(
        finding_id="F001",
        statement="Mean salary is 15",
        evidence=[EvidenceItem(
            evidence_text="Observed mean=15",
            source_fields=["salary"],
            source_execution_ids=[execution.execution_id],
            source_step_ids=[step.step_id],
            source_asset_ids=list(runtime.assets),
        )],
        run_id=runtime.run.run_id,
    ))
    runtime.complete_step(step.step_id)
    runtime.set_run_status("completed")
    return runtime, source, report


def test_analysis_package_contains_snapshot_inputs_artifacts_and_replay(tmp_path):
    workspace_root = tmp_path / "workspace"
    runtime, source, report = _completed_runtime(workspace_root / "session_package")

    package = build_analysis_package(
        snapshot=runtime.snapshot(),
        workspace_root=workspace_root,
    )

    with zipfile.ZipFile(package) as archive:
        names = set(archive.namelist())
        assert "manifest.json" in names
        assert "metadata/run_snapshot.json" in names
        assert "README.md" in names
        assert "replay.py" in names
        assert "artifacts/rebuilt_report.md" in names
        assert any(name.startswith("inputs/") and name.endswith(source.name) for name in names)
        assert any(name.startswith("artifacts/") and name.endswith(report.name) for name in names)
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["package_version"] == "1.0"
        assert manifest["run_id"] == runtime.run.run_id
        assert manifest["missing_files"] == []
        for item in manifest["files"]:
            content = archive.read(item["archive_path"])
            assert len(content) == item["size_bytes"]
            assert hashlib.sha256(content).hexdigest() == item["sha256"]
        replay = archive.read("replay.py").decode("utf-8")
        assert "salary" in replay
        assert "mean()" in replay


def test_analysis_package_does_not_read_files_outside_workspace(tmp_path):
    workspace_root = tmp_path / "workspace"
    runtime, _, _ = _completed_runtime(workspace_root / "session_safe")
    secret = tmp_path / "secret.txt"
    secret.write_text("must-not-leak", encoding="utf-8")
    snapshot = runtime.snapshot()
    snapshot["artifacts"].append({
        "artifact_id": "artifact_outside",
        "name": secret.name,
        "path": str(secret),
    })

    package = build_analysis_package(snapshot=snapshot, workspace_root=workspace_root)

    with zipfile.ZipFile(package) as archive:
        assert b"must-not-leak" not in b"".join(archive.read(name) for name in archive.namelist())
        manifest = json.loads(archive.read("manifest.json"))
        assert {item["ref_id"] for item in manifest["missing_files"]} == {"artifact_outside"}

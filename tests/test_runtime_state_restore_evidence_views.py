"""Generic Runtime regression: state restore must keep EvidenceRegistry views.

B9.8.2 stage 3 found that ``AnalysisRuntime`` state restore replaced
``self.artifacts`` wholesale, leaving the EvidenceRegistry (which captures the
dict at construction) with a stale view — artifact registrations made after a
restore were invisible to evidence reference validation, failing any tool
that registers canonical evidence on a restored/retry run.  The fix restores
artifacts in place; this regression pins that contract for the generic
runtime (no macro involvement).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from langgraph_langchain.evidence import (
    canonical_from_operation_evidence,
    collect_operation_evidence,
)
from langgraph_langchain.runtime.context import AnalysisRuntime


def test_restored_runtime_artifacts_visible_to_evidence_registry(tmp_path):
    workspace = tmp_path / "ws"
    first = AnalysisRuntime(workspace_dir=workspace, session_id="restore", question="q")
    frame = pd.DataFrame({"period": ["2024"], "value": [1.0]})
    path = workspace / "a.parquet"
    frame.to_parquet(path, index=False)
    artifact = first.register_artifact(path, created_by_tool="run_analysis_operation")
    first._persist()

    # Restore on the same workspace (fresh runtime instance loads state).
    restored = AnalysisRuntime(workspace_dir=workspace, session_id="restore", question="q")
    assert artifact.artifact_id in restored.artifacts

    # A post-restore artifact registration must be visible to the registry.
    new_path = workspace / "b.parquet"
    frame.to_parquet(new_path, index=False)
    new_artifact = restored.register_artifact(
        new_path, created_by_tool="run_analysis_operation"
    )

    asset = restored.register_dataframe(
        dataframe=frame,
        source_path=workspace / "in.parquet",
        source_type="parquet",
        source_metadata={},
    )
    result = restored.record_execution(
        tool_name="run_analysis_operation",
        status="succeeded",
        execution_id="exec_restore_view_1",
    )
    operation_evidence = collect_operation_evidence(
        execution_id=result.execution_id,
        operation="summarize",
        parameters={},
        input_asset_ids=[asset.asset_id],
        output_asset=restored.register_dataframe(
            dataframe=frame,
            source_path=workspace / "out.parquet",
            source_type="parquet",
            source_metadata={"execution_id": result.execution_id},
        ),
        numeric_values={"row_count": 1.0, "column_count": 2.0},
    )
    canonical = canonical_from_operation_evidence(
        operation_evidence,
        task_id=result.task_id,
        artifact_ids=[new_artifact.artifact_id, artifact.artifact_id],
    )
    # Must not raise EvidenceRegistryError about unknown artifact IDs.
    registered = restored.register_canonical_evidence(canonical)
    assert registered.artifact_ids

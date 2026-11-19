"""Restore integrity contracts for DataAssets and Evidence (A1.4.1)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from langgraph_langchain.data.assets import DataAsset, SchemaSnapshot, hash_file_stable
from langgraph_langchain.evidence import (
    canonical_from_operation_evidence,
    canonical_from_sqlite_query_evidence,
    collect_operation_evidence,
    collect_sqlite_query_evidence,
)
from langgraph_langchain.runtime.context import AnalysisRuntime
from langgraph_langchain.runtime.report_validator import validate_report
from langgraph_langchain.schemas import EvidenceItem, Finding


def _restore_state(asset: DataAsset) -> dict:
    return asset.source_metadata["restore_integrity"]


def _read_state(workspace: Path) -> dict:
    return json.loads((workspace / ".analysis_runtime.json").read_text(encoding="utf-8"))


def _write_state(workspace: Path, state: dict) -> None:
    (workspace / ".analysis_runtime.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _new_runtime(tmp_path: Path, *, filename: str = "input.csv") -> tuple[AnalysisRuntime, DataAsset, Path]:
    workspace = tmp_path / "workspace"
    path = workspace / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"period": ["2024"], "value": [1.0]}).to_csv(path, index=False)
    runtime = AnalysisRuntime(workspace_dir=workspace, session_id="restore", question="q")
    asset = runtime.register_dataframe(dataframe=pd.read_csv(path), source_path=path)
    return runtime, asset, path


def _operation_evidence(
    runtime: AnalysisRuntime,
    *,
    input_asset: DataAsset,
    output_path: Path,
) -> tuple[DataAsset, object]:
    artifact = runtime.register_artifact(
        output_path,
        created_by_tool="run_analysis_operation",
    )
    output_asset = runtime.register_dataframe(
        dataframe=pd.DataFrame({"value": [2.0]}),
        source_path=output_path,
        source_type="parquet",
        source_metadata={"execution_id": "exec_restore_integrity"},
    )
    result = runtime.record_execution(
        tool_name="run_analysis_operation",
        status="succeeded",
        execution_id="exec_restore_integrity",
    )
    operation_evidence = collect_operation_evidence(
        execution_id=result.execution_id,
        operation="summarize",
        parameters={},
        input_asset_ids=[input_asset.asset_id],
        output_asset=output_asset,
        numeric_values={"row_count": 1.0, "column_count": 1.0},
    )
    canonical = canonical_from_operation_evidence(
        operation_evidence,
        task_id=result.task_id,
        artifact_ids=[artifact.artifact_id],
    )
    registered = runtime.register_canonical_evidence(canonical)
    runtime.verify_canonical_evidence(registered.evidence_id)
    return output_asset, registered


def _sqlite_result(frame: pd.DataFrame, *, source: str = "db.sqlite") -> SimpleNamespace:
    metadata = SimpleNamespace(
        source_type="sqlite",
        source_identity=source,
        query_type="table",
        query_sha256="a" * 64,
        table_name="orders",
        selected_columns=("period", "value"),
        returned_row_count=len(frame),
        requested_row_limit=1000,
        execution_duration_seconds=0.01,
        truncated=False,
    )
    return SimpleNamespace(dataframe=frame, metadata=metadata)


def _register_sqlite_asset(runtime: AnalysisRuntime) -> DataAsset:
    return runtime.register_sqlite_result(
        result=_sqlite_result(pd.DataFrame({"period": ["2024"], "value": [1.0]})),
        execution_id="exec_restore_sqlite",
    )


def test_normal_file_restore_is_verified(tmp_path):
    runtime, asset, _ = _new_runtime(tmp_path)
    restored = AnalysisRuntime(
        workspace_dir=runtime.workspace_dir, session_id="restore", question="q"
    )
    assert _restore_state(restored.assets[asset.asset_id])["status"] == "VERIFIED"


def test_modified_file_restore_is_stale(tmp_path):
    runtime, asset, path = _new_runtime(tmp_path)
    path.write_text("period,value\n2024,999\n", encoding="utf-8")
    restored = AnalysisRuntime(
        workspace_dir=runtime.workspace_dir, session_id="restore", question="q"
    )
    state = _restore_state(restored.assets[asset.asset_id])
    assert state["status"] == "STALE"
    assert state["reason"] == "content_hash_mismatch"


def test_missing_file_restore_is_missing(tmp_path):
    runtime, asset, path = _new_runtime(tmp_path)
    path.unlink()
    restored = AnalysisRuntime(
        workspace_dir=runtime.workspace_dir, session_id="restore", question="q"
    )
    assert _restore_state(restored.assets[asset.asset_id])["status"] == "MISSING"


def test_truncated_file_restore_is_stale(tmp_path):
    runtime, asset, path = _new_runtime(tmp_path)
    path.write_text("", encoding="utf-8")
    restored = AnalysisRuntime(
        workspace_dir=runtime.workspace_dir, session_id="restore", question="q"
    )
    assert _restore_state(restored.assets[asset.asset_id])["status"] == "STALE"


def test_legacy_asset_without_hash_is_unverifiable(tmp_path):
    runtime, asset, _ = _new_runtime(tmp_path)
    asset.content_hash = None
    runtime._persist()
    restored = AnalysisRuntime(
        workspace_dir=runtime.workspace_dir, session_id="restore", question="q"
    )
    state = _restore_state(restored.assets[asset.asset_id])
    assert state["status"] == "UNVERIFIABLE"
    assert state["reason"] == "missing_content_hash"


def test_relative_path_is_validation_failed(tmp_path):
    runtime, asset, _ = _new_runtime(tmp_path)
    state = _read_state(runtime.workspace_dir)
    state["assets"][0]["location"] = "input.csv"
    _write_state(runtime.workspace_dir, state)
    restored = AnalysisRuntime(
        workspace_dir=runtime.workspace_dir, session_id="restore", question="q"
    )
    assert _restore_state(restored.assets[asset.asset_id])["status"] == "VALIDATION_FAILED"


def test_large_file_uses_streaming_hash(tmp_path):
    workspace = tmp_path / "workspace"
    path = workspace / "large.bin"
    workspace.mkdir()
    path.write_bytes(b"a" * (1024 * 1024 + 17))
    runtime = AnalysisRuntime(workspace_dir=workspace, session_id="restore", question="q")
    digest, stable = hash_file_stable(path)
    asset = DataAsset(
        name="large.bin",
        source_type="binary",
        location=str(path),
        content_hash=digest,
        schema_snapshot=SchemaSnapshot(row_count=-1, column_count=0, columns=[]),
    )
    runtime.assets[asset.asset_id] = asset
    runtime._persist()
    restored = AnalysisRuntime(
        workspace_dir=workspace, session_id="restore", question="q"
    )
    assert stable
    assert _restore_state(restored.assets[asset.asset_id])["status"] == "VERIFIED"


def test_unstable_file_read_is_not_verified(tmp_path, monkeypatch):
    runtime, asset, path = _new_runtime(tmp_path)
    original = hash_file_stable

    def unstable_hash(candidate, **kwargs):
        digest, _ = original(candidate, **kwargs)
        path.write_bytes(b"changed-during-read")
        return digest, False

    monkeypatch.setattr(
        "langgraph_langchain.runtime.context.hash_file_stable", unstable_hash
    )
    restored = AnalysisRuntime(
        workspace_dir=runtime.workspace_dir, session_id="restore", question="q"
    )
    state = _restore_state(restored.assets[asset.asset_id])
    assert state["status"] == "VALIDATION_FAILED"
    assert state["reason"] == "unstable_file_read"


def test_repeated_restore_keeps_asset_status_idempotent(tmp_path):
    runtime, asset, _ = _new_runtime(tmp_path)
    first = AnalysisRuntime(
        workspace_dir=runtime.workspace_dir, session_id="restore", question="q"
    )
    second = AnalysisRuntime(
        workspace_dir=runtime.workspace_dir, session_id="restore", question="q"
    )
    assert _restore_state(first.assets[asset.asset_id]) == _restore_state(
        second.assets[asset.asset_id]
    )


def test_multiple_assets_are_independently_classified(tmp_path):
    runtime, asset, path = _new_runtime(tmp_path)
    other_path = runtime.workspace_dir / "other.csv"
    pd.DataFrame({"period": ["2025"], "value": [2.0]}).to_csv(other_path, index=False)
    other = runtime.register_dataframe(
        dataframe=pd.read_csv(other_path), source_path=other_path
    )
    path.write_text("period,value\n2024,999\n", encoding="utf-8")
    restored = AnalysisRuntime(
        workspace_dir=runtime.workspace_dir, session_id="restore", question="q"
    )
    assert _restore_state(restored.assets[asset.asset_id])["status"] == "STALE"
    assert _restore_state(restored.assets[other.asset_id])["status"] == "VERIFIED"


def test_csv_and_parquet_file_assets_restore(tmp_path):
    runtime, csv_asset, _ = _new_runtime(tmp_path)
    parquet_path = runtime.workspace_dir / "input.parquet"
    frame = pd.DataFrame({"period": ["2024"], "value": [1.0]})
    frame.to_parquet(parquet_path, index=False)
    parquet_asset = runtime.register_dataframe(dataframe=frame, source_path=parquet_path)
    restored = AnalysisRuntime(
        workspace_dir=runtime.workspace_dir, session_id="restore", question="q"
    )
    assert _restore_state(restored.assets[csv_asset.asset_id])["status"] == "VERIFIED"
    assert _restore_state(restored.assets[parquet_asset.asset_id])["status"] == "VERIFIED"


def test_sqlite_database_with_sidecars_is_unverifiable(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    database_path = workspace / "db.sqlite"
    connection = sqlite3.connect(database_path)
    connection.execute("create table orders (id integer)")
    connection.commit()
    connection.close()
    database_path.with_name(database_path.name + "-wal").write_bytes(b"wal")
    database_path.with_name(database_path.name + "-shm").write_bytes(b"shm")
    runtime = AnalysisRuntime(workspace_dir=workspace, session_id="restore", question="q")
    asset = DataAsset.from_sqlite_database(database_path=database_path)
    runtime.assets[asset.asset_id] = asset
    runtime._persist()
    restored = AnalysisRuntime(
        workspace_dir=workspace, session_id="restore", question="q"
    )
    assert _restore_state(restored.assets[asset.asset_id])["status"] == "UNVERIFIABLE"


def test_tampered_sqlite_snapshot_is_stale(tmp_path):
    workspace = tmp_path / "workspace"
    runtime = AnalysisRuntime(workspace_dir=workspace, session_id="restore", question="q")
    asset = _register_sqlite_asset(runtime)
    reference = asset.source_metadata["snapshot_reference"]
    snapshot_path = runtime.snapshot_store.root / reference["snapshot_location"]
    snapshot_path.write_bytes(b"tampered")
    restored = AnalysisRuntime(
        workspace_dir=workspace, session_id="restore", question="q"
    )
    assert _restore_state(restored.assets[asset.asset_id])["status"] == "STALE"


def test_missing_sqlite_snapshot_is_missing(tmp_path):
    workspace = tmp_path / "workspace"
    runtime = AnalysisRuntime(workspace_dir=workspace, session_id="restore", question="q")
    asset = _register_sqlite_asset(runtime)
    reference = asset.source_metadata["snapshot_reference"]
    snapshot_path = runtime.snapshot_store.root / reference["snapshot_location"]
    snapshot_path.unlink()
    restored = AnalysisRuntime(
        workspace_dir=workspace, session_id="restore", question="q"
    )
    assert _restore_state(restored.assets[asset.asset_id])["status"] == "MISSING"


def test_sqlite_snapshot_content_mismatch_is_stale(tmp_path):
    workspace = tmp_path / "workspace"
    runtime = AnalysisRuntime(workspace_dir=workspace, session_id="restore", question="q")
    asset = _register_sqlite_asset(runtime)
    state = _read_state(runtime.workspace_dir)
    state["assets"][0]["content_hash"] = "b" * 64
    _write_state(runtime.workspace_dir, state)
    restored = AnalysisRuntime(
        workspace_dir=workspace, session_id="restore", question="q"
    )
    assert _restore_state(restored.assets[asset.asset_id])["status"] == "STALE"


def test_new_asset_creation_is_not_restored_state(tmp_path):
    runtime, _, _ = _new_runtime(tmp_path)
    new_path = runtime.workspace_dir / "new.csv"
    pd.DataFrame({"period": ["2026"], "value": [3.0]}).to_csv(new_path, index=False)
    asset = runtime.register_dataframe(
        dataframe=pd.read_csv(new_path), source_path=new_path
    )
    assert "restore_integrity" not in asset.source_metadata


def test_data_asset_serialization_roundtrip_is_compatible(tmp_path):
    runtime, asset, _ = _new_runtime(tmp_path)
    restored = AnalysisRuntime(
        workspace_dir=runtime.workspace_dir, session_id="restore", question="q"
    )
    roundtrip = restored.assets[asset.asset_id]
    assert roundtrip.asset_id == asset.asset_id
    assert roundtrip.content_hash == asset.content_hash
    assert roundtrip.schema_snapshot.row_count == asset.schema_snapshot.row_count


def test_canonical_evidence_survives_restore_and_is_verified(tmp_path):
    runtime, input_asset, _ = _new_runtime(tmp_path)
    output_path = runtime.workspace_dir / "operation.parquet"
    pd.DataFrame({"value": [2.0]}).to_parquet(output_path, index=False)
    output_asset, evidence = _operation_evidence(
        runtime, input_asset=input_asset, output_path=output_path
    )
    assert evidence.validation_status == "verified"
    runtime._persist()
    restored = AnalysisRuntime(
        workspace_dir=runtime.workspace_dir, session_id="restore", question="q"
    )
    record = restored.evidence_registry.get(evidence.evidence_id)
    assert record.validation_status == "verified"
    assert evidence.evidence_id in {item.evidence_id for item in restored.evidence_registry.records()}


def test_canonical_evidence_fails_for_stale_output(tmp_path):
    runtime, input_asset, _ = _new_runtime(tmp_path)
    output_path = runtime.workspace_dir / "operation.parquet"
    pd.DataFrame({"value": [2.0]}).to_parquet(output_path, index=False)
    output_asset, evidence = _operation_evidence(
        runtime, input_asset=input_asset, output_path=output_path
    )
    output_path.write_bytes(b"tampered")
    runtime._persist()
    restored = AnalysisRuntime(
        workspace_dir=runtime.workspace_dir, session_id="restore", question="q"
    )
    record = restored.evidence_registry.get(evidence.evidence_id)
    assert record.validation_status == "failed"


def test_sqlite_evidence_content_hash_mismatch_fails(tmp_path):
    workspace = tmp_path / "workspace"
    runtime = AnalysisRuntime(workspace_dir=workspace, session_id="restore", question="q")
    source_path = workspace / "source.csv"
    source_frame = pd.DataFrame({"period": ["2024"], "value": [1.0]})
    source_path.write_text(source_frame.to_csv(index=False), encoding="utf-8")
    source_asset = runtime.register_dataframe(dataframe=source_frame, source_path=source_path)
    asset = _register_sqlite_asset(runtime)
    runtime.record_execution(
        tool_name="run_sqlite_query",
        status="succeeded",
        execution_id="exec_restore_sqlite",
    )
    result = runtime._lookup_execution("exec_restore_sqlite")
    evidence = collect_sqlite_query_evidence(
        execution_id="exec_restore_sqlite",
        result=_sqlite_result(source_frame),
        output_asset=asset,
        source_asset_id=source_asset.asset_id,
    )
    canonical = canonical_from_sqlite_query_evidence(evidence, task_id=result.task_id)
    registered = runtime.register_canonical_evidence(canonical)
    runtime.verify_canonical_evidence(registered.evidence_id)
    runtime._persist()
    state = _read_state(runtime.workspace_dir)
    canonical_payload = state["canonical_evidence"][0]
    canonical_payload["source_metadata"]["content_hash"] = "b" * 64
    _write_state(runtime.workspace_dir, state)
    restored = AnalysisRuntime(
        workspace_dir=workspace, session_id="restore", question="q"
    )
    assert restored.evidence_registry.get(registered.evidence_id).validation_status == "failed"


def test_verified_legacy_evidence_is_downgraded(tmp_path):
    runtime, asset, _ = _new_runtime(tmp_path)
    item = EvidenceItem(
        evidence_id="evidence_restore_legacy",
        evidence_text="value is 1",
        stats={"value": 1.0},
        source_asset_ids=[asset.asset_id],
        verification_status="verified",
    )
    runtime.evidence.append(item)
    runtime._persist()
    restored = AnalysisRuntime(
        workspace_dir=runtime.workspace_dir, session_id="restore", question="q"
    )
    assert restored.evidence[0].verification_status == "unverified"
    assert restored.evidence[0].verification_result_id == "restore_evidence_evidence_restore_legacy"


def test_legacy_evidence_with_missing_asset_fails(tmp_path):
    runtime, _, _ = _new_runtime(tmp_path)
    item = EvidenceItem(
        evidence_id="evidence_restore_unknown",
        evidence_text="value is 1",
        source_asset_ids=["asset_missing"],
        verification_status="verified",
    )
    runtime.evidence.append(item)
    runtime._persist()
    restored = AnalysisRuntime(
        workspace_dir=runtime.workspace_dir, session_id="restore", question="q"
    )
    assert restored.evidence[0].verification_status == "failed"


def test_legacy_evidence_with_unverifiable_asset_is_not_verified(tmp_path):
    runtime, asset, _ = _new_runtime(tmp_path)
    asset.content_hash = None
    runtime.evidence.append(
        EvidenceItem(
            evidence_id="evidence_restore_unverifiable",
            evidence_text="value is 1",
            source_asset_ids=[asset.asset_id],
            verification_status="verified",
        )
    )
    runtime._persist()
    restored = AnalysisRuntime(
        workspace_dir=runtime.workspace_dir, session_id="restore", question="q"
    )
    assert restored.evidence[0].verification_status == "unverified"


def test_restore_verification_records_are_replaced_not_duplicated(tmp_path):
    runtime, _, _ = _new_runtime(tmp_path)
    runtime.evidence.extend(
        [
            EvidenceItem(
                evidence_id="evidence_restore_one",
                evidence_text="first",
                verification_status="verified",
            ),
            EvidenceItem(
                evidence_id="evidence_restore_two",
                evidence_text="second",
                verification_status="verified",
            ),
        ]
    )
    runtime._persist()
    first = AnalysisRuntime(
        workspace_dir=runtime.workspace_dir, session_id="restore", question="q"
    )
    second = AnalysisRuntime(
        workspace_dir=runtime.workspace_dir, session_id="restore", question="q"
    )
    restore_results = [
        item
        for item in second.verifications
        if item.verification_id.startswith("restore_evidence_")
    ]
    assert {item.evidence_id for item in restore_results} == {
        "evidence_restore_one",
        "evidence_restore_two",
    }


def test_old_snapshot_without_canonical_records_restores(tmp_path):
    runtime, asset, _ = _new_runtime(tmp_path)
    runtime.evidence.append(
        EvidenceItem(
            evidence_id="evidence_old_snapshot",
            evidence_text="legacy value",
            source_asset_ids=[asset.asset_id],
            verification_status="verified",
        )
    )
    runtime._persist()
    state = _read_state(runtime.workspace_dir)
    state.pop("canonical_evidence", None)
    _write_state(runtime.workspace_dir, state)
    restored = AnalysisRuntime(
        workspace_dir=runtime.workspace_dir, session_id="restore", question="q"
    )
    assert restored.evidence_registry.records() == []
    assert restored.evidence[0].verification_status == "unverified"


def test_v2_report_excludes_failed_restored_evidence(tmp_path):
    runtime, input_asset, _ = _new_runtime(tmp_path)
    output_path = runtime.workspace_dir / "operation.parquet"
    pd.DataFrame({"value": [2.0]}).to_parquet(output_path, index=False)
    _, evidence = _operation_evidence(
        runtime, input_asset=input_asset, output_path=output_path
    )
    output_path.write_bytes(b"tampered")
    runtime._persist()
    restored = AnalysisRuntime(
        workspace_dir=runtime.workspace_dir, session_id="restore", question="q"
    )
    assert restored.evidence_registry.get(evidence.evidence_id).validation_status == "failed"
    assert restored.build_verified_report() == (None, None)


def test_v2_report_discloses_degraded_restored_asset(tmp_path):
    workspace = tmp_path / "workspace"
    runtime = AnalysisRuntime(workspace_dir=workspace, session_id="restore", question="q")
    source_path = workspace / "source.csv"
    frame = pd.DataFrame({"period": ["2024"], "value": [1.0]})
    source_path.write_text(frame.to_csv(index=False), encoding="utf-8")
    source_asset = runtime.register_dataframe(dataframe=frame, source_path=source_path)
    asset = _register_sqlite_asset(runtime)
    runtime.record_execution(
        tool_name="run_sqlite_query",
        status="succeeded",
        execution_id="exec_restore_sqlite",
    )
    execution = runtime._lookup_execution("exec_restore_sqlite")
    evidence = collect_sqlite_query_evidence(
        execution_id="exec_restore_sqlite",
        result=_sqlite_result(frame),
        output_asset=asset,
        source_asset_id=source_asset.asset_id,
    )
    registered = runtime.register_canonical_evidence(
        canonical_from_sqlite_query_evidence(evidence, task_id=execution.task_id)
    )
    runtime.verify_canonical_evidence(registered.evidence_id)
    degraded_path = runtime.workspace_dir / "degraded.csv"
    pd.DataFrame({"period": ["2025"], "value": [3.0]}).to_csv(degraded_path, index=False)
    runtime.register_dataframe(
        dataframe=pd.read_csv(degraded_path), source_path=degraded_path
    )
    degraded_path.write_text("period,value\n2025,999\n", encoding="utf-8")
    runtime._persist()
    restored = AnalysisRuntime(
        workspace_dir=workspace, session_id="restore", question="q"
    )
    assert restored.evidence_registry.get(registered.evidence_id).validation_status == "verified"
    report, rendered = restored.build_verified_report()
    assert report is not None
    assert any("STALE" in limitation for limitation in report.limitations)


def test_v1_report_validator_uses_downgraded_legacy_status(tmp_path):
    runtime, asset, _ = _new_runtime(tmp_path)
    item = EvidenceItem(
        evidence_id="evidence_restore_v1",
        evidence_text="value is 1",
        stats={"value": 1.0},
        source_asset_ids=[asset.asset_id],
        verification_status="verified",
    )
    runtime.evidence.append(item)
    runtime._persist()
    restored = AnalysisRuntime(
        workspace_dir=runtime.workspace_dir, session_id="restore", question="q"
    )
    finding = Finding(
        finding_id="finding_restore_v1",
        statement="The value is 1",
        stats={"value": 1.0},
        evidence=[restored.evidence[0]],
        supported_by=["evidence_restore_v1"],
    )
    result = validate_report(
        "The value is 1",
        findings=[finding],
        evidence=restored.evidence,
    )
    assert not result.is_valid

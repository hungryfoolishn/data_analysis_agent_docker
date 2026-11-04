from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from langgraph_langchain.data.database import (
    SQLiteConnectionConfig,
    SQLiteDatabase,
    SQLiteDataSource,
)
from langgraph_langchain.data.snapshots import SnapshotReference
from langgraph_langchain.execution.python_task_executor import PythonTaskExecutor
from langgraph_langchain.runtime.asset_resolver import AssetResolutionError
from langgraph_langchain.runtime.context import AnalysisRuntime
from langgraph_langchain.schemas import AnalysisTask
from tests.fixtures.phase1.golden.database.golden_sqlite import (
    create_golden_sqlite,
)


def _source(path: Path) -> SQLiteDataSource:
    return SQLiteDataSource(SQLiteDatabase(SQLiteConnectionConfig(database_path=path)))


def _new_runtime(workspace_dir: Path) -> AnalysisRuntime:
    return AnalysisRuntime(
        workspace_dir=workspace_dir,
        session_id="sqlite-recovery",
        question="Recover and consume SQLite snapshots",
    )


def _task(asset_id: str) -> AnalysisTask:
    return AnalysisTask(
        session_id="sqlite-recovery",
        question="Use recovered SQLite result",
        input_asset_ids=[asset_id],
    )


def _register_orders(runtime: AnalysisRuntime, source_path: Path):
    result = _source(source_path).execute_bounded_query(
        """
        SELECT o.order_id, o.amount, c.name
        FROM orders AS o
        JOIN customers AS c ON c.customer_id = o.customer_id
        ORDER BY o.order_id
        """
    )
    return runtime.register_sqlite_result(result=result), result


def test_register_restart_resolve_and_execute_golden_sqlite(tmp_path: Path):
    source_path = create_golden_sqlite(tmp_path / "golden.sqlite")
    first = _new_runtime(tmp_path)
    asset, result = _register_orders(first, source_path)

    second = _new_runtime(tmp_path)
    assert asset.asset_id in second.assets
    restored = second.assets[asset.asset_id]
    assert restored.source_metadata == asset.source_metadata
    assert restored.content_hash == asset.content_hash
    assert asset.asset_id in second.task.input_asset_ids

    resolved = second.asset_resolver.resolve(asset.asset_id)
    pd.testing.assert_frame_equal(resolved.dataframe, result.dataframe)
    assert resolved.truncated is False
    assert resolved.source_type == "sqlite"
    assert resolved.query_fingerprint == (
        restored.source_metadata["query_fingerprint"]
    )

    # Remove the database to prove execution consumes the snapshot, not SQL.
    source_path.unlink()
    task = _task(asset.asset_id)
    request = second.build_execution_request(task)
    request.code = (
        "frame = runtime_inputs['" + asset.asset_id + "']\n"
        "assert frame['order_id'].tolist() == [1, 2, 3, 4, 5, 6]\n"
        "print('RECOVERY_OK')"
    )
    execution = PythonTaskExecutor(workspace_dir=tmp_path).execute(request)

    assert execution.status == "succeeded"
    assert "RECOVERY_OK" in execution.stdout_preview
    assert execution.input_asset_ids == [asset.asset_id]


def test_repeated_loading_after_restart_is_deterministic(tmp_path: Path):
    source_path = create_golden_sqlite(tmp_path / "golden.sqlite")
    first = _new_runtime(tmp_path)
    asset, result = _register_orders(first, source_path)

    second = _new_runtime(tmp_path)
    first_resolved = second.asset_resolver.resolve(asset.asset_id)
    second_resolved = second.asset_resolver.resolve(asset.asset_id)

    pd.testing.assert_frame_equal(first_resolved.dataframe, result.dataframe)
    pd.testing.assert_frame_equal(
        first_resolved.dataframe, second_resolved.dataframe
    )
    assert first_resolved.provenance == second_resolved.provenance


def test_registration_after_restart_reuses_snapshot(tmp_path: Path):
    source_path = create_golden_sqlite(tmp_path / "golden.sqlite")
    first = _new_runtime(tmp_path)
    asset, result = _register_orders(first, source_path)
    original_reference = SnapshotReference.model_validate(
        asset.source_metadata["snapshot_reference"]
    )

    second = _new_runtime(tmp_path)
    reused = second.register_sqlite_result(result=result)

    assert reused.asset_id == asset.asset_id
    assert reused.source_metadata["snapshot_reference"] == (
        asset.source_metadata["snapshot_reference"]
    )
    reference = SnapshotReference.model_validate(
        reused.source_metadata["snapshot_reference"]
    )
    assert reference.snapshot_id == original_reference.snapshot_id


def test_missing_snapshot_after_restart_fails_explicitly(tmp_path: Path):
    source_path = create_golden_sqlite(tmp_path / "golden.sqlite")
    first = _new_runtime(tmp_path)
    asset, _ = _register_orders(first, source_path)
    reference = SnapshotReference.model_validate(
        asset.source_metadata["snapshot_reference"]
    )
    (tmp_path / ".data_snapshots" / reference.snapshot_location).unlink()

    second = _new_runtime(tmp_path)
    with pytest.raises(AssetResolutionError, match="Unable to load snapshot"):
        second.asset_resolver.resolve(asset.asset_id)


def test_snapshot_hash_mismatch_after_restart_fails_explicitly(tmp_path: Path):
    source_path = create_golden_sqlite(tmp_path / "golden.sqlite")
    first = _new_runtime(tmp_path)
    asset, _ = _register_orders(first, source_path)
    payload = dict(asset.source_metadata["snapshot_reference"])
    payload["snapshot_hash"] = "0" * 64
    first.assets[asset.asset_id] = asset.model_copy(
        update={
            "source_metadata": {
                **asset.source_metadata,
                "snapshot_reference": payload,
            }
        }
    )
    first._persist()

    second = _new_runtime(tmp_path)
    with pytest.raises(AssetResolutionError, match="Unable to load snapshot"):
        second.asset_resolver.resolve(asset.asset_id)


def test_invalid_snapshot_reference_after_restart_fails_explicitly(
    tmp_path: Path,
):
    source_path = create_golden_sqlite(tmp_path / "golden.sqlite")
    first = _new_runtime(tmp_path)
    asset, _ = _register_orders(first, source_path)
    invalid_reference = {
        "snapshot_id": "not-valid",
        "snapshot_format": "pickle",
        "snapshot_location": "../../outside.parquet",
        "snapshot_hash": "not-a-hash",
        "snapshot_hash_type": "md5",
        "content_hash": "not-a-hash",
        "row_count": -1,
        "column_count": -1,
        "created_at": "not-a-time",
    }
    first.assets[asset.asset_id] = asset.model_copy(
        update={
            "source_metadata": {
                **asset.source_metadata,
                "snapshot_reference": invalid_reference,
            }
        }
    )
    first._persist()

    second = _new_runtime(tmp_path)
    with pytest.raises(
        AssetResolutionError, match="invalid snapshot reference"
    ):
        second.asset_resolver.resolve(asset.asset_id)


def test_corrupt_runtime_metadata_starts_without_assets(tmp_path: Path):
    source_path = create_golden_sqlite(tmp_path / "golden.sqlite")
    first = _new_runtime(tmp_path)
    asset, _ = _register_orders(first, source_path)
    (tmp_path / ".analysis_runtime.json").write_text("{not json", encoding="utf-8")

    second = _new_runtime(tmp_path)

    assert asset.asset_id not in second.assets
    assert second.task.input_asset_ids == []
    with pytest.raises(AssetResolutionError, match="not registered"):
        second.asset_resolver.resolve(asset.asset_id)


def test_runtime_persist_failure_after_restart_keeps_existing_snapshot(
    tmp_path: Path, monkeypatch
):
    source_path = create_golden_sqlite(tmp_path / "golden.sqlite")
    first = _new_runtime(tmp_path)
    asset, result = _register_orders(first, source_path)
    reference = SnapshotReference.model_validate(
        asset.source_metadata["snapshot_reference"]
    )

    second = _new_runtime(tmp_path)
    second.task.input_asset_ids.remove(asset.asset_id)

    def fail_persist():
        raise OSError("simulated persistence failure")

    monkeypatch.setattr(second, "_persist", fail_persist)
    with pytest.raises(OSError, match="simulated persistence failure"):
        second.register_sqlite_result(result=result)

    assert second.assets[asset.asset_id] == asset
    assert asset.asset_id not in second.task.input_asset_ids
    assert (tmp_path / ".data_snapshots" / reference.snapshot_location).is_file()

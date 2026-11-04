from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from langgraph_langchain.data.assets import DataAsset, hash_dataframe
from langgraph_langchain.data.database import (
    SQLiteConnectionConfig,
    SQLiteDatabase,
    SQLiteDataSource,
)
from langgraph_langchain.data.snapshots import (
    SnapshotError,
    SnapshotReference,
    SnapshotStore,
    new_snapshot_id,
)
from langgraph_langchain.execution.python_task_executor import PythonTaskExecutor
from langgraph_langchain.runtime.asset_resolver import (
    AssetNotFoundError,
    AssetResolutionError,
    AssetResolver,
    AssetUnsupportedError,
)
from langgraph_langchain.runtime.context import AnalysisRuntime
from langgraph_langchain.schemas import AnalysisTask


def _database(path: Path, **config):
    return SQLiteDatabase(SQLiteConnectionConfig(database_path=path, **config))


def _source(path: Path, **config) -> SQLiteDataSource:
    return SQLiteDataSource(_database(path, **config))


def _create_items(path: Path, row_count: int = 2) -> Path:
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "CREATE TABLE items (id INTEGER PRIMARY KEY, value TEXT)"
        )
        connection.executemany(
            "INSERT INTO items (id, value) VALUES (?, ?)",
            [(index, f"row-{index}") for index in range(1, row_count + 1)],
        )
        connection.commit()
    finally:
        connection.close()
    return path


def _asset(
    frame: pd.DataFrame,
    store: SnapshotStore,
    *,
    asset_id: str = "asset_1",
    truncated: bool = False,
    fingerprint: str = "a" * 64,
) -> DataAsset:
    reference = store.save(frame, snapshot_id=new_snapshot_id())
    return DataAsset.from_sqlite_query(
        dataframe=frame,
        source_identity="sqlite:sha256:" + "b" * 64,
        query_fingerprint=fingerprint,
        query_type="sql",
        selected_columns=tuple(frame.columns),
        truncated=truncated,
        source_metadata={"snapshot_reference": reference.model_dump(mode="json")},
    ).model_copy(update={"asset_id": asset_id})


def test_resolver_loads_single_verified_snapshot(tmp_path: Path):
    frame = pd.DataFrame(
        {
            "number": [1.5, None],
            "text": ["a", None],
            "day": [pd.Timestamp("2026-01-01"), None],
        }
    )
    store = SnapshotStore(tmp_path / ".data_snapshots")
    asset = _asset(frame, store)
    resolver = AssetResolver(assets={asset.asset_id: asset}, snapshot_store=store)

    resolved = resolver.resolve(asset.asset_id)

    pd.testing.assert_frame_equal(resolved.dataframe, frame)
    assert resolved.source_type == "sqlite"
    assert resolved.row_count == 2
    assert resolved.query_fingerprint == "a" * 64
    assert resolved.snapshot_reference.content_hash == hash_dataframe(frame)
    assert resolved.provenance["selected_columns"] == [
        "number",
        "text",
        "day",
    ]


def test_resolver_loads_multiple_assets_in_order(tmp_path: Path):
    store = SnapshotStore(tmp_path / ".data_snapshots")
    first = _asset(pd.DataFrame({"value": [1]}), store, asset_id="asset_1")
    second = _asset(
        pd.DataFrame({"region": ["east"]}), store, asset_id="asset_2"
    )
    resolver = AssetResolver(
        assets={"asset_1": first, "asset_2": second}, snapshot_store=store
    )

    resolved = resolver.resolve_many(["asset_2", "asset_1", "asset_2"])

    assert list(resolved) == ["asset_2", "asset_1"]
    assert resolved["asset_1"].dataframe["value"].tolist() == [1]
    assert resolved["asset_2"].dataframe["region"].tolist() == ["east"]


def test_missing_asset_fails_explicitly(tmp_path: Path):
    resolver = AssetResolver(
        assets={}, snapshot_store=SnapshotStore(tmp_path / "snapshots")
    )

    with pytest.raises(AssetNotFoundError, match="not registered"):
        resolver.resolve("asset_missing")


def test_asset_without_snapshot_reference_is_unsupported(tmp_path: Path):
    store = SnapshotStore(tmp_path / "snapshots")
    frame = pd.DataFrame({"value": [1]})
    asset = DataAsset.from_sqlite_query(
        dataframe=frame,
        source_identity="sqlite:sha256:" + "b" * 64,
        query_fingerprint="a" * 64,
        query_type="sql",
    ).model_copy(update={"asset_id": "asset_1"})
    resolver = AssetResolver(assets={"asset_1": asset}, snapshot_store=store)

    with pytest.raises(AssetUnsupportedError, match="no snapshot reference"):
        resolver.resolve("asset_1")


def test_invalid_snapshot_reference_is_unsupported(tmp_path: Path):
    store = SnapshotStore(tmp_path / "snapshots")
    frame = pd.DataFrame({"value": [1]})
    asset = DataAsset.from_sqlite_query(
        dataframe=frame,
        source_identity="sqlite:sha256:" + "b" * 64,
        query_fingerprint="a" * 64,
        query_type="sql",
        source_metadata={"snapshot_reference": {"snapshot_id": "invalid"}},
    ).model_copy(update={"asset_id": "asset_1"})
    resolver = AssetResolver(assets={"asset_1": asset}, snapshot_store=store)

    with pytest.raises(AssetUnsupportedError, match="invalid snapshot reference"):
        resolver.resolve("asset_1")


def test_missing_snapshot_file_fails_without_sql_execution(tmp_path: Path):
    store = SnapshotStore(tmp_path / "snapshots")
    asset = _asset(pd.DataFrame({"value": [1]}), store)
    store.delete(
        SnapshotReference.model_validate(
            asset.source_metadata["snapshot_reference"]
        )
    )
    resolver = AssetResolver(assets={"asset_1": asset}, snapshot_store=store)

    with pytest.raises(AssetResolutionError, match="Unable to load snapshot"):
        resolver.resolve("asset_1")


def test_hash_mismatch_is_rejected(tmp_path: Path):
    store = SnapshotStore(tmp_path / "snapshots")
    asset = _asset(pd.DataFrame({"value": [1]}), store)
    payload = dict(asset.source_metadata["snapshot_reference"])
    payload["snapshot_hash"] = "0" * 64
    asset = asset.model_copy(
        update={
            "source_metadata": {
                **asset.source_metadata,
                "snapshot_reference": payload,
            }
        }
    )
    resolver = AssetResolver(assets={"asset_1": asset}, snapshot_store=store)

    with pytest.raises(AssetResolutionError, match="Unable to load snapshot"):
        resolver.resolve("asset_1")


def test_path_traversal_is_rejected_without_leaking_path(tmp_path: Path):
    store = SnapshotStore(tmp_path / "snapshots")
    asset = _asset(pd.DataFrame({"value": [1]}), store)
    payload = dict(asset.source_metadata["snapshot_reference"])
    payload["snapshot_location"] = f"../{payload['snapshot_id']}.parquet"
    asset = asset.model_copy(
        update={
            "source_metadata": {
                **asset.source_metadata,
                "snapshot_reference": payload,
            }
        }
    )
    resolver = AssetResolver(assets={"asset_1": asset}, snapshot_store=store)

    with pytest.raises(AssetResolutionError, match="Unable to load snapshot") as exc:
        resolver.resolve("asset_1")

    assert str(store.root) not in str(exc.value)


def test_snapshot_store_error_is_normalized(tmp_path: Path, monkeypatch):
    store = SnapshotStore(tmp_path / "snapshots")
    asset = _asset(pd.DataFrame({"value": [1]}), store)
    resolver = AssetResolver(assets={"asset_1": asset}, snapshot_store=store)
    secret_path = str(tmp_path / "private.sqlite")

    def fail_load(reference):
        raise SnapshotError(f"database unavailable: {secret_path}")

    monkeypatch.setattr(store, "load", fail_load)
    with pytest.raises(AssetResolutionError, match="Unable to load snapshot") as exc:
        resolver.resolve("asset_1")

    assert secret_path not in str(exc.value)


def test_runtime_builds_request_with_resolved_inputs(tmp_path: Path):
    db_path = _create_items(tmp_path / "items.sqlite")
    result = _source(db_path).execute_bounded_query(
        "SELECT id, value FROM items ORDER BY id"
    )
    runtime = AnalysisRuntime(
        workspace_dir=tmp_path,
        session_id="resolver-e2e",
        question="Consume SQLite snapshots",
    )
    asset = runtime.register_sqlite_result(result=result)
    task = AnalysisTask(
        session_id="resolver-e2e",
        question="Use the registered SQLite result",
        input_asset_ids=[asset.asset_id],
    )

    request = runtime.build_execution_request(task)

    assert request.metadata == {}
    assert list(request.namespace["runtime_inputs"]) == [asset.asset_id]
    pd.testing.assert_frame_equal(
        request.namespace["runtime_inputs"][asset.asset_id],
        result.dataframe,
    )
    provenance = request.namespace["runtime_input_metadata"][asset.asset_id]
    assert provenance["source_type"] == "sqlite"
    assert provenance["truncated"] is False
    assert provenance["query_fingerprint"] == (
        asset.source_metadata["query_fingerprint"]
    )


def test_missing_runtime_input_becomes_failed_execution(tmp_path: Path):
    runtime = AnalysisRuntime(
        workspace_dir=tmp_path,
        session_id="resolver-e2e",
        question="Missing input",
    )
    task = AnalysisTask(
        session_id="resolver-e2e",
        question="Use a missing asset",
        input_asset_ids=["asset_missing"],
    )

    request = runtime.build_execution_request(task)

    assert "runtime_inputs" not in request.namespace
    assert request.metadata["asset_resolution_error"]["type"] == (
        "AssetNotFoundError"
    )


def test_reserved_input_namespace_conflict_fails(tmp_path: Path):
    db_path = _create_items(tmp_path / "items.sqlite")
    result = _source(db_path).execute_bounded_query(
        "SELECT id, value FROM items ORDER BY id"
    )
    runtime = AnalysisRuntime(
        workspace_dir=tmp_path,
        session_id="resolver-e2e",
        question="Conflict",
    )
    asset = runtime.register_sqlite_result(result=result)
    runtime.execution_namespace["runtime_inputs"] = {}
    task = AnalysisTask(
        session_id="resolver-e2e",
        question="Use asset",
        input_asset_ids=[asset.asset_id],
    )

    request = runtime.build_execution_request(task)

    assert "runtime_inputs" not in request.namespace
    assert request.metadata["asset_resolution_error"]["type"] == (
        "AssetInputConflictError"
    )


def test_python_executor_uses_snapshot_dataframe(tmp_path: Path):
    db_path = _create_items(tmp_path / "items.sqlite")
    result = _source(db_path).execute_bounded_query(
        "SELECT id, value FROM items ORDER BY id"
    )
    runtime = AnalysisRuntime(
        workspace_dir=tmp_path,
        session_id="resolver-e2e",
        question="Execute with snapshot",
    )
    asset = runtime.register_sqlite_result(result=result)
    # Deleting the source database proves executor input cannot come from SQL.
    db_path.unlink()
    task = AnalysisTask(
        session_id="resolver-e2e",
        question="Use the registered SQLite result",
        input_asset_ids=[asset.asset_id],
    )
    request = runtime.build_execution_request(task)
    request.code = (
        "frame = runtime_inputs['" + asset.asset_id + "']\n"
        "assert frame['value'].tolist() == ['row-1', 'row-2']\n"
        "print('SNAPSHOT_OK')"
    )

    execution = PythonTaskExecutor(workspace_dir=tmp_path).execute(request)

    assert execution.status == "succeeded"
    assert "SNAPSHOT_OK" in execution.stdout_preview
    assert execution.input_asset_ids == [asset.asset_id]

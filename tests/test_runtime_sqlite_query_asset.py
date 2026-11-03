from __future__ import annotations

import json
import sqlite3
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from langgraph_langchain.data.assets import DataAsset, hash_dataframe
from langgraph_langchain.data.database import (
    SQLiteConnectionConfig,
    SQLiteDatabase,
    SQLiteDataSource,
)
from langgraph_langchain.runtime.context import AnalysisRuntime


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


def test_sqlite_source_identity_is_stable(tmp_path: Path):
    db_path = _create_items(tmp_path / "items.sqlite")

    first = _source(db_path)
    second = _source(db_path)
    configured = _source(db_path, source_identity="sqlite:explicit")

    assert first.source_identity == second.source_identity
    assert first.source_identity.startswith("sqlite:sha256:")
    assert len(first.source_identity.split(":")[-1]) == 64
    assert configured.source_identity == "sqlite:explicit"
    assert str(db_path) not in first.source_identity


def test_query_fingerprint_distinguishes_sql_parameters_and_mode(tmp_path: Path):
    db_path = _create_items(tmp_path / "items.sqlite")
    source = _source(db_path)

    first = source.execute_bounded_query(
        "SELECT id FROM items WHERE id = ?", parameters=(1,)
    )
    repeated = source.execute_bounded_query(
        "SELECT id\nFROM items\nWHERE id = ?", parameters=(1,)
    )
    different_parameter = source.execute_bounded_query(
        "SELECT id FROM items WHERE id = ?", parameters=(2,)
    )
    table = source.materialize_with_metadata(table_name="items")
    query = source.execute_bounded_query("SELECT id FROM items")

    assert first.metadata.query_sha256 == repeated.metadata.query_sha256
    assert first.metadata.query_sha256 != different_parameter.metadata.query_sha256
    assert table.metadata.query_type == "table"
    assert query.metadata.query_type == "sql"
    assert table.metadata.query_sha256 != query.metadata.query_sha256


def test_sql_fingerprint_preserves_quoted_whitespace(tmp_path: Path):
    db_path = _create_items(tmp_path / "items.sqlite")
    source = _source(db_path)

    wide = source.execute_bounded_query("SELECT 'a  b' AS text")
    narrow = source.execute_bounded_query("SELECT 'a b' AS text")
    repeated = source.execute_bounded_query("SELECT 'a  b' AS text")

    assert wide.metadata.query_sha256 != narrow.metadata.query_sha256
    assert wide.metadata.query_sha256 == repeated.metadata.query_sha256


def test_query_metadata_does_not_retain_raw_parameters(tmp_path: Path):
    db_path = _create_items(tmp_path / "items.sqlite")
    source = _source(db_path)
    secret = "secret-sensitive-value"

    result = source.execute_bounded_query(
        "SELECT id FROM items WHERE value = ?", parameters=(secret,)
    )

    payload = json.dumps(result.metadata.to_dict(), ensure_ascii=False)
    assert secret not in payload


def test_hash_dataframe_is_stable_and_structural(tmp_path: Path):
    frame = pd.DataFrame(
        {
            "text": ["a", None],
            "number": [1.5, 2],
            "boolean": [True, False],
            "bytes": [b"x", b"y"],
            "decimal": [Decimal("1.25"), None],
            "day": [pd.Timestamp("2026-01-01"), pd.Timestamp("2026-01-02")],
        }
    )
    same = frame.copy(deep=True)
    empty = pd.DataFrame(
        {"number": pd.Series(dtype="int64"), "text": pd.Series(dtype="object")}
    )
    with_null = pd.DataFrame(
        {"value": pd.array([1, pd.NA], dtype="Int64")}
    )

    assert hash_dataframe(frame) == hash_dataframe(same)
    assert hash_dataframe(frame) != hash_dataframe(frame[list(frame.columns[::-1])])
    assert hash_dataframe(frame) != hash_dataframe(frame.iloc[::-1])
    assert hash_dataframe(empty)
    assert hash_dataframe(with_null)


def test_hash_dataframe_rejects_unsupported_values():
    frame = pd.DataFrame({"object": [{"unsupported": True}]})

    with pytest.raises(TypeError, match="Unsupported DataFrame value"):
        hash_dataframe(frame)


def _sqlite_result(db_path: Path):
    return _source(db_path).execute_bounded_query(
        "SELECT id, value FROM items ORDER BY id"
    )


def test_runtime_registers_sqlite_query_asset(tmp_path: Path):
    db_path = _create_items(tmp_path / "items.sqlite")
    result = _sqlite_result(db_path)
    runtime = AnalysisRuntime(
        workspace_dir=tmp_path,
        session_id="sqlite-asset",
        question="Load SQLite query results",
    )

    asset = runtime.register_sqlite_result(result=result)

    assert isinstance(asset, DataAsset)
    assert asset.source_type == "sqlite"
    assert asset.location is None
    assert asset.content_hash == hash_dataframe(result.dataframe)
    assert asset.name == "sqlite_query"
    assert asset.source_metadata["source_identity"] == (
        result.metadata.source_identity
    )
    assert asset.source_metadata["query_type"] == "sql"
    assert asset.source_metadata["query_fingerprint"] == result.metadata.query_sha256
    assert asset.source_metadata["table_name"] is None
    assert asset.source_metadata["truncated"] is False
    assert asset.asset_id in runtime.task.input_asset_ids

    asset_payload = asset.model_dump_json()
    assert str(db_path) not in asset_payload


def test_sqlite_query_asset_persists_and_reloads(tmp_path: Path):
    db_path = _create_items(tmp_path / "items.sqlite")
    result = _sqlite_result(db_path)
    first = AnalysisRuntime(
        workspace_dir=tmp_path,
        session_id="sqlite-asset",
        question="Load SQLite query results",
    )
    asset = first.register_sqlite_result(result=result)

    second = AnalysisRuntime(
        workspace_dir=tmp_path,
        session_id="sqlite-asset",
        question="Load SQLite query results",
    )

    assert asset.asset_id in second.assets
    reloaded = second.assets[asset.asset_id]
    assert reloaded.source_metadata == asset.source_metadata
    assert reloaded.content_hash == asset.content_hash
    assert asset.asset_id in second.task.input_asset_ids


def test_sqlite_asset_dedup_preserves_provenance(tmp_path: Path):
    db_path = _create_items(tmp_path / "items.sqlite")
    source = _source(db_path)
    runtime = AnalysisRuntime(
        workspace_dir=tmp_path,
        session_id="sqlite-asset",
        question="Deduplicate SQLite query results",
    )

    first_result = source.execute_bounded_query(
        "SELECT id, value FROM items ORDER BY id"
    )
    second_result = source.execute_bounded_query(
        "SELECT id, value FROM items ORDER BY id LIMIT 2"
    )
    first = runtime.register_sqlite_result(result=first_result)
    reused = runtime.register_sqlite_result(result=first_result)
    different_source = runtime.register_sqlite_result(result=second_result)

    assert reused.asset_id == first.asset_id
    assert different_source.asset_id != first.asset_id
    assert different_source.content_hash == first.content_hash
    assert different_source.source_metadata != first.source_metadata
    assert {first.asset_id, different_source.asset_id}.issubset(
        runtime.task.input_asset_ids
    )

    same_source = source.execute_bounded_query(
        "SELECT id, value FROM items ORDER BY id"
    )
    same_source.dataframe.loc[0, "value"] = "changed"
    changed_content = runtime.register_sqlite_result(result=same_source)

    assert changed_content.asset_id != first.asset_id
    assert changed_content.source_metadata["query_fingerprint"] == (
        first.source_metadata["query_fingerprint"]
    )
    assert changed_content.content_hash != first.content_hash


def test_failed_sqlite_asset_registration_rolls_back(tmp_path: Path, monkeypatch):
    db_path = _create_items(tmp_path / "items.sqlite")
    result = _sqlite_result(db_path)
    runtime = AnalysisRuntime(
        workspace_dir=tmp_path,
        session_id="sqlite-asset",
        question="Roll back failed registration",
    )

    def fail_persist():
        raise OSError("simulated persistence failure")

    monkeypatch.setattr(runtime, "_persist", fail_persist)
    with pytest.raises(OSError, match="simulated persistence failure"):
        runtime.register_sqlite_result(result=result)

    assert runtime.assets == {}
    assert runtime.task.input_asset_ids == []



def test_failed_sqlite_asset_reuse_rolls_back_task_link(
    tmp_path: Path, monkeypatch
):
    db_path = _create_items(tmp_path / "items.sqlite")
    result = _sqlite_result(db_path)
    runtime = AnalysisRuntime(
        workspace_dir=tmp_path,
        session_id="sqlite-asset",
        question="Roll back failed asset reuse",
    )
    asset = runtime.register_sqlite_result(result=result)
    runtime.task.input_asset_ids.remove(asset.asset_id)

    def fail_persist():
        raise OSError("simulated persistence failure")

    monkeypatch.setattr(runtime, "_persist", fail_persist)
    with pytest.raises(OSError, match="simulated persistence failure"):
        runtime.register_sqlite_result(result=result)

    assert runtime.assets[asset.asset_id] is asset
    assert asset.asset_id not in runtime.task.input_asset_ids

from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from langgraph_langchain.data.database import (
    BoundedReadResult,
    DatabaseQueryValidationError,
    ResultSizeLimitExceededError,
    QueryTimeoutError,
    SQLiteConnectionConfig,
    SQLiteDatabase,
    SQLiteDataSource,
)
from tests.fixtures.phase1.golden.database.golden_sqlite import (
    create_golden_sqlite,
)


def _database(path: Path, **config) -> SQLiteDatabase:
    return SQLiteDatabase(SQLiteConnectionConfig(database_path=path, **config))


def _source(path: Path, **config) -> SQLiteDataSource:
    return SQLiteDataSource(_database(path, **config))


def _create_items(path: Path, row_count: int = 5) -> Path:
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, value TEXT)")
        connection.executemany(
            "INSERT INTO items (id, value) VALUES (?, ?)",
            [(index, f"row-{index}") for index in range(1, row_count + 1)],
        )
        connection.commit()
    finally:
        connection.close()
    return path


def test_table_preview_is_bounded_and_columns_are_projected(tmp_path: Path):
    db_path = create_golden_sqlite(tmp_path / "golden.sqlite")
    source = _source(db_path)

    preview = source.preview(rows=2, table_name="orders")

    assert len(preview) == 2
    assert preview.columns.tolist() == [
        "order_id",
        "customer_id",
        "amount",
        "status",
        "order_date",
    ]
    assert preview.iloc[0]["order_id"] == 1

    projected = source.preview(
        rows=2,
        table_name="orders",
        columns=["amount", "order_id"],
        order_by=["order_id DESC"],
    )

    assert projected.columns.tolist() == ["amount", "order_id"]
    assert projected["order_id"].tolist() == [6, 5]


def test_materialize_table_and_query_return_stable_results(tmp_path: Path):
    db_path = create_golden_sqlite(tmp_path / "golden.sqlite")
    source = _source(db_path)

    result = source.materialize_with_metadata(
        table_name="orders", max_rows=4, order_by="order_id"
    )

    assert isinstance(result, BoundedReadResult)
    assert len(result.dataframe) == 4
    assert result.metadata.table_name == "orders"
    assert result.metadata.requested_row_limit == 4
    assert result.metadata.truncated is True
    assert result.metadata.selected_columns == (
        "order_id",
        "customer_id",
        "amount",
        "status",
        "order_date",
    )

    query_result = source.materialize(
        query=(
            "SELECT c.region, COUNT(*) AS orders "
            "FROM orders AS o JOIN customers AS c "
            "ON c.customer_id = o.customer_id "
            "GROUP BY c.region ORDER BY c.region"
        )
    )

    assert query_result["region"].tolist() == [None, "East", "North", "South"]
    assert query_result["orders"].tolist() == [1, 2, 2, 1]


def test_bounded_query_supports_parameters_and_fingerprints_sql(tmp_path: Path):
    db_path = create_golden_sqlite(tmp_path / "golden.sqlite")
    source = _source(db_path)

    result = source.execute_bounded_query(
        "SELECT order_id FROM orders WHERE status = ?", parameters=("pending",)
    )

    assert result.dataframe["order_id"].tolist() == [3, 6]
    assert result.metadata.query_sha256
    assert len(result.metadata.query_sha256) == 64
    assert "?" not in result.metadata.query_sha256


def test_preview_and_materialize_limit_truncation(tmp_path: Path):
    db_path = _create_items(tmp_path / "items.sqlite", row_count=5)
    source = _source(
        db_path,
        preview_max_rows=3,
        materialize_max_rows=3,
        fetch_batch_rows=2,
    )

    assert len(source.preview(rows=3, table_name="items")) == 3
    with pytest.raises(DatabaseQueryValidationError, match="preview rows"):
        source.preview(rows=4, table_name="items")

    materialized = source.materialize_with_metadata(
        table_name="items", max_rows=3
    )
    assert len(materialized.dataframe) == 3
    assert materialized.truncated is True
    assert materialized.metadata.returned_row_count == 3
    assert materialized.metadata.requested_row_limit == 3


def test_empty_table_and_zero_row_query_are_not_truncated(tmp_path: Path):
    db_path = tmp_path / "empty.sqlite"
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("CREATE TABLE empty_table (id INTEGER, name TEXT)")
        connection.commit()
    finally:
        connection.close()
    source = _source(db_path)

    result = source.materialize_with_metadata(table_name="empty_table")
    assert result.dataframe.empty
    assert result.metadata.truncated is False

    query_result = source.execute_bounded_query(
        "SELECT id, name FROM empty_table WHERE id = ?", parameters=(1,)
    )
    assert query_result.dataframe.empty
    assert query_result.dataframe.columns.tolist() == ["id", "name"]
    assert query_result.metadata.truncated is False


def test_special_character_identifiers_are_read_safely(tmp_path: Path):
    db_path = tmp_path / "special.sqlite"
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            'CREATE TABLE "orders #1" ("column #1" TEXT, "q""uote" TEXT)'
        )
        connection.execute('INSERT INTO "orders #1" VALUES (?, ?)', ("a", "b"))
        connection.commit()
    finally:
        connection.close()

    source = _source(db_path)
    preview = source.preview(rows=10, table_name="orders #1")

    assert preview.columns.tolist() == ["column #1", 'q"uote']
    assert preview.iloc[0].tolist() == ["a", "b"]


def test_estimate_rows_distinguishes_exact_estimated_and_unknown(tmp_path: Path):
    analyzed_path = _create_items(tmp_path / "analyzed.sqlite", row_count=7)
    connection = sqlite3.connect(analyzed_path)
    try:
        connection.execute("ANALYZE")
        connection.commit()
    finally:
        connection.close()
    source = _source(analyzed_path)

    estimated = source.estimate_rows("items")
    assert estimated.count is not None
    assert estimated.is_exact is False
    assert estimated.source == "sqlite_stat1"

    exact = source.estimate_rows("items", exact=True)
    assert exact.count == 7
    assert exact.is_exact is True
    assert exact.source == "exact_count"

    unknown_path = _create_items(tmp_path / "unknown.sqlite", row_count=1)
    unknown = _source(unknown_path).estimate_rows("items")
    assert unknown.count is None
    assert unknown.is_exact is False
    assert unknown.source == "unknown"


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO items VALUES (1, 'bad')",
        "UPDATE items SET value = 'bad'",
        "DELETE FROM items",
        "CREATE TABLE injected (id INTEGER)",
        "PRAGMA integrity_check",
    ],
)
def test_write_ddl_and_pragma_queries_are_rejected(tmp_path: Path, sql: str):
    db_path = _create_items(tmp_path / "items.sqlite", row_count=1)
    source = _source(db_path)

    with pytest.raises(DatabaseQueryValidationError):
        source.execute_bounded_query(sql)

    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 1


def test_multiple_statements_are_rejected(tmp_path: Path):
    db_path = _create_items(tmp_path / "items.sqlite", row_count=1)
    source = _source(db_path)

    with pytest.raises(DatabaseQueryValidationError, match="one SQL statement"):
        source.execute_bounded_query("SELECT 1; DELETE FROM items")


def test_timeout_is_reported_and_progress_handler_is_not_inherited(tmp_path: Path):
    db_path = _create_items(tmp_path / "items.sqlite", row_count=1)
    source = _source(db_path, query_timeout_seconds=0.05)

    with pytest.raises(QueryTimeoutError):
        source.execute_bounded_query(
            """
            WITH RECURSIVE counter(x) AS (
                SELECT 1 UNION ALL SELECT x + 1 FROM counter
            )
            SELECT sum(x) FROM counter
            """
        )

    assert source.execute_bounded_query("SELECT 42").dataframe.iloc[0, 0] == 42


def test_result_size_limit_is_explicit(tmp_path: Path):
    db_path = tmp_path / "large.sqlite"
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("CREATE TABLE payload (value TEXT)")
        connection.executemany(
            "INSERT INTO payload VALUES (?)",
            [("x" * 100,) for _ in range(10)],
        )
        connection.commit()
    finally:
        connection.close()
    source = _source(db_path, max_result_bytes=100)

    with pytest.raises(ResultSizeLimitExceededError, match="100 bytes"):
        source.materialize(table_name="payload")


def test_oversized_returned_value_is_rejected(tmp_path: Path):
    db_path = _create_items(tmp_path / "items.sqlite", row_count=1)
    source = _source(db_path, max_result_bytes=16)

    with pytest.raises(ResultSizeLimitExceededError, match="16 bytes"):
        source.execute_bounded_query(
            "SELECT ? AS oversized_value", parameters=("x" * 64,)
        )


@pytest.mark.parametrize("sql", ["SELECT randomblob(64)", "SELECT zeroblob(64)"])
def test_unbounded_value_functions_are_denied(tmp_path: Path, sql: str):
    db_path = _create_items(tmp_path / "items.sqlite", row_count=1)
    source = _source(db_path, max_result_bytes=64)

    with pytest.raises(DatabaseQueryValidationError, match="Blocked SQLite function"):
        source.execute_bounded_query(sql)


class _SpySQLiteDatabase(SQLiteDatabase):
    def __init__(self, config: SQLiteConnectionConfig):
        super().__init__(config)
        self.connections: list[sqlite3.Connection] = []

    def connect_readonly(self) -> sqlite3.Connection:
        connection = super().connect_readonly()
        self.connections.append(connection)
        return connection


def test_failed_bounded_read_closes_connection(tmp_path: Path):
    db_path = _create_items(tmp_path / "items.sqlite", row_count=1)
    database = _SpySQLiteDatabase(
        SQLiteConnectionConfig(database_path=db_path)
    )
    source = SQLiteDataSource(database)

    with pytest.raises(DatabaseQueryValidationError):
        source.execute_bounded_query("SELECT missing_column FROM items")

    assert database.connections
    with pytest.raises(sqlite3.ProgrammingError):
        database.connections[0].execute("SELECT 1")
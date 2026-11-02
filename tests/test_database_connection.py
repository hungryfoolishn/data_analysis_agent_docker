"""Tests for Phase 1 read-only SQLite connection foundations."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlite3

from langgraph_langchain.data import DataSource
from langgraph_langchain.data.database import (
    DatabaseConfigError,
    DatabaseConnectionError,
    DatabaseReadOnlyError,
    SQLiteDatabase,
    SQLiteConnectionConfig,
    SQLiteDataSource,
)
from tests.fixtures.phase1.golden.database.golden_sqlite import (
    create_golden_sqlite,
)


def test_connect_readonly_reads_golden_fixture(tmp_path: Path):
    db_path = create_golden_sqlite(tmp_path / "golden.sqlite")
    database = SQLiteDatabase(
        SQLiteConnectionConfig(database_path=db_path)
    )

    with database.open_readonly() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM customers"
        ).fetchone()[0]

    assert count == 4


def test_connect_readonly_rejects_insert_update_delete_and_ddl(tmp_path: Path):
    db_path = create_golden_sqlite(tmp_path / "golden.sqlite")
    database = SQLiteDatabase(SQLiteConnectionConfig(database_path=db_path))

    with pytest.raises(DatabaseReadOnlyError):
        with database.open_readonly() as connection:
            connection.execute(
                "INSERT INTO customers VALUES (5, 'New', 'West', '2024-01-01')"
            )

    with pytest.raises(DatabaseReadOnlyError):
        with database.open_readonly() as connection:
            connection.execute("UPDATE customers SET name = 'Changed'")

    with pytest.raises(DatabaseReadOnlyError):
        with database.open_readonly() as connection:
            connection.execute("DELETE FROM customers")

    with pytest.raises(DatabaseReadOnlyError):
        with database.open_readonly() as connection:
            connection.execute("CREATE TABLE should_not_exist (id INTEGER)")

    with pytest.raises(DatabaseReadOnlyError):
        with database.open_readonly() as connection:
            connection.execute("DROP TABLE customers")

    with database.open_readonly() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM customers"
        ).fetchone()[0]

    assert count == 4


def test_missing_database_is_not_created(tmp_path: Path):
    db_path = tmp_path / "missing.sqlite"

    with pytest.raises(DatabaseConfigError, match="does not exist"):
        SQLiteConnectionConfig(database_path=db_path)

    assert not db_path.exists()


def test_missing_database_config_is_rejected(tmp_path: Path):
    db_path = tmp_path / "missing.sqlite"

    with pytest.raises(DatabaseConfigError, match="does not exist"):
        SQLiteConnectionConfig(database_path=db_path)

    assert not db_path.exists()


def test_directory_database_path_is_rejected(tmp_path: Path):
    with pytest.raises(DatabaseConfigError, match="not a file"):
        SQLiteConnectionConfig(database_path=tmp_path)


def test_invalid_file_maps_to_connection_error(tmp_path: Path):
    db_path = tmp_path / "corrupt.sqlite"
    db_path.write_text("this is not a SQLite database", encoding="utf-8")
    database = SQLiteDatabase(SQLiteConnectionConfig(database_path=db_path))

    with pytest.raises(DatabaseConnectionError) as exc_info:
        database.connect_readonly()

    assert isinstance(exc_info.value.__cause__, sqlite3.Error)


def test_special_character_paths_are_uri_encoded(tmp_path: Path):
    db_path = tmp_path / "golden #1.sqlite"
    create_golden_sqlite(db_path)
    database = SQLiteDatabase(SQLiteConnectionConfig(database_path=db_path))

    with database.open_readonly() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM orders"
        ).fetchone()[0]

    assert count == 6


def test_context_manager_closes_connection(tmp_path: Path):
    db_path = create_golden_sqlite(tmp_path / "golden.sqlite")
    database = SQLiteDatabase(SQLiteConnectionConfig(database_path=db_path))

    with database.open_readonly() as connection:
        assert connection.execute("SELECT 1").fetchone() == (1,)

    assert connection is not None


def test_multiple_connections_are_isolated(tmp_path: Path):
    db_path = create_golden_sqlite(tmp_path / "golden.sqlite")
    database = SQLiteDatabase(SQLiteConnectionConfig(database_path=db_path))

    with database.open_readonly() as first:
        with database.open_readonly() as second:
            assert first is not second
            assert first.execute("SELECT COUNT(*) FROM customers").fetchone()[0] == 4
            assert second.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 6

    assert first is not second
    assert second is not first


def test_readonly_context_maps_write_error(tmp_path: Path):
    db_path = create_golden_sqlite(tmp_path / "golden.sqlite")
    database = SQLiteDatabase(SQLiteConnectionConfig(database_path=db_path))

    with pytest.raises(DatabaseReadOnlyError) as exc_info:
        with database.open_readonly() as connection:
            connection.execute(
                "INSERT INTO customers VALUES (5, 'New', 'West', '2024-01-01')"
            )

    assert exc_info.value.__cause__ is not None


def test_sqlite_data_source_is_compatible_with_data_source_contract(tmp_path: Path):
    db_path = create_golden_sqlite(tmp_path / "golden.sqlite")
    source = SQLiteDataSource(
        SQLiteDatabase(SQLiteConnectionConfig(database_path=db_path))
    )

    assert isinstance(source, DataSource)
    assert source.source_type == "sqlite"
    assert source.path == db_path

    with source.open_readonly() as connection:
        assert connection.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 6


def test_data_source_defers_query_capabilities(tmp_path: Path):
    db_path = create_golden_sqlite(tmp_path / "golden.sqlite")
    source = SQLiteDataSource(
        SQLiteDatabase(SQLiteConnectionConfig(database_path=db_path))
    )

    with pytest.raises(NotImplementedError, match="M2.5"):
        source.preview()
    with pytest.raises(NotImplementedError, match="M2.5"):
        source.estimate_rows()
    with pytest.raises(NotImplementedError, match="M2.5"):
        source.materialize()


def test_string_database_path_is_accepted(tmp_path: Path):
    db_path = create_golden_sqlite(tmp_path / "golden.sqlite")
    database = SQLiteDatabase(SQLiteConnectionConfig(database_path=str(db_path)))

    assert database.path == db_path

    with database.open_readonly() as connection:
        assert connection.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 6


@pytest.mark.parametrize(
    "filename",
    [
        "golden with spaces.sqlite",
        "golden#hash.sqlite",
        "golden?query.sqlite",
    ],
)
def test_special_character_database_paths_are_uri_encoded(
    tmp_path: Path, filename: str
):
    db_path = tmp_path / filename
    create_golden_sqlite(db_path)
    database = SQLiteDatabase(SQLiteConnectionConfig(database_path=db_path))

    with database.open_readonly() as connection:
        assert connection.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 6


def test_open_readonly_closes_connection(tmp_path: Path):
    db_path = create_golden_sqlite(tmp_path / "golden.sqlite")
    database = SQLiteDatabase(SQLiteConnectionConfig(database_path=db_path))

    with database.open_readonly() as connection:
        assert connection.execute("SELECT 1").fetchone() == (1,)

    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")

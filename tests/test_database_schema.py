"""Tests for SQLite schema introspection without business-data execution."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest
import sqlite3

from langgraph_langchain.data.database import (
    DatabaseConnectionError,
    SQLiteDatabase,
    SQLiteConnectionConfig,
    SQLiteDataSource,
    SQLiteDatabaseSchema,
)
from langgraph_langchain.data.sources import SourceScan
from tests.fixtures.phase1.golden.database.golden_sqlite import (
    create_golden_sqlite,
)


def _database(path: Path) -> SQLiteDatabase:
    return SQLiteDatabase(SQLiteConnectionConfig(database_path=path))


def _source(path: Path) -> SQLiteDataSource:
    return SQLiteDataSource(_database(path))


def _create_database(path: Path, statements: list[str]) -> Path:
    connection = sqlite3.connect(path)
    try:
        for statement in statements:
            connection.execute(statement)
        connection.commit()
    finally:
        connection.close()
    return path


def test_golden_fixture_tables_are_discovered_in_stable_order(tmp_path: Path):
    db_path = create_golden_sqlite(tmp_path / "golden.sqlite")
    schema = _source(db_path).introspect_schema()

    assert isinstance(schema, SQLiteDatabaseSchema)
    assert [table.name for table in schema.tables] == [
        "customers",
        "order_items",
        "orders",
    ]


def test_scan_schema_satisfies_source_scan_contract(tmp_path: Path):
    db_path = create_golden_sqlite(tmp_path / "golden.sqlite")
    source = _source(db_path)

    scan = source.scan_schema()

    assert isinstance(scan, SourceScan)
    assert scan.source_type == "sqlite"
    assert scan.estimated_rows == 0
    assert scan.metadata["schema_introspection_only"] is True
    assert scan.metadata["database_schema"]["tables"]
    assert not scan.schema_snapshot.columns


def test_golden_fixture_columns_and_primary_keys_are_normalized(tmp_path: Path):
    db_path = create_golden_sqlite(tmp_path / "golden.sqlite")
    schema = _source(db_path).introspect_schema()
    customers = schema.tables[0]

    assert customers.primary_key_columns == ("customer_id",)
    assert [(column.name, column.declared_type) for column in customers.columns] == [
        ("customer_id", "INTEGER"),
        ("name", "TEXT"),
        ("region", "TEXT"),
        ("created_at", "TEXT"),
    ]
    assert [column.ordinal_position for column in customers.columns] == [0, 1, 2, 3]
    assert customers.columns[0].is_primary_key is True
    assert customers.columns[1].is_primary_key is False


def test_nullable_and_default_values_are_preserved(tmp_path: Path):
    db_path = _create_database(
        tmp_path / "defaults.sqlite",
        [
            "CREATE TABLE records (id INTEGER PRIMARY KEY, name TEXT NOT NULL DEFAULT 'unknown', value INTEGER DEFAULT 1, created_at TEXT DEFAULT CURRENT_TIMESTAMP)",
        ],
    )
    schema = _source(db_path).introspect_schema()
    columns = {column.name: column for column in schema.tables[0].columns}

    assert columns["id"].nullable is True
    assert columns["name"].nullable is False
    assert columns["name"].default_value == "'unknown'"
    assert columns["value"].nullable is True
    assert columns["value"].default_value == "1"
    assert columns["created_at"].default_value == "CURRENT_TIMESTAMP"

def test_views_internal_tables_and_empty_databases_are_handled(tmp_path: Path):
    db_path = _create_database(
        tmp_path / "objects.sqlite",
        [
            "CREATE TABLE user_table (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT)",
            "INSERT INTO user_table (name) VALUES ('fixture')",
            "CREATE VIEW user_view AS SELECT id FROM user_table",
        ],
    )
    schema = _source(db_path).introspect_schema()

    assert [table.name for table in schema.tables] == ["user_table"]

    empty_path = tmp_path / "empty.sqlite"
    empty_path.touch()
    assert _source(empty_path).introspect_schema().tables == ()


def test_composite_primary_key_preserves_declared_order(tmp_path: Path):
    db_path = _create_database(
        tmp_path / "primary-keys.sqlite",
        [
            "CREATE TABLE composite (b TEXT, a TEXT, value TEXT, PRIMARY KEY (b, a))",
        ],
    )
    schema = _source(db_path).introspect_schema()

    assert schema.tables[0].primary_key_columns == ("b", "a")
    assert [column.name for column in schema.tables[0].columns] == ["b", "a", "value"]


def test_foreign_key_actions_and_implicit_target_column(tmp_path: Path):
    db_path = _create_database(
        tmp_path / "foreign-keys.sqlite",
        [
            "CREATE TABLE parents (id INTEGER PRIMARY KEY)",
            "CREATE TABLE children (id INTEGER PRIMARY KEY, parent_id INTEGER, "
            "FOREIGN KEY (parent_id) REFERENCES parents "
            "ON UPDATE RESTRICT ON DELETE CASCADE)",
        ],
    )
    schema = _source(db_path).introspect_schema()
    children = next(table for table in schema.tables if table.name == "children")

    assert len(children.foreign_keys) == 1
    foreign_key = children.foreign_keys[0]
    assert foreign_key.source_column == "parent_id"
    assert foreign_key.target_table == "parents"
    assert foreign_key.target_column is None
    assert foreign_key.on_update == "RESTRICT"
    assert foreign_key.on_delete == "CASCADE"


def test_composite_foreign_keys_are_grouped_by_constraint(tmp_path: Path):
    db_path = _create_database(
        tmp_path / "composite-foreign-keys.sqlite",
        [
            "CREATE TABLE parents (a TEXT, b TEXT, PRIMARY KEY (a, b))",
            "CREATE TABLE children (pa TEXT, pb TEXT, "
            "FOREIGN KEY (pa, pb) REFERENCES parents (a, b))",
        ],
    )
    schema = _source(db_path).introspect_schema()
    children = next(table for table in schema.tables if table.name == "children")
    foreign_keys = children.foreign_keys

    assert len(foreign_keys) == 2
    assert [foreign_key.constraint_id for foreign_key in foreign_keys] == [0, 0]
    assert [foreign_key.sequence for foreign_key in foreign_keys] == [0, 1]
    assert [foreign_key.source_column for foreign_key in foreign_keys] == ["pa", "pb"]
    assert [foreign_key.target_column for foreign_key in foreign_keys] == ["a", "b"]


def test_unique_normal_partial_and_expression_indexes(tmp_path: Path):
    db_path = _create_database(
        tmp_path / "indexes.sqlite",
        [
            "CREATE TABLE items (a TEXT, b INTEGER)",
            "CREATE UNIQUE INDEX unique_idx ON items (a)",
            "CREATE INDEX normal_idx ON items (b)",
            "CREATE INDEX partial_idx ON items (a) WHERE b > 0",
            "CREATE INDEX expression_idx ON items (lower(a))",
        ],
    )
    schema = _source(db_path).introspect_schema()
    indexes = {index.name: index for index in schema.tables[0].indexes}

    assert indexes["unique_idx"].is_unique is True
    assert indexes["unique_idx"].columns == ("a",)
    assert indexes["normal_idx"].is_unique is False
    assert indexes["normal_idx"].columns == ("b",)
    assert indexes["partial_idx"].partial is True
    assert indexes["partial_idx"].columns == ("a",)
    assert indexes["expression_idx"].has_expressions is True
    assert indexes["expression_idx"].columns == ()


def test_special_character_identifiers_are_quoted_safely(tmp_path: Path):
    db_path = tmp_path / "special.sqlite"
    connection = sqlite3.connect(db_path)
    try:
        connection.execute('CREATE TABLE "orders #1" ("column #1" TEXT, "q""uote" TEXT)')
        connection.commit()
    finally:
        connection.close()

    schema = _source(db_path).introspect_schema()

    assert [table.name for table in schema.tables] == ["orders #1"]
    assert [column.name for column in schema.tables[0].columns] == [
        "column #1",
        'q"uote',
    ]


class _SpySQLiteDatabase(SQLiteDatabase):
    def __init__(self, config: SQLiteConnectionConfig):
        super().__init__(config)
        self.last_connection: sqlite3.Connection | None = None
        self.statements: list[str] = []

    @contextmanager
    def open_readonly(self):
        with super().open_readonly() as connection:
            self.last_connection = connection
            connection.set_trace_callback(self.statements.append)
            yield connection


def test_schema_scan_uses_readonly_connection_and_closes_it(tmp_path: Path):
    db_path = create_golden_sqlite(tmp_path / "golden.sqlite")
    database = _SpySQLiteDatabase(SQLiteConnectionConfig(database_path=db_path))
    source = SQLiteDataSource(database)

    source.scan_schema()

    assert database.last_connection is not None
    with pytest.raises(sqlite3.ProgrammingError):
        database.last_connection.execute("SELECT 1")

    assert database.statements
    assert all(
        statement.startswith("PRAGMA")
        or statement.startswith("SELECT name, rootpage, sql FROM sqlite_schema")
        for statement in database.statements
    )
    assert not any(
        statement.startswith("SELECT COUNT(*) FROM customers")
        for statement in database.statements
    )


def test_schema_scan_does_not_modify_database(tmp_path: Path):
    db_path = create_golden_sqlite(tmp_path / "golden.sqlite")
    before = db_path.read_bytes()
    source = _source(db_path)

    source.scan_schema()

    assert db_path.read_bytes() == before


def test_repeated_schema_scans_are_stable(tmp_path: Path):
    db_path = create_golden_sqlite(tmp_path / "golden.sqlite")
    source = _source(db_path)

    first = source.introspect_schema()
    second = source.introspect_schema()

    assert first == second
    assert first.to_dict() == second.to_dict()


def test_corrupt_database_does_not_return_empty_schema(tmp_path: Path):
    db_path = tmp_path / "corrupt.sqlite"
    db_path.write_text("not a sqlite database", encoding="utf-8")
    source = _source(db_path)

    with pytest.raises(DatabaseConnectionError):
        source.scan_schema()

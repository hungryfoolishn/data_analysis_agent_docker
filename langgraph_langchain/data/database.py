"""SQLite read-only connection and schema-introspection primitives.

This module intentionally keeps SQL execution out of Phase 1. Schema
introspection is read-only and does not scan business data rows.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from dataclasses import asdict
from pathlib import Path
from typing import Iterator

import sqlite3

import pandas as pd

from .assets import ColumnSpec
from .sources import DataSource, SchemaSnapshot, SourceScan


class DatabaseConfigError(ValueError):
    """Raised when a SQLite database path or configuration is invalid."""


class DatabaseConnectionError(RuntimeError):
    """Raised when a SQLite database cannot be opened."""


class DatabaseReadOnlyError(RuntimeError):
    """Raised when a write is attempted on a read-only SQLite connection."""



class SchemaIntrospectionError(RuntimeError):
    """Raised when SQLite schema metadata cannot be read or normalized."""


@dataclass(frozen=True)
class SQLiteConnectionConfig:
    """Validated configuration for a read-only SQLite database."""

    database_path: Path

    def __post_init__(self) -> None:
        path = Path(self.database_path).resolve()
        object.__setattr__(self, "database_path", path)
        if not path.exists():
            raise DatabaseConfigError(f"SQLite database does not exist: {path}")
        if not path.is_file():
            raise DatabaseConfigError(
                f"SQLite database path is not a file: {path}"
            )


class SQLiteDatabase:
    """Create isolated read-only connections to a SQLite database."""

    def __init__(self, config: SQLiteConnectionConfig) -> None:
        self.config = config

    @property
    def path(self) -> Path:
        return self.config.database_path

    def connect_readonly(self) -> sqlite3.Connection:
        """Open a new read-only connection.

        The connection is not cached. Callers must close it, preferably through
        :meth:`open_readonly`.
        """
        connection = None
        try:
            connection = sqlite3.connect(
                self._readonly_uri(),
                uri=True,
            )

            # mode=ro already opens the database without write access.  This
            # pragma is defense-in-depth against accidental write attempts on
            # the same connection.
            connection.execute("PRAGMA query_only=ON")
            # Opening a malformed file can succeed lazily.  Read a cheap
            # header-level value so connection errors surface here with a
            # stable exception type.  Avoid quick_check because it can be
            # expensive on large databases.
            connection.execute("PRAGMA schema_version").fetchone()
            return connection
        except sqlite3.Error as exc:
            if connection is not None:
                connection.close()
            raise DatabaseConnectionError(
                f"Cannot open read-only SQLite database: {self.path}"
            ) from exc

    @contextmanager
    def open_readonly(self) -> Iterator[sqlite3.Connection]:
        """Yield a read-only connection and always close it."""
        connection = self.connect_readonly()
        try:
            yield connection
        except sqlite3.OperationalError as exc:
            if self._is_readonly_error(exc):
                raise DatabaseReadOnlyError(str(exc)) from exc
            raise
        finally:
            connection.close()

    @staticmethod
    def _is_readonly_error(exc: sqlite3.Error) -> bool:
        message = str(exc).casefold()
        return (
            "readonly database" in message
            or "read-only database" in message
            or "attempt to write a readonly database" in message
        )

    def _readonly_uri(self) -> str:
        return f"{self.path.resolve().as_uri()}?mode=ro"


@dataclass(frozen=True)
class SQLiteColumnSchema:
    """A single column as declared by SQLite metadata."""

    name: str
    declared_type: str
    nullable: bool
    default_value: object | None
    is_primary_key: bool
    ordinal_position: int


@dataclass(frozen=True)
class SQLiteForeignKeySchema:
    """One column within a SQLite foreign-key constraint."""

    constraint_id: int
    sequence: int
    source_column: str
    target_table: str
    target_column: str | None
    on_update: str
    on_delete: str
    match: str


@dataclass(frozen=True)
class SQLiteIndexSchema:
    """A SQLite index; expression indexes never expose fake column names."""

    name: str
    columns: tuple[str, ...]
    is_unique: bool
    origin: str
    partial: bool
    has_expressions: bool = False


@dataclass(frozen=True)
class SQLiteTableSchema:
    """Schema metadata for one ordinary user table."""

    name: str
    columns: tuple[SQLiteColumnSchema, ...]
    primary_key_columns: tuple[str, ...]
    foreign_keys: tuple[SQLiteForeignKeySchema, ...]
    indexes: tuple[SQLiteIndexSchema, ...]


@dataclass(frozen=True)
class SQLiteDatabaseSchema:
    """Immutable, serializable schema metadata for SQLite user tables."""

    tables: tuple[SQLiteTableSchema, ...]

    def to_dict(self) -> dict[str, object]:
        return {"tables": [asdict(table) for table in self.tables]}


def _quote_sqlite_identifier(identifier: str) -> str:
    """Quote an identifier already obtained from SQLite metadata."""
    if not isinstance(identifier, str):
        raise SchemaIntrospectionError("SQLite identifier metadata must be text")
    return '"' + identifier.replace('"', '""') + '"'


def _metadata_rows(connection: sqlite3.Connection, statement: str) -> list[dict[str, object]]:
    cursor = connection.execute(statement)
    names = [description[0] for description in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def _table_types(connection: sqlite3.Connection) -> dict[str, str]:
    """Return SQLite table types from PRAGMA table_list when available."""
    if sqlite3.sqlite_version_info < (3, 37, 0):
        return {}
    try:
        rows = _metadata_rows(connection, "PRAGMA table_list")
    except sqlite3.OperationalError:
        return {}
    return {
        str(row["name"]): str(row["type"])
        for row in rows
        if row.get("schema") == "main"
    }


def _has_primary_key_autoindex(connection: sqlite3.Connection, quoted_table: str) -> bool:
    rows = _metadata_rows(connection, f"PRAGMA index_list({quoted_table})")
    return any(row.get("origin") == "pk" for row in rows)

def _column_schemas(connection: sqlite3.Connection, table_name: str) -> tuple[tuple[SQLiteColumnSchema, ...], tuple[str, ...]]:
    quoted_table = _quote_sqlite_identifier(table_name)
    rows = _metadata_rows(connection, f"PRAGMA table_info({quoted_table})")
    rows.sort(key=lambda row: int(row["cid"]))
    primary_key_rows = [row for row in rows if row["pk"]]
    primary_key_rows.sort(key=lambda row: int(row["pk"]))
    primary_key_columns = tuple(str(row["name"]) for row in primary_key_rows)
    # INTEGER PRIMARY KEY without a PK autoindex is a rowid alias. SQLite
    # reports notnull=0 for compatibility, but it cannot store NULL.
    is_rowid_alias = (
        len(primary_key_rows) == 1
        and int(primary_key_rows[0]["pk"]) == 1
        and str(primary_key_rows[0]["type"] or "").strip().upper() == "INTEGER"
        and not _has_primary_key_autoindex(connection, quoted_table)
    )
    columns = tuple(
        SQLiteColumnSchema(
            name=str(row["name"]),
            declared_type=str(row["type"] or ""),
            nullable=(
                False
                if is_rowid_alias and int(row["pk"]) == 1
                else not bool(row["notnull"])
            ),
            default_value=row["dflt_value"],
            is_primary_key=bool(row["pk"]),
            ordinal_position=int(row["cid"]),
        )
        for row in rows
    )
    return columns, primary_key_columns


def _foreign_key_schemas(connection: sqlite3.Connection, table_name: str) -> tuple[SQLiteForeignKeySchema, ...]:
    quoted_table = _quote_sqlite_identifier(table_name)
    rows = _metadata_rows(connection, f"PRAGMA foreign_key_list({quoted_table})")
    rows.sort(key=lambda row: (int(row["id"]), int(row["seq"])))
    grouped: dict[int, list[dict[str, object]]] = {}
    for row in rows:
        grouped.setdefault(int(row["id"]), []).append(row)
    foreign_keys: list[SQLiteForeignKeySchema] = []
    for constraint_id in sorted(grouped):
        for row in grouped[constraint_id]:
            foreign_keys.append(
                SQLiteForeignKeySchema(
                    constraint_id=constraint_id,
                    sequence=int(row["seq"]),
                    source_column=str(row["from"]),
                    target_table=str(row["table"]),
                    target_column=row["to"] if row["to"] is None else str(row["to"]),
                    on_update=str(row["on_update"]),
                    on_delete=str(row["on_delete"]),
                    match=str(row["match"]),
                )
            )
    return tuple(foreign_keys)


def _index_schemas(connection: sqlite3.Connection, table_name: str) -> tuple[SQLiteIndexSchema, ...]:
    quoted_table = _quote_sqlite_identifier(table_name)
    rows = _metadata_rows(connection, f"PRAGMA index_list({quoted_table})")
    rows.sort(key=lambda row: int(row["seq"]))
    indexes: list[SQLiteIndexSchema] = []
    for row in rows:
        index_name = str(row["name"])
        quoted_index = _quote_sqlite_identifier(index_name)
        index_info = _metadata_rows(connection, f"PRAGMA index_info({quoted_index})")
        index_info.sort(key=lambda item: int(item["seqno"]))
        columns = tuple(
            str(item["name"]) for item in index_info if item["name"] is not None
        )
        indexes.append(
            SQLiteIndexSchema(
                name=index_name,
                columns=columns,
                is_unique=bool(row["unique"]),
                origin=str(row["origin"]),
                partial=bool(row["partial"]),
                has_expressions=any(item["name"] is None for item in index_info),
            )
        )
    indexes.sort(key=lambda index: (index.name.casefold(), index.name))
    return tuple(indexes)


def _schema_snapshot_columns(schema: SQLiteDatabaseSchema) -> list[ColumnSpec]:
    """Project SQLite schema into the generic SourceScan column model."""
    return [
        ColumnSpec(
            name=f"{table.name}.{column.name}",
            dtype=column.declared_type,
            nullable=column.nullable,
            null_count=None,
        )
        for table in schema.tables
        for column in table.columns
    ]


def _introspect_sqlite_schema(connection: sqlite3.Connection) -> SQLiteDatabaseSchema:
    rows = _metadata_rows(
        connection,
        "SELECT name, rootpage, sql FROM sqlite_schema WHERE type = 'table'",
    )
    table_types = _table_types(connection)
    tables: list[SQLiteTableSchema] = []
    for row in rows:
        name = row["name"]
        if not isinstance(name, str) or name.startswith("sqlite_"):
            continue
        table_type = table_types.get(name)
        if table_type not in (None, "table"):
            continue
        # Older SQLite builds without PRAGMA table_list use sqlite_schema as a
        # best-effort fallback for virtual tables; shadow tables require a
        # SQLite version that can identify them explicitly.
        if table_type is None:
            if row["rootpage"] in (None, 0):
                continue
            sql = row["sql"]
            if isinstance(sql, str) and "CREATE VIRTUAL TABLE" in sql.upper():
                continue
        columns, primary_key_columns = _column_schemas(connection, name)
        tables.append(
            SQLiteTableSchema(
                name=name,
                columns=columns,
                primary_key_columns=primary_key_columns,
                foreign_keys=_foreign_key_schemas(connection, name),
                indexes=_index_schemas(connection, name),
            )
        )
    tables.sort(key=lambda table: (table.name.casefold(), table.name))
    return SQLiteDatabaseSchema(tables=tuple(tables))


class SQLiteDataSource(DataSource):
    """SQLite adapter exposing readonly connections and schema metadata.

    `preview`, `estimate_rows`, and `materialize` remain unimplemented until
    M2.5 defines bounded query execution.
    """

    source_type = "sqlite"
    adapter_version = "sqlite-readonly-v1"

    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database

    @property
    def path(self) -> Path:
        return self.database.path

    def connect_readonly(self) -> sqlite3.Connection:
        return self.database.connect_readonly()

    @contextmanager
    def open_readonly(self) -> Iterator[sqlite3.Connection]:
        with self.database.open_readonly() as connection:
            yield connection

    def introspect_schema(self) -> SQLiteDatabaseSchema:
        """Read and normalize SQLite schema metadata using a readonly connection."""
        try:
            with self.open_readonly() as connection:
                return _introspect_sqlite_schema(connection)
        except sqlite3.Error as exc:
            raise SchemaIntrospectionError(
                "Cannot read SQLite schema metadata"
            ) from exc

    def scan_schema(self) -> SourceScan:
        schema = self.introspect_schema()
        generic_columns = _schema_snapshot_columns(schema)
        # -1 is an explicit unknown sentinel: schema introspection must not
        # scan business rows merely to estimate row counts.
        return SourceScan(
            source_type=self.source_type,
            schema_snapshot=SchemaSnapshot(
                row_count=-1,
                column_count=len(generic_columns),
                columns=generic_columns,
            ),
            estimated_rows=-1,
            size_bytes=self.path.stat().st_size,
            metadata={
                "database_schema": schema.to_dict(),
                "schema_introspection_only": True,
                "row_count_known": False,
            },
        )

    def preview(self, rows: int = 5) -> pd.DataFrame:
        raise NotImplementedError(
            "SQLite preview execution is delivered in M2.5"
        )

    def estimate_rows(self) -> int:
        raise NotImplementedError(
            "SQLite row estimation is delivered in M2.5"
        )

    def materialize(self, *, max_rows: int | None = None, columns: list[str] | None = None) -> pd.DataFrame:
        raise NotImplementedError(
            "SQLite materialization is delivered in M2.5"
        )

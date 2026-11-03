"""SQLite read-only connection, bounded reads, and schema introspection."""

from __future__ import annotations

import hashlib
import re
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

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


class DatabaseQueryError(RuntimeError):
    """Base error for a failed bounded SQLite read."""


class DatabaseQueryValidationError(DatabaseQueryError, ValueError):
    """Raised when SQL, identifiers, or query parameters fail validation."""


class QueryTimeoutError(DatabaseQueryError):
    """Raised when SQLite execution exceeds the configured deadline."""


class ResultSizeLimitExceededError(DatabaseQueryError):
    """Raised when a query would materialize more bytes than permitted."""


@dataclass(frozen=True)
class SQLiteConnectionConfig:
    """Validated configuration for a read-only SQLite database."""

    database_path: Path
    preview_max_rows: int = 100
    materialize_max_rows: int = 100_000
    query_timeout_seconds: float = 30.0
    # This is an estimated cap for materialized SQLite values. SQLite must
    # return a row before Python can inspect its bytes, so it is not a hard
    # guarantee for SQLite's internal or Python's peak memory usage.
    max_result_bytes: int = 50 * 1024 * 1024
    fetch_batch_rows: int = 1_000

    def __post_init__(self) -> None:
        path = Path(self.database_path).resolve()
        object.__setattr__(self, "database_path", path)
        if not path.exists():
            raise DatabaseConfigError(f"SQLite database does not exist: {path}")
        if not path.is_file():
            raise DatabaseConfigError(
                f"SQLite database path is not a file: {path}"
            )
        _validate_positive_int(self.preview_max_rows, "preview_max_rows")
        _validate_positive_int(
            self.materialize_max_rows, "materialize_max_rows"
        )
        _validate_positive_int(self.fetch_batch_rows, "fetch_batch_rows")
        if not isinstance(self.query_timeout_seconds, (int, float)) or isinstance(
            self.query_timeout_seconds, bool
        ):
            raise DatabaseConfigError("query_timeout_seconds must be a number")
        if self.query_timeout_seconds <= 0:
            raise DatabaseConfigError("query_timeout_seconds must be positive")
        _validate_positive_int(self.max_result_bytes, "max_result_bytes")


def _validate_positive_int(value: Any, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise DatabaseConfigError(f"{name} must be a positive integer")


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

    def table(self, name: str) -> SQLiteTableSchema | None:
        return next((table for table in self.tables if table.name == name), None)


@dataclass(frozen=True)
class RowEstimate:
    """A row-count estimate with explicit precision semantics."""

    table_name: str
    count: int | None
    is_exact: bool
    source: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class SQLiteReadMetadata:
    """Provenance and resource metadata for one bounded read."""

    database_path: str
    table_name: str | None
    query_sha256: str | None
    selected_columns: tuple[str, ...]
    returned_row_count: int
    requested_row_limit: int
    truncated: bool
    execution_duration_seconds: float
    source_type: str = "sqlite"
    adapter_version: str = "sqlite-readonly-v1"
    estimate: RowEstimate | None = None

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["estimate"] = (
            self.estimate.to_dict() if self.estimate is not None else None
        )
        return payload


@dataclass(frozen=True)
class BoundedReadResult:
    """A materialized dataframe paired with its read provenance."""

    dataframe: pd.DataFrame
    metadata: SQLiteReadMetadata

    @property
    def truncated(self) -> bool:
        return self.metadata.truncated

    @property
    def row_count(self) -> int:
        return len(self.dataframe)


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


def _sqlite_value_size(value: object) -> int:
    """Return a conservative bounded-read byte estimate for one SQLite value."""
    if value is None:
        return 1
    if isinstance(value, (bytes, bytearray)):
        return len(value)
    if isinstance(value, str):
        return len(value.encode("utf-8"))
    return 8


def _has_multiple_statements(sql: str) -> bool:
    """Detect a second top-level SQL statement without parsing SQLite SQL."""
    quote: str | None = None
    index = 0
    length = len(sql)
    while index < length:
        char = sql[index]
        if quote is not None:
            if char == quote:
                if index + 1 < length and sql[index + 1] == quote:
                    index += 2
                    continue
                quote = None
            index += 1
            continue
        if char in {"'", '"', "`"}:
            quote = char
        elif char == "[":
            quote = "]"
        elif sql.startswith("--", index):
            end = sql.find("\n", index + 2)
            if end < 0:
                break
            index = end + 1
            continue
        elif sql.startswith("/*", index):
            end = sql.find("*/", index + 2)
            if end < 0:
                raise DatabaseQueryValidationError("Unterminated SQL comment")
            index = end + 2
            continue
        elif char == ";":
            remainder = sql[index + 1 :]
            if remainder.strip():
                return True
        index += 1
    return False


# SQLite must materialize values before Python can measure them. Deny the
# built-in/extension functions most likely to create a very large value before
# execution; ordinary column values are still guarded per fetched row.
_UNBOUNDED_VALUE_FUNCTIONS = frozenset(
    {"randomblob", "zeroblob", "readfile", "writefile"}
)


def _readonly_select_authorizer(
    action: int, arg1: Any, arg2: Any, database: Any, trigger: Any
) -> int:
    """Permit SELECT/READ operations and deny every other SQLite action."""
    allowed = {
        sqlite3.SQLITE_SELECT,
        sqlite3.SQLITE_READ,
        sqlite3.SQLITE_FUNCTION,
        sqlite3.SQLITE_RECURSIVE,
    }
    if action in allowed:
        if action == sqlite3.SQLITE_FUNCTION and isinstance(arg2, str):
            function_name = arg2.casefold()
            if (
                function_name == "load_extension"
                or function_name in _UNBOUNDED_VALUE_FUNCTIONS
            ):
                return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def _is_timeout_error(exc: sqlite3.Error) -> bool:
    return "interrupted" in str(exc).casefold()


def _query_fingerprint(sql: str) -> str:
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()


def _validate_max_rows(value: int | None, hard_limit: int, label: str) -> int:
    if value is None:
        return hard_limit
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DatabaseQueryValidationError(f"{label} must be a non-negative integer")
    if value > hard_limit:
        raise DatabaseQueryValidationError(
            f"{label} exceeds system limit {hard_limit}"
        )
    return value


def _validate_columns(
    columns: list[str] | None, allowed_columns: list[str]
) -> list[str] | None:
    if columns is None:
        return None
    if not isinstance(columns, Sequence) or isinstance(columns, str):
        raise DatabaseQueryValidationError("columns must be a sequence of names")
    normalized: list[str] = []
    for column in columns:
        if not isinstance(column, str) or column not in allowed_columns:
            raise DatabaseQueryValidationError(f"Unknown SQLite column: {column}")
        normalized.append(column)
    if len(set(normalized)) != len(normalized):
        raise DatabaseQueryValidationError("Duplicate projected columns are not allowed")
    return normalized

def _validate_order_by(
    order_by: Sequence[str] | str | None, allowed_columns: list[str]
) -> tuple[str, ...]:
    if order_by is None:
        return ()
    if isinstance(order_by, str):
        clauses = [order_by]
    elif isinstance(order_by, Sequence):
        clauses = list(order_by)
    else:
        raise DatabaseQueryValidationError("order_by must be a name or sequence")
    normalized: list[str] = []
    for clause in clauses:
        if not isinstance(clause, str) or not clause.strip():
            raise DatabaseQueryValidationError("order_by clauses must be text")
        match = re.fullmatch(r"\s*(.+?)\s*(?:\s+(?i:asc|desc))?\s*", clause.strip())
        if match is None:
            raise DatabaseQueryValidationError(f"Invalid order_by clause: {clause}")
        column = match.group(1)
        if column not in allowed_columns:
            raise DatabaseQueryValidationError(f"Unknown order_by column: {column}")
        quoted = _quote_sqlite_identifier(column)
        direction = re.search(r"(?i)\s+(asc|desc)\s*$", clause.strip())
        normalized.append(
            f"{quoted} {direction.group(1).upper()}" if direction else quoted
        )
    return tuple(normalized)


class SQLiteDataSource(DataSource):
    """Read-only SQLite adapter with bounded previews and materialization."""

    source_type = "sqlite"
    adapter_version = "sqlite-readonly-v1"

    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database

    @property
    def path(self) -> Path:
        return self.database.path

    @property
    def config(self) -> SQLiteConnectionConfig:
        return self.database.config

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

    def preview(
        self,
        rows: int = 5,
        *,
        table_name: str | None = None,
        columns: list[str] | None = None,
        order_by: Sequence[str] | str | None = None,
    ) -> pd.DataFrame:
        """Return a bounded dataframe preview without truncation metadata."""
        return self.preview_with_metadata(
            rows=rows,
            table_name=table_name,
            columns=columns,
            order_by=order_by,
        ).dataframe

    def preview_with_metadata(
        self,
        rows: int = 5,
        *,
        table_name: str | None = None,
        columns: list[str] | None = None,
        order_by: Sequence[str] | str | None = None,
    ) -> BoundedReadResult:
        _validate_max_rows(rows, self.config.preview_max_rows, "preview rows")
        return self._materialize_table(
            table_name=table_name,
            max_rows=rows,
            columns=columns,
            order_by=order_by,
        )

    def estimate_rows(
        self,
        table_name: str | None = None,
        *,
        exact: bool = False,
    ) -> RowEstimate:
        """Estimate rows using ANALYZE statistics, or count exactly on request."""
        schema = self.introspect_schema()
        table = self._resolve_table(schema, table_name)
        if exact:
            quoted_table = _quote_sqlite_identifier(table.name)
            result = self.execute_bounded_query(
                f"SELECT COUNT(*) AS row_count FROM {quoted_table}",
                max_rows=1,
            )
            if len(result.dataframe) != 1:
                raise DatabaseQueryError("Exact row-count query returned no row")
            return RowEstimate(
                table_name=table.name,
                count=int(result.dataframe.iloc[0]["row_count"]),
                is_exact=True,
                source="exact_count",
            )

        try:
            result = self.execute_bounded_query(
                "SELECT stat FROM sqlite_stat1 WHERE tbl = ?",
                parameters=(table.name,),
                max_rows=1,
            )
        except (DatabaseQueryError, sqlite3.Error):
            result = None
        if result is not None and len(result.dataframe) == 1:
            raw_stat = result.dataframe.iloc[0]["stat"]
            if isinstance(raw_stat, str) and raw_stat.strip():
                try:
                    estimated = int(raw_stat.strip().split()[0])
                except (ValueError, IndexError):
                    estimated = None
                if estimated is not None:
                    return RowEstimate(
                        table_name=table.name,
                        count=estimated,
                        is_exact=False,
                        source="sqlite_stat1",
                    )
        return RowEstimate(
            table_name=table.name,
            count=None,
            is_exact=False,
            source="unknown",
        )

    def materialize(
        self,
        *,
        max_rows: int | None = None,
        columns: list[str] | None = None,
        table_name: str | None = None,
        order_by: Sequence[str] | str | None = None,
        query: str | None = None,
        parameters: tuple[Any, ...] | Mapping[str, Any] | None = None,
        result_size_limit: int | None = None,
        timeout_seconds: float | None = None,
    ) -> pd.DataFrame:
        """Materialize a table or validated read-only query into a DataFrame."""
        if query is not None:
            if table_name is not None or columns is not None or order_by is not None:
                raise DatabaseQueryValidationError(
                    "table_name, columns, and order_by cannot be combined with query"
                )
            return self.execute_bounded_query(
                query,
                parameters=parameters,
                max_rows=max_rows,
                result_size_limit=result_size_limit,
                timeout_seconds=timeout_seconds,
            ).dataframe
        if parameters is not None:
            raise DatabaseQueryValidationError(
                "parameters are only valid with a SQL query"
            )
        return self._materialize_table(
            table_name=table_name,
            max_rows=max_rows,
            columns=columns,
            order_by=order_by,
            result_size_limit=result_size_limit,
            timeout_seconds=timeout_seconds,
        ).dataframe

    def materialize_with_metadata(
        self,
        *,
        table_name: str | None = None,
        max_rows: int | None = None,
        columns: list[str] | None = None,
        order_by: Sequence[str] | str | None = None,
        result_size_limit: int | None = None,
        timeout_seconds: float | None = None,
    ) -> BoundedReadResult:
        return self._materialize_table(
            table_name=table_name,
            max_rows=max_rows,
            columns=columns,
            order_by=order_by,
            result_size_limit=result_size_limit,
            timeout_seconds=timeout_seconds,
        )

    def execute_bounded_query(
        self,
        query: str,
        *,
        parameters: tuple[Any, ...] | Mapping[str, Any] | None = None,
        max_rows: int | None = None,
        result_size_limit: int | None = None,
        timeout_seconds: float | None = None,
    ) -> BoundedReadResult:
        """Execute one read-only SELECT with bounded rows, time, and size.

        ``result_size_limit`` is an estimated cap applied as fetched values are
        accumulated. SQLite must return a row before Python can inspect its
        bytes, so this is not a hard SQLite/Python peak-memory boundary.
        Functions capable of generating unbounded values are denied before
        execution.
        """
        if not isinstance(query, str) or not query.strip():
            raise DatabaseQueryValidationError("SQL query must be non-empty text")
        if _has_multiple_statements(query):
            raise DatabaseQueryValidationError("Only one SQL statement is allowed")
        requested_limit = _validate_max_rows(
            max_rows, self.config.materialize_max_rows, "max_rows"
        )
        if result_size_limit is None:
            result_size_limit = self.config.max_result_bytes
        _validate_positive_int(result_size_limit, "result_size_limit")
        if timeout_seconds is None:
            timeout_seconds = self.config.query_timeout_seconds
        if (
            not isinstance(timeout_seconds, (int, float))
            or isinstance(timeout_seconds, bool)
            or timeout_seconds <= 0
        ):
            raise DatabaseQueryValidationError(
                "timeout_seconds must be a positive number"
            )

        started = time.monotonic()
        connection = self.connect_readonly()
        cursor = None
        try:
            connection.set_authorizer(_readonly_select_authorizer)
            deadline = started + float(timeout_seconds)

            def check_progress() -> int:
                return 1 if time.monotonic() >= deadline else 0

            connection.set_progress_handler(check_progress, 1_000)
            cursor = connection.execute(query, parameters or ())
            column_names = tuple(
                str(description[0]) for description in cursor.description
            )
            materialized_rows: list[tuple[Any, ...]] = []
            returned = 0
            result_bytes = 0
            truncated = False
            while True:
                remaining = requested_limit - returned
                fetch_count = min(self.config.fetch_batch_rows, remaining + 1)
                fetched_rows = cursor.fetchmany(fetch_count)
                if not fetched_rows:
                    break
                if len(fetched_rows) > remaining:
                    fetched_rows = fetched_rows[:remaining]
                    truncated = True
                for row in fetched_rows:
                    row_bytes = sum(_sqlite_value_size(value) for value in row)
                    if result_bytes + row_bytes > result_size_limit:
                        raise ResultSizeLimitExceededError(
                            f"SQLite result exceeds {result_size_limit} bytes"
                        )
                    result_bytes += row_bytes
                if fetched_rows:
                    materialized_rows.extend(fetched_rows)
                    returned += len(fetched_rows)
                if truncated or len(fetched_rows) < fetch_count:
                    break

            dataframe = pd.DataFrame(
                materialized_rows, columns=list(column_names)
            )
            duration = time.monotonic() - started
            metadata = SQLiteReadMetadata(
                database_path=str(self.path),
                table_name=None,
                query_sha256=_query_fingerprint(query),
                selected_columns=column_names,
                returned_row_count=len(dataframe),
                requested_row_limit=requested_limit,
                truncated=truncated,
                execution_duration_seconds=duration,
            )
            return BoundedReadResult(dataframe=dataframe, metadata=metadata)
        except sqlite3.OperationalError as exc:
            if _is_timeout_error(exc):
                raise QueryTimeoutError(
                    f"SQLite query exceeded {timeout_seconds} seconds"
                ) from exc
            message = str(exc).casefold()
            if "not authorized to use function" in message:
                raise DatabaseQueryValidationError(
                    "Blocked SQLite function: unbounded value functions are disabled"
                ) from exc
            if "not authorized" in message:
                raise DatabaseQueryValidationError(
                    "Only single read-only SELECT statements are allowed"
                ) from exc
            if "no such" in str(exc).casefold():
                raise DatabaseQueryValidationError(
                    f"Invalid SQLite query: {exc}"
                ) from exc
            raise DatabaseQueryError(f"SQLite query failed: {exc}") from exc
        except sqlite3.DatabaseError as exc:
            if self.database._is_readonly_error(exc):
                raise DatabaseReadOnlyError(str(exc)) from exc
            raise DatabaseQueryValidationError(f"Invalid SQLite query: {exc}") from exc
        finally:
            if cursor is not None:
                cursor.close()
            connection.set_progress_handler(None, 0)
            try:
                connection.set_authorizer(None)
            except sqlite3.Error:
                pass
            connection.close()

    def _materialize_table(
        self,
        *,
        table_name: str | None,
        max_rows: int | None,
        columns: list[str] | None,
        order_by: Sequence[str] | str | None,
        result_size_limit: int | None = None,
        timeout_seconds: float | None = None,
    ) -> BoundedReadResult:
        schema = self.introspect_schema()
        table = self._resolve_table(schema, table_name)
        column_names = [column.name for column in table.columns]
        projected_columns = _validate_columns(columns, column_names)
        order_clauses = _validate_order_by(order_by, column_names)
        requested_limit = _validate_max_rows(
            max_rows, self.config.materialize_max_rows, "max_rows"
        )
        selected = (
            ", ".join(_quote_sqlite_identifier(name) for name in projected_columns)
            if projected_columns is not None
            else "*"
        )
        sql = f"SELECT {selected} FROM {_quote_sqlite_identifier(table.name)}"
        if order_clauses:
            sql += " ORDER BY " + ", ".join(order_clauses)
        result = self.execute_bounded_query(
            sql,
            max_rows=requested_limit,
            result_size_limit=result_size_limit,
            timeout_seconds=timeout_seconds,
        )
        metadata = SQLiteReadMetadata(
            database_path=str(self.path),
            table_name=table.name,
            query_sha256=None,
            selected_columns=tuple(
                projected_columns if projected_columns is not None else column_names
            ),
            returned_row_count=result.metadata.returned_row_count,
            requested_row_limit=result.metadata.requested_row_limit,
            truncated=result.metadata.truncated,
            execution_duration_seconds=result.metadata.execution_duration_seconds,
        )
        return BoundedReadResult(dataframe=result.dataframe, metadata=metadata)

    def _resolve_table(
        self, schema: SQLiteDatabaseSchema, table_name: str | None
    ) -> SQLiteTableSchema:
        if table_name is None:
            if len(schema.tables) == 1:
                return schema.tables[0]
            raise DatabaseQueryValidationError(
                "table_name is required when the database has multiple tables"
            )
        if not isinstance(table_name, str):
            raise DatabaseQueryValidationError("table_name must be text")
        table = schema.table(table_name)
        if table is None:
            raise DatabaseQueryValidationError(
                f"Unknown SQLite table: {table_name}"
            )
        return table
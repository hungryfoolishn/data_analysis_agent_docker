"""Deterministic SQLite golden fixture builder for Phase 1.

This module is intentionally test-only. It creates a small, stable, and
human-auditable database for later Phase 1 database adapter tests.
"""

from __future__ import annotations

from pathlib import Path
import shutil
import sqlite3
import tempfile


SCHEMA_SQL = """
CREATE TABLE customers (
    customer_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    region TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE orders (
    order_id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(customer_id),
    amount REAL NOT NULL,
    status TEXT,
    order_date TEXT NOT NULL
);

CREATE TABLE order_items (
    item_id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES orders(order_id),
    product TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    unit_price REAL
);
"""

CUSTOMERS = [
    (1, "Acme Corp", "North", "2023-01-01"),
    (2, "Globex", "South", "2023-01-05"),
    (3, "Initech", "East", "2023-01-10"),
    (4, "Umbrella", None, "2023-02-01"),
]

ORDERS = [
    (1, 1, 120.50, "completed", "2024-01-02"),
    (2, 1, 80.00, "completed", "2024-01-15"),
    (3, 2, 45.00, "pending", "2024-02-01"),
    (4, 3, 210.00, "completed", "2024-02-10"),
    (5, 3, 0.00, "cancelled", "2024-03-01"),
    (6, 4, 99.99, "pending", "2024-03-15"),
]

ORDER_ITEMS = [
    (1, 1, "Widget", 2, 25.00),
    (2, 1, "Gadget", 1, 70.50),
    (3, 2, "Widget", 1, 80.00),
    (4, 3, "Sprocket", 3, 15.00),
    (5, 4, "Widget", 2, 100.00),
    (6, 4, "Gizmo", 1, 10.00),
    (7, 5, "Widget", 0, 0.00),
    (8, 6, "Gadget", 1, 99.99),
]


def create_golden_sqlite(
    db_path: str | Path | None = None,
    *,
    overwrite: bool = False,
) -> Path:
    """Create a deterministic SQLite golden fixture and return its path.

    Args:
        db_path: Optional target path. When omitted, the database is created in
            a fresh temporary directory. The caller owns the returned path.
        overwrite: Allow replacing an existing caller-provided database path.
            Automatic temporary fixtures always start from a fresh path.

    Returns:
        Path to the created SQLite database.

    Raises:
        FileExistsError: If a caller-provided path exists and ``overwrite`` is
            false.
        RuntimeError: If schema or fixture data creation fails.
    """
    created_temp_dir = db_path is None
    if db_path is None:
        temp_dir = Path(tempfile.mkdtemp(prefix="deepanalyze_golden_sqlite_"))
        target_path = temp_dir / "golden.sqlite"
    else:
        target_path = Path(db_path)

    try:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        if target_path.exists() and not (overwrite and not created_temp_dir):
            raise FileExistsError(
                f"Refusing to overwrite existing SQLite fixture path: {target_path}"
            )
        target_path.unlink(missing_ok=True)

        connection = sqlite3.connect(target_path)
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.executescript(SCHEMA_SQL)
            connection.executemany(
                "INSERT INTO customers VALUES (?, ?, ?, ?)",
                CUSTOMERS,
            )
            connection.executemany(
                "INSERT INTO orders VALUES (?, ?, ?, ?, ?)",
                ORDERS,
            )
            connection.executemany(
                "INSERT INTO order_items VALUES (?, ?, ?, ?, ?)",
                ORDER_ITEMS,
            )
            connection.commit()
        except Exception as exc:
            connection.rollback()
            raise RuntimeError("Failed to create the SQLite golden fixture") from exc
        finally:
            connection.close()
    except Exception:
        if created_temp_dir:
            shutil.rmtree(target_path.parent, ignore_errors=True)
        raise

    return target_path


def cleanup_golden_sqlite(db_path: str | Path) -> None:
    """Remove only a fixture-generated temporary database.

    A caller-provided path is deliberately not accepted as cleanup input. This
    prevents the helper from deleting arbitrary database files.
    """
    path = Path(db_path)
    is_generated_fixture = (
        path.name == "golden.sqlite"
        and path.parent.name.startswith("deepanalyze_golden_sqlite_")
    )
    if not is_generated_fixture:
        return

    path.unlink(missing_ok=True)
    try:
        path.parent.rmdir()
    except OSError:
        # The directory is not empty or is no longer owned by the fixture.
        pass

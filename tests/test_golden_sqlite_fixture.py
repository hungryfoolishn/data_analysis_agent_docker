"""Tests for the deterministic Phase 1 SQLite golden fixture builder."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tests.fixtures.phase1.golden.database.golden_sqlite import (
    cleanup_golden_sqlite,
    create_golden_sqlite,
)


def _connect(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(path)


def test_create_golden_sqlite_success(tmp_path: Path):
    db_path = tmp_path / "nested" / "golden.sqlite"

    created_path = create_golden_sqlite(db_path)

    assert created_path == db_path
    assert db_path.is_file()
    assert db_path.stat().st_size > 0

    with _connect(db_path) as connection:
        table_names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert table_names == {"customers", "orders", "order_items"}


def test_golden_sqlite_schema_matches_contract(tmp_path: Path):
    db_path = create_golden_sqlite(tmp_path / "golden.sqlite")

    with _connect(db_path) as connection:
        customer_columns = {
            row[1]: row[2]
            for row in connection.execute("PRAGMA table_info(customers)")
        }
        order_columns = {
            row[1]: row[2]
            for row in connection.execute("PRAGMA table_info(orders)")
        }
        item_columns = {
            row[1]: row[2]
            for row in connection.execute("PRAGMA table_info(order_items)")
        }
        order_foreign_keys = {
            (row[3], row[2], row[4])
            for row in connection.execute("PRAGMA foreign_key_list(orders)")
        }
        item_foreign_keys = {
            (row[3], row[2], row[4])
            for row in connection.execute("PRAGMA foreign_key_list(order_items)")
        }

    assert customer_columns == {
        "customer_id": "INTEGER",
        "name": "TEXT",
        "region": "TEXT",
        "created_at": "TEXT",
    }
    assert order_columns == {
        "order_id": "INTEGER",
        "customer_id": "INTEGER",
        "amount": "REAL",
        "status": "TEXT",
        "order_date": "TEXT",
    }
    assert item_columns == {
        "item_id": "INTEGER",
        "order_id": "INTEGER",
        "product": "TEXT",
        "quantity": "INTEGER",
        "unit_price": "REAL",
    }
    assert order_foreign_keys == {("customer_id", "customers", "customer_id")}
    assert item_foreign_keys == {("order_id", "orders", "order_id")}


def test_golden_sqlite_contains_expected_row_counts_and_values(tmp_path: Path):
    db_path = create_golden_sqlite(tmp_path / "golden.sqlite")

    with _connect(db_path) as connection:
        customer_count = connection.execute(
            "SELECT COUNT(*) FROM customers"
        ).fetchone()[0]
        order_count = connection.execute(
            "SELECT COUNT(*) FROM orders"
        ).fetchone()[0]
        item_count = connection.execute(
            "SELECT COUNT(*) FROM order_items"
        ).fetchone()[0]
        total_amount = connection.execute(
            "SELECT SUM(amount) FROM orders"
        ).fetchone()[0]
        null_regions = connection.execute(
            "SELECT COUNT(*) FROM customers WHERE region IS NULL"
        ).fetchone()[0]
        zero_quantity_items = connection.execute(
            "SELECT COUNT(*) FROM order_items WHERE quantity = 0"
        ).fetchone()[0]
        amounts = [
            row[0]
            for row in connection.execute(
                "SELECT amount FROM orders ORDER BY order_id"
            )
        ]

    assert customer_count == 4
    assert order_count == 6
    assert item_count == 8
    assert total_amount == pytest.approx(555.49)
    assert null_regions == 1
    assert zero_quantity_items == 1
    assert amounts == [
        120.50,
        80.00,
        45.00,
        210.00,
        0.00,
        99.99,
    ]


def test_golden_sqlite_supports_expected_aggregations(tmp_path: Path):
    db_path = create_golden_sqlite(tmp_path / "golden.sqlite")

    with _connect(db_path) as connection:
        revenue_by_region = {
            row[0]: row[1]
            for row in connection.execute(
                """
                SELECT c.region, SUM(o.amount)
                FROM orders AS o
                JOIN customers AS c ON c.customer_id = o.customer_id
                GROUP BY c.region
                ORDER BY c.region
                """
            )
        }
        status_counts = {
            row[0]: row[1]
            for row in connection.execute(
                "SELECT status, COUNT(*) FROM orders GROUP BY status"
            )
        }

    assert revenue_by_region == {
        "East": 210.00,
        "North": 200.50,
        "South": 45.00,
        None: 99.99,
    }
    assert status_counts == {
        "completed": 3,
        "pending": 2,
        "cancelled": 1,
    }


def test_golden_sqlite_supports_expected_join(tmp_path: Path):
    db_path = create_golden_sqlite(tmp_path / "golden.sqlite")

    with _connect(db_path) as connection:
        joined_rows = list(
            connection.execute(
                """
                SELECT c.name, o.order_id, o.amount
                FROM orders AS o
                JOIN customers AS c ON c.customer_id = o.customer_id
                ORDER BY o.order_id
                """
            )
        )

    assert joined_rows == [
        ("Acme Corp", 1, 120.50),
        ("Acme Corp", 2, 80.00),
        ("Globex", 3, 45.00),
        ("Initech", 4, 210.00),
        ("Initech", 5, 0.00),
        ("Umbrella", 6, 99.99),
    ]


def test_repeated_creation_is_deterministic(tmp_path: Path):
    first_path = create_golden_sqlite(tmp_path / "first.sqlite")
    second_path = create_golden_sqlite(tmp_path / "second.sqlite")

    with _connect(first_path) as first_connection:
        first_dump = list(first_connection.iterdump())
    with _connect(second_path) as second_connection:
        second_dump = list(second_connection.iterdump())

    assert first_dump == second_dump


def test_fixture_instances_are_isolated(tmp_path: Path):
    first_path = create_golden_sqlite(tmp_path / "first.sqlite")
    second_path = create_golden_sqlite(tmp_path / "second.sqlite")

    with sqlite3.connect(first_path) as connection:
        connection.execute(
            "INSERT INTO customers VALUES (?, ?, ?, ?)",
            (5, "Extra Customer", "West", "2024-01-01"),
        )
        connection.commit()

    with _connect(first_path) as first_connection:
        first_count = first_connection.execute(
            "SELECT COUNT(*) FROM customers"
        ).fetchone()[0]
    with _connect(second_path) as second_connection:
        second_count = second_connection.execute(
            "SELECT COUNT(*) FROM customers"
        ).fetchone()[0]

    assert first_count == 5
    assert second_count == 4


def test_cleanup_golden_sqlite_removes_temporary_database(tmp_path: Path):
    db_path = create_golden_sqlite()
    assert db_path.is_file()

    cleanup_golden_sqlite(db_path)

    assert not db_path.exists()
    assert not db_path.parent.exists()

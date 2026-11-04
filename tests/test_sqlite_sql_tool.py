from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from langgraph_langchain.data.assets import DataAsset
from langgraph_langchain.data.database import (
    DatabaseQueryValidationError,
    SQLiteConnectionConfig,
    SQLiteDatabase,
    SQLiteDataSource,
)
from langgraph_langchain.evidence import (
    SqliteEvidenceError,
    collect_sqlite_query_evidence,
)
from langgraph_langchain.runtime.context import AnalysisRuntime
from langgraph_langchain.tools.sqlite_sql_tool import (
    SQLiteQueryTool,
    SQLiteQueryToolError,
)
from tests.fixtures.phase1.golden.database.golden_sqlite import (
    create_golden_sqlite,
)


def _setup(tmp_path: Path):
    db_path = create_golden_sqlite(tmp_path / "golden.sqlite")
    runtime = AnalysisRuntime(
        workspace_dir=tmp_path,
        session_id="sqlite-tool",
        question="Run controlled SQL",
    )
    source = SQLiteDataSource(
        SQLiteDatabase(SQLiteConnectionConfig(database_path=db_path))
    )
    tool = SQLiteQueryTool(
        source=source,
        runtime=runtime,
        source_identity=source.source_identity,
    )
    return runtime, tool


def test_sql_tool_executes_parameterized_cte_and_registers_snapshot(tmp_path: Path):
    runtime, tool = _setup(tmp_path)
    sql = """
    WITH north_orders AS (
        SELECT o.amount
        FROM orders AS o
        JOIN customers AS c ON c.customer_id = o.customer_id
        WHERE c.region = ?
    )
    SELECT SUM(amount) AS total_amount FROM north_orders
    """
    tool_result, bounded, asset = tool.execute(
        sql=sql,
        parameters=("North",),
        execution_id="exec_sql_1",
    )

    assert tool_result.status == "succeeded"
    assert tool_result.output_asset_id == asset.asset_id
    assert tool_result.query_fingerprint == bounded.metadata.query_sha256
    assert asset.asset_id in runtime.task.input_asset_ids
    assert asset.location is None
    assert asset.source_metadata["snapshot_reference"]
    assert len(bounded.dataframe) == 1

    evidence = collect_sqlite_query_evidence(
        execution_id="exec_sql_1",
        result=bounded,
        output_asset=asset,
    )
    assert evidence.output_asset_id == asset.asset_id
    assert evidence.truncated is False
    assert evidence.provenance["content_hash"] == asset.content_hash
    assert "FROM orders" not in evidence.model_dump_json()


def test_sql_tool_preserves_truncation(tmp_path: Path):
    runtime, tool = _setup(tmp_path)
    tool_result, bounded, asset = tool.execute(
        sql="SELECT order_id, amount FROM orders ORDER BY order_id",
        max_rows=3,
    )

    assert tool_result.truncated is True
    assert bounded.metadata.truncated is True
    assert asset.source_metadata["truncated"] is True
    evidence = collect_sqlite_query_evidence(
        execution_id="exec_sql_truncated",
        result=bounded,
        output_asset=asset,
    )
    assert evidence.truncated is True
    assert evidence.provenance["returned_row_count"] == 3


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO customers VALUES (99, 'bad', 'x', '2026-01-01')",
        "UPDATE customers SET name = 'bad'",
        "DELETE FROM customers",
        "CREATE TABLE bad (id INTEGER)",
        "SELECT 1; DELETE FROM customers",
    ],
)
def test_sql_tool_rejects_unsafe_sql_without_asset(tmp_path: Path, sql: str):
    runtime, tool = _setup(tmp_path)
    before = dict(runtime.assets)

    with pytest.raises(DatabaseQueryValidationError):
        tool.execute(sql=sql)

    assert runtime.assets == before


def test_sql_tool_does_not_register_on_registration_failure(
    tmp_path: Path, monkeypatch
):
    runtime, tool = _setup(tmp_path)

    def fail_register(*args, **kwargs):
        raise RuntimeError("simulated registration failure")

    monkeypatch.setattr(runtime, "register_sqlite_result", fail_register)
    with pytest.raises(SQLiteQueryToolError, match="registration failed"):
        tool.execute(sql="SELECT 1 AS ok")

    assert runtime.assets == {}


def test_sqlite_evidence_requires_real_registered_asset(tmp_path: Path):
    runtime, tool = _setup(tmp_path)
    _, bounded, asset = tool.execute(sql="SELECT order_id FROM orders")
    fake_asset = DataAsset.from_sqlite_query(
        dataframe=bounded.dataframe,
        source_identity="sqlite:sha256:" + "a" * 64,
        query_fingerprint="b" * 64,
        query_type="sql",
    ).model_copy(update={"content_hash": None})

    with pytest.raises(SqliteEvidenceError, match="content hash"):
        collect_sqlite_query_evidence(
            execution_id="exec_bad",
            result=bounded,
            output_asset=fake_asset,
        )

    evidence = collect_sqlite_query_evidence(
        execution_id="exec_good",
        result=bounded,
        output_asset=asset,
    )
    assert evidence.validation_status == "verified"
    assert evidence.provenance["snapshot_reference"] is not None

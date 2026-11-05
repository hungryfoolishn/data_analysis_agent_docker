from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from langgraph_langchain.data.assets import DataAsset
from langgraph_langchain.data.database import (
    SQLiteConnectionConfig,
    SQLiteDatabase,
    SQLiteDataSource,
)
from langgraph_langchain.evidence import collect_sqlite_query_evidence
from langgraph_langchain.runtime.analysis_operations import execute_analysis_operation
from langgraph_langchain.runtime.analysis_workflow import prepare_file_analysis
from langgraph_langchain.runtime.context import AnalysisRuntime
from langgraph_langchain.runtime.sqlite_workflow import prepare_sqlite_analysis
from langgraph_langchain.tools.sqlite_sql_tool import SQLiteQueryTool


def _runtime(tmp_path: Path) -> AnalysisRuntime:
    return AnalysisRuntime(
        workspace_dir=tmp_path,
        session_id="phase1-integration",
        question="Analyze registered assets",
    )


def test_file_analysis_uses_asset_and_resolved_input(tmp_path: Path):
    frame = pd.DataFrame(
        {
            "region": ["North", "North", "South"],
            "amount": [100.0, 120.0, 80.0],
        }
    )
    path = tmp_path / "sales.csv"
    frame.to_csv(path, index=False)
    runtime = _runtime(tmp_path)

    prepared = prepare_file_analysis(
        runtime,
        path,
        goal="Sum total amount group by region",
    )

    assert prepared.asset.asset_id in runtime.task.input_asset_ids
    assert prepared.asset.location == str(path)
    assert prepared.runtime_plan.input_asset_ids == [prepared.asset.asset_id]
    assert all(step.method == "run_analysis_operation" for step in prepared.runtime_plan.steps)
    assert all(step.parameters for step in prepared.runtime_plan.steps)

    resolved = runtime.asset_resolver.resolve(prepared.asset.asset_id)
    pd.testing.assert_frame_equal(resolved.dataframe, frame)

    analysis_step = prepared.runtime_plan.steps[-1]
    output = execute_analysis_operation(
        resolved.dataframe,
        operation=analysis_step.parameters["operation"],
        parameters={
            key: value
            for key, value in analysis_step.parameters.items()
            if key not in {"operation", "input_refs"}
        },
    )
    assert output.loc[0, "amount_sum"] == 220.0


def test_sqlite_workflow_and_registered_query_tool(tmp_path: Path):
    db_path = tmp_path / "orders.sqlite"
    connection = sqlite3.connect(db_path)
    try:
        connection.executescript(
            """
            CREATE TABLE customers (customer_id INTEGER PRIMARY KEY, region TEXT);
            CREATE TABLE orders (order_id INTEGER PRIMARY KEY, customer_id INTEGER, amount REAL);
            INSERT INTO customers VALUES (1, 'North'), (2, 'South');
            INSERT INTO orders VALUES (1, 1, 100.0), (2, 1, 120.0), (3, 2, 80.0);
            """
        )
        connection.commit()
    finally:
        connection.close()

    runtime = _runtime(tmp_path)
    asset, schema, plan = prepare_sqlite_analysis(
        runtime,
        db_path,
        goal="Sum total amount group by region",
    )

    assert asset.source_type == "sqlite_database"
    assert asset.asset_id in runtime.task.input_asset_ids
    assert any(table["table"] == "orders" for table in schema.summary())
    assert plan.steps[0].method == "run_sqlite_query"
    assert plan.steps[0].parameters["sql"]

    source = SQLiteDataSource(SQLiteDatabase(SQLiteConnectionConfig(database_path=db_path)))
    tool = SQLiteQueryTool(
        source=source,
        runtime=runtime,
        source_identity=source.source_identity,
    )
    tool_result, bounded, output_asset = tool.execute(
        sql="SELECT customer_id, SUM(amount) AS amount_sum FROM orders GROUP BY customer_id"
    )
    evidence = collect_sqlite_query_evidence(
        execution_id=tool_result.execution_id,
        result=bounded,
        output_asset=output_asset,
        source_asset_id=asset.asset_id,
    )
    resolved = runtime.asset_resolver.resolve(output_asset.asset_id)

    assert tool_result.status == "succeeded"
    assert evidence.truncated is False
    assert evidence.output_asset_id == output_asset.asset_id
    assert resolved.dataframe["amount_sum"].sum() == 300.0


def test_registry_contains_phase1_tools():
    from langgraph_langchain.tools import registry

    assert "run_analysis_operation" in registry.get_tool_names()
    assert "run_sqlite_query" in registry.get_tool_names()

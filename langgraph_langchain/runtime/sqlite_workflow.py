"""Schema-aware deterministic planning for SQLite analysis."""

from __future__ import annotations

import re
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence
from uuid import uuid4

from langgraph_langchain.data.assets import DataAsset
from langgraph_langchain.data.database import (
    SQLiteDatabase,
    SQLiteConnectionConfig,
    SQLiteDataSource,
)
from langgraph_langchain.runtime.context import AnalysisRuntime
from langgraph_langchain.runtime.models import RuntimePlanStep
from langgraph_langchain.runtime.plans import AnalysisPlan


@dataclass(frozen=True)
class SqliteSchemaContext:
    database_asset_id: str
    tables: dict[str, dict[str, str]]

    def summary(self) -> list[dict[str, Any]]:
        return [
            {
                "table": table,
                "columns": [
                    {"name": name, "type": dtype}
                    for name, dtype in columns.items()
                ],
            }
            for table, columns in sorted(self.tables.items())
        ]


def register_sqlite_database_asset(
    runtime: AnalysisRuntime,
    path: Path,
) -> tuple[DataAsset, SqliteSchemaContext]:
    source = SQLiteDataSource(SQLiteDatabase(SQLiteConnectionConfig(database_path=path)))
    schema = source.introspect_schema()
    tables = {
        table.name: {
            column.name: column.declared_type or "UNKNOWN"
            for column in table.columns
        }
        for table in schema.tables
    }
    asset = runtime.register_dataframe(
        dataframe=source.preview(rows=0, table_name=schema.tables[0].name)
        if len(schema.tables) == 1
        else source.preview(rows=0, table_name=schema.tables[0].name),
        source_path=path,
        source_type=path.suffix.lower().lstrip(".") or "sqlite",
        source_metadata={
            "database_asset": True,
            "source_identity": source.source_identity,
            "tables": sorted(tables),
        },
    )
    # Preserve the database source contract; register_dataframe's file path is
    # only a durable location reference and not a query-result snapshot.
    asset = asset.model_copy(
        update={
            "source_type": "sqlite_database",
            "source_metadata": {
                **asset.source_metadata,
                "database_asset": True,
                "source_identity": source.source_identity,
                "tables": sorted(tables),
            },
        }
    )
    runtime.assets[asset.asset_id] = asset
    return asset, SqliteSchemaContext(
        database_asset_id=asset.asset_id,
        tables=tables,
    )


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _match_table(goal: str, schema: SqliteSchemaContext) -> str | None:
    text = goal.lower()
    best: tuple[int, str] | None = None
    for table, columns in schema.tables.items():
        score = 0
        if table.lower() in text:
            score += 10
        for column in columns:
            if column.lower() in text:
                score += 2
        if best is None or score > best[0]:
            best = (score, table)
    return best[1] if best and best[0] > 0 else None


def _match_column(goal: str, columns: dict[str, str], *, numeric_only: bool = False) -> str | None:
    text = goal.lower()
    for column, dtype in sorted(columns.items()):
        if column.lower() not in text:
            continue
        if numeric_only and not any(
            token in dtype.upper()
            for token in ("INT", "REAL", "FLOA", "DOUB", "NUM", "DEC")
        ):
            continue
        return column
    numeric_columns = [
        column
        for column, dtype in columns.items()
        if any(token in dtype.upper() for token in ("INT", "REAL", "FLOA", "DOUB", "NUM", "DEC"))
    ]
    return numeric_columns[0] if numeric_columns and numeric_only else None


def _parse_group(goal: str) -> str | None:
    match = re.search(
        r"(?:group(?:ed)?\s+by|按|根据|每个)\s*[\s:：]*([\w\u4e00-\u9fff]+)",
        goal,
        re.IGNORECASE,
    )
    return match.group(1) if match else None


def _parse_limit(goal: str) -> int:
    match = re.search(r"\btop\s+(\d+)\b|前\s*(\d+)\s*名", goal, re.IGNORECASE)
    if not match:
        return 20
    return int(match.group(1) or match.group(2))


def build_sqlite_plan(
    *,
    goal: str,
    database_asset_id: str,
    schema: SqliteSchemaContext,
) -> AnalysisPlan:
    table = _match_table(goal, schema)
    if table is None and schema.tables:
        table = sorted(schema.tables)[0]
    if table is None:
        raise ValueError("SQLite schema has no queryable table")
    columns = schema.tables[table]
    lower_goal = goal.lower()
    sql: str
    parameters: list[Any] = []

    group_by = _parse_group(goal)
    group_by = group_by if group_by in columns else _match_column(goal, columns)
    metric = _match_column(goal, columns, numeric_only=True)
    if group_by and metric and group_by != metric:
        agg = "avg" if re.search(r"average|avg|平均", lower_goal) else "sum"
        sql = (
            f"SELECT {_quote(group_by)}, {agg}({_quote(metric)}) AS "
            f"{agg}_{metric} FROM {_quote(table)} "
            f"GROUP BY {_quote(group_by)} ORDER BY {agg}_{metric} DESC"
        )
    elif re.search(r"\btrend\b|趋势|随时间|over time", lower_goal):
        time_column = _match_column(goal, columns) or sorted(columns)[0]
        value_column = _match_column(goal, columns, numeric_only=True) or time_column
        sql = (
            f"SELECT {_quote(time_column)}, {_quote(value_column)} "
            f"FROM {_quote(table)} ORDER BY {_quote(time_column)}"
        )
    elif re.search(r"\btop\s+\d+\b|前\s*\d+\s*名|rank|排名", lower_goal):
        metric = metric or sorted(columns)[-1]
        sql = (
            f"SELECT * FROM {_quote(table)} "
            f"ORDER BY {_quote(metric)} DESC LIMIT {_parse_limit(goal)}"
        )
    elif re.search(r"\bdata quality\b|质量问题|缺失|重复|数据质量", lower_goal):
        sql = f"SELECT * FROM {_quote(table)} LIMIT 200"
    else:
        sql = f"SELECT * FROM {_quote(table)} LIMIT 100"

    sql_step = RuntimePlanStep(
        step_id=f"step_{uuid4().hex}",
        objective=f"Run read-only SQLite query for: {goal}",
        method="run_sqlite_query",
        required_inputs=[database_asset_id],
        expected_outputs=["query_result_asset"],
        parameters={
            "asset_id": database_asset_id,
            "sql": sql,
            "parameters_json": json.dumps(parameters, ensure_ascii=False),
        },
    )
    return AnalysisPlan(
        goal=goal,
        input_asset_ids=[database_asset_id],
        steps=[sql_step],
        success_criteria=[
            "SQL is a single read-only query.",
            "The result is registered as a snapshot-backed DataAsset.",
            "Evidence records the execution and query fingerprint.",
        ],
    )


def prepare_sqlite_analysis(
    runtime: AnalysisRuntime,
    path: Path,
    *,
    goal: str,
) -> tuple[DataAsset, SqliteSchemaContext, AnalysisPlan]:
    asset, schema = register_sqlite_database_asset(runtime, path)
    plan = build_sqlite_plan(
        goal=goal,
        database_asset_id=asset.asset_id,
        schema=schema,
    )
    runtime.initialize_plan(plan)
    return asset, schema, plan

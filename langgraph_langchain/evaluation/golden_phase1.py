"""Deterministic Phase 1 golden evaluation harness.

The harness avoids LLM calls and evaluates planner, deterministic operations,
SQLite SQL tool, evidence, report validation, and expected numeric answers.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

import pandas as pd

from langgraph_langchain.data.assets import DataAsset
from langgraph_langchain.data.database import (
    SQLiteConnectionConfig,
    SQLiteDatabase,
    SQLiteDataSource,
)
from langgraph_langchain.evidence import (
    collect_operation_evidence,
    collect_sqlite_query_evidence,
)
from langgraph_langchain.runtime.analysis_operations import (
    execute_analysis_operation,
)
from langgraph_langchain.runtime.analysis_workflow import prepare_file_analysis
from langgraph_langchain.runtime.analysis_report import (
    ReportFinding,
    build_analysis_report,
    validate_analysis_report,
)
from langgraph_langchain.runtime.context import AnalysisRuntime
from langgraph_langchain.runtime.general_planner import GeneralPlanStep
from langgraph_langchain.runtime.sqlite_workflow import prepare_sqlite_analysis
from langgraph_langchain.tools.sqlite_sql_tool import SQLiteQueryTool


@dataclass(frozen=True)
class GoldenDataset:
    dataset_id: str
    kind: str
    filename: str
    builder: Callable[[Path], None]
    expected_rows: int
    quality_notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class GoldenQuestion:
    question_id: str
    dataset_id: str
    question: str
    operation: str
    parameters: dict[str, Any]
    expected_column: str | None
    expected_value: Any
    expected_selector: str = "first"
    tolerance: float = 0.0
    required_limitation: str | None = None
    sql: str | None = None
    sql_parameters: tuple[Any, ...] = ()


def _write_csv(path: Path, frame: pd.DataFrame) -> None:
    frame.to_csv(path, index=False, encoding="utf-8")


def _build_simple_numeric(path: Path) -> None:
    _write_csv(path, pd.DataFrame({"value": [10, 20, 30, 40, 50]}))


def _build_regional_sales(path: Path) -> None:
    _write_csv(
        path,
        pd.DataFrame(
            {
                "region": ["North", "South", "North", "East", "West"],
                "amount": [120.5, 80.0, 80.0, 45.0, 99.5],
            }
        ),
    )


def _build_time_series(path: Path) -> None:
    _write_csv(
        path,
        pd.DataFrame(
            {
                "order_date": [
                    "2026-01-01",
                    "2026-01-02",
                    "2026-01-03",
                    "2026-01-04",
                ],
                "amount": [100.0, 120.0, 90.0, 150.0],
            }
        ),
    )


def _build_missing_values(path: Path) -> None:
    _write_csv(
        path,
        pd.DataFrame(
            {
                "region": ["North", "South", None, "East"],
                "amount": [10.0, None, 30.0, 40.0],
            }
        ),
    )


def _build_duplicates(path: Path) -> None:
    _write_csv(
        path,
        pd.DataFrame(
            {
                "order_id": [1, 2, 2, 3],
                "amount": [10.0, 20.0, 20.0, 30.0],
            }
        ),
    )


def _build_outliers(path: Path) -> None:
    _write_csv(
        path,
        pd.DataFrame({"value": [10, 12, 11, 13, 12, 500]}),
    )


def _build_mixed_types(path: Path) -> None:
    _write_csv(
        path,
        pd.DataFrame(
            {
                "category": ["a", "b", "a"],
                "created_at": ["2026-01-01", "2026-01-02", "2026-01-03"],
                "value": [1, 2.5, 3],
                "score": [2.0, 4.0, 6.0],
            }
        ),
    )


def _build_insufficient(path: Path) -> None:
    _write_csv(path, pd.DataFrame({"value": [1]}))


def _build_customers_db(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE customers (
                customer_id INTEGER PRIMARY KEY,
                region TEXT
            );
            CREATE TABLE orders (
                order_id INTEGER PRIMARY KEY,
                customer_id INTEGER,
                amount REAL
            );
            INSERT INTO customers VALUES (1, 'North'), (2, 'South'), (3, 'North');
            INSERT INTO orders VALUES (1, 1, 100.0), (2, 1, 120.0), (3, 2, 80.0);
            """
        )
        connection.commit()
    finally:
        connection.close()


def _build_employee_db(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE employees (
                employee_id INTEGER PRIMARY KEY,
                department TEXT,
                salary REAL
            );
            INSERT INTO employees VALUES (1, 'Engineering', 120000.0);
            INSERT INTO employees VALUES (2, 'Engineering', 140000.0);
            INSERT INTO employees VALUES (3, 'Sales', 90000.0);
            """
        )
        connection.commit()
    finally:
        connection.close()


DATASETS: tuple[GoldenDataset, ...] = (
    GoldenDataset("simple_numeric", "file", "simple_numeric.csv", _build_simple_numeric, 5),
    GoldenDataset("regional_sales", "file", "regional_sales.csv", _build_regional_sales, 5),
    GoldenDataset("time_series", "file", "time_series.csv", _build_time_series, 4),
    GoldenDataset("missing_values", "file", "missing_values.csv", _build_missing_values, 4, ("contains missing values",)),
    GoldenDataset("duplicates", "file", "duplicates.csv", _build_duplicates, 4, ("contains duplicate rows",)),
    GoldenDataset("outliers", "file", "outliers.csv", _build_outliers, 6, ("contains outliers",)),
    GoldenDataset("mixed_types", "file", "mixed_types.csv", _build_mixed_types, 3),
    GoldenDataset("insufficient_data", "file", "insufficient_data.csv", _build_insufficient, 1, ("small sample",)),
    GoldenDataset("customers_db", "sqlite", "customers_db.sqlite", _build_customers_db, 3),
    GoldenDataset("employees_db", "sqlite", "employees_db.sqlite", _build_employee_db, 3),
)


QUESTIONS: tuple[GoldenQuestion, ...] = (
    GoldenQuestion("Q001", "simple_numeric", "Summarize value", "summarize", {}, "value", 5.0),
    GoldenQuestion("Q002", "simple_numeric", "Sum total value", "group_aggregate", {"group_by": "value", "metric": "value", "agg": "sum"}, "value_sum", 150.0, expected_selector="sum"),
    GoldenQuestion("Q003", "simple_numeric", "Sort value descending", "sort", {"by": "value", "ascending": False}, "value", 50.0),
    GoldenQuestion("Q004", "regional_sales", "Sum total amount group by region", "group_aggregate", {"group_by": "region", "metric": "amount", "agg": "sum"}, "amount_sum", 200.5),
    GoldenQuestion("Q005", "regional_sales", "Average amount group by region", "group_aggregate", {"group_by": "region", "metric": "amount", "agg": "mean"}, "amount_mean", 100.25),
    GoldenQuestion("Q006", "regional_sales", "Count amount group by region", "group_aggregate", {"group_by": "region", "metric": "amount", "agg": "count"}, "amount_count", 2.0),
    GoldenQuestion("Q007", "regional_sales", "Show top 2 amount", "sort", {"by": "amount", "ascending": False}, "amount", 120.5),
    GoldenQuestion("Q008", "regional_sales", "Filter amount greater than 50", "filter", {"column": "amount", "operator": "gt", "value": 50}, "amount", 120.5),
    GoldenQuestion("Q009", "time_series", "Show amount trend over time", "trend", {"time_column": "order_date", "value_column": "amount"}, "value_change", 20.0),
    GoldenQuestion("Q010", "time_series", "Sum amount", "group_aggregate", {"group_by": "order_date", "metric": "amount", "agg": "sum"}, "amount_sum", 460.0, expected_selector="sum"),
    GoldenQuestion("Q011", "time_series", "Maximum amount", "group_aggregate", {"group_by": "order_date", "metric": "amount", "agg": "max"}, "amount_max", 150.0),
    GoldenQuestion("Q012", "time_series", "Average amount", "group_aggregate", {"group_by": "order_date", "metric": "amount", "agg": "mean"}, "amount_mean", 115.0, expected_selector="mean"),
    GoldenQuestion("Q013", "time_series", "Minimum amount", "group_aggregate", {"group_by": "order_date", "metric": "amount", "agg": "min"}, "amount_min", 90.0, expected_selector="min"),
    GoldenQuestion("Q014", "missing_values", "Data quality overview", "data_quality", {}, "missing_count", 1.0),
    GoldenQuestion("Q015", "missing_values", "Sum amount", "group_aggregate", {"group_by": "region", "metric": "amount", "agg": "sum"}, "amount_sum", 40.0, expected_selector="sum"),
    GoldenQuestion("Q016", "missing_values", "Show missing rows", "filter", {"column": "region", "operator": "eq", "value": None}, "amount", 30.0),
    GoldenQuestion("Q017", "duplicates", "Count duplicate rows", "data_quality", {}, "duplicate_row_count", 1.0),
    GoldenQuestion("Q018", "duplicates", "Sum amount group by order_id", "group_aggregate", {"group_by": "order_id", "metric": "amount", "agg": "sum"}, "amount_sum", 40.0),
    GoldenQuestion("Q019", "duplicates", "Filter duplicate order", "filter", {"column": "order_id", "operator": "eq", "value": 2}, "amount", 20.0),
    GoldenQuestion("Q020", "outliers", "Detect value anomalies", "detect_anomalies", {"value_column": "value", "method": "iqr"}, "value", 500.0),
    GoldenQuestion("Q021", "outliers", "Sum value", "group_aggregate", {"group_by": "value", "metric": "value", "agg": "sum"}, "value_sum", 558.0, expected_selector="sum"),
    GoldenQuestion("Q022", "outliers", "Maximum value", "group_aggregate", {"group_by": "value", "metric": "value", "agg": "max"}, "value_max", 500.0),
    GoldenQuestion("Q023", "mixed_types", "Correlate value and score", "correlation", {"columns": ["value", "score"]}, "correlation", None),
    GoldenQuestion("Q024", "mixed_types", "Data quality overview", "data_quality", {"time_column": "created_at"}, "missing_count", 0.0),
    GoldenQuestion("Q025", "mixed_types", "Sum value", "group_aggregate", {"group_by": "category", "metric": "value", "agg": "sum"}, "value_sum", 4.0),
    GoldenQuestion("Q026", "customers_db", "Sum order amount group by customer_id", "group_aggregate", {"group_by": "customer_id", "metric": "amount", "agg": "sum"}, "amount_sum", 220.0, sql="SELECT customer_id, SUM(amount) AS amount_sum FROM orders GROUP BY customer_id"),
    GoldenQuestion("Q027", "customers_db", "Join orders to customer region and sum amount", "group_aggregate", {"group_by": "region", "metric": "amount", "agg": "sum"}, "amount_sum", 220.0, sql="SELECT c.region, SUM(o.amount) AS amount_sum FROM orders AS o JOIN customers AS c ON c.customer_id = o.customer_id GROUP BY c.region"),
    GoldenQuestion("Q028", "customers_db", "Use a CTE for North revenue", "group_aggregate", {"group_by": "region", "metric": "amount", "agg": "sum"}, "amount_sum", 220.0, sql="WITH north AS (SELECT o.amount FROM orders AS o JOIN customers AS c ON c.customer_id = o.customer_id WHERE c.region = 'North') SELECT SUM(amount) AS amount_sum FROM north"),
    GoldenQuestion("Q029", "employees_db", "Average salary group by department", "group_aggregate", {"group_by": "department", "metric": "salary", "agg": "mean"}, "salary_mean", 130000.0, sql="SELECT department, AVG(salary) AS salary_mean FROM employees GROUP BY department"),
    GoldenQuestion("Q030", "employees_db", "Count employees group by department", "group_aggregate", {"group_by": "department", "metric": "salary", "agg": "count"}, "salary_count", 2.0, sql="SELECT department, COUNT(salary) AS salary_count FROM employees GROUP BY department"),
)


DATASET_BY_ID = {item.dataset_id: item for item in DATASETS}
QUESTION_BY_ID = {item.question_id: item for item in QUESTIONS}


def build_golden_datasets(root: Path) -> dict[str, Path]:
    root.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    for dataset in DATASETS:
        path = root / dataset.filename
        dataset.builder(path)
        paths[dataset.dataset_id] = path
    return paths


def _expected_value(output: pd.DataFrame, question: GoldenQuestion) -> Any:
    if question.expected_column is None or question.expected_value is None:
        return None
    if question.expected_column not in output.columns:
        raise AssertionError(f"Expected column missing: {question.expected_column}")
    values = output[question.expected_column].dropna().tolist()
    if not values:
        return None
    if question.expected_selector == "first":
        selected = values[0]
    elif question.expected_selector == "last":
        selected = values[-1]
    elif question.expected_selector == "max":
        selected = max(values)
    elif question.expected_selector == "min":
        selected = min(values)
    elif question.expected_selector == "sum":
        selected = sum(float(item) for item in values)
    elif question.expected_selector == "mean":
        selected = sum(float(item) for item in values) / len(values)
    else:
        raise AssertionError(f"Unknown expected selector: {question.expected_selector}")
    if isinstance(question.expected_value, float):
        return float(selected)
    return selected


def _numeric_summary(frame: pd.DataFrame) -> dict[str, float]:
    numeric = frame.select_dtypes(include=["number"]).columns
    result: dict[str, float] = {}
    for column in numeric:
        series = pd.to_numeric(frame[column], errors="coerce").dropna()
        if not series.empty:
            result[str(column)] = float(series.iloc[0])
    return result


def _run_file_case(
    question: GoldenQuestion,
    dataset: GoldenDataset,
    path: Path,
    workspace: Path,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    runtime = AnalysisRuntime(
        workspace_dir=workspace,
        session_id=f"golden_{question.question_id}",
        question=question.question,
    )
    prepared = prepare_file_analysis(runtime, path, goal=question.question)
    resolved = runtime.asset_resolver.resolve(prepared.asset.asset_id)
    output = execute_analysis_operation(
        resolved.dataframe,
        operation=question.operation,
        parameters=question.parameters,
    )
    output_path = workspace / "output.parquet"
    output.to_parquet(output_path, index=False)
    output_asset = runtime.register_dataframe(
        dataframe=output,
        source_path=output_path,
        source_type="parquet",
        source_metadata={
            "operation": question.operation,
            "parameters": question.parameters,
            "input_asset_ids": [prepared.asset.asset_id],
        },
    )
    evidence = collect_operation_evidence(
        execution_id=f"exec_{question.question_id}",
        operation=question.operation,
        parameters=question.parameters,
        input_asset_ids=[prepared.asset.asset_id],
        output_asset=output_asset,
        numeric_values=_numeric_summary(output),
    )
    return output, {
        "evidence_id": evidence.evidence_id,
        "validation_status": evidence.validation_status,
        "truncated": False,
        "numeric_values": evidence.numeric_values,
        "evidence_text": "Verified deterministic operation.",
        "asset_ids": [output_asset.asset_id],
        "execution_id": evidence.execution_id,
    }


def _run_sqlite_case(
    question: GoldenQuestion,
    dataset: GoldenDataset,
    path: Path,
    workspace: Path,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    runtime = AnalysisRuntime(
        workspace_dir=workspace,
        session_id=f"golden_{question.question_id}",
        question=question.question,
    )
    asset, schema, plan = prepare_sqlite_analysis(runtime, path, goal=question.question)
    source = SQLiteDataSource(SQLiteDatabase(SQLiteConnectionConfig(database_path=path)))
    query_tool = SQLiteQueryTool(
        source=source,
        runtime=runtime,
        source_identity=source.source_identity,
    )
    tool_result, bounded, output_asset = query_tool.execute(
        sql=question.sql or plan.steps[0].parameters["sql"],
        parameters=question.sql_parameters,
        max_rows=1000,
        execution_id=f"exec_{question.question_id}",
    )
    evidence = collect_sqlite_query_evidence(
        execution_id=f"exec_{question.question_id}",
        result=bounded,
        output_asset=output_asset,
        source_asset_id=asset.asset_id,
    )
    return bounded.dataframe, {
        "evidence_id": evidence.evidence_id,
        "validation_status": "verified",
        "truncated": evidence.truncated,
        "numeric_values": _numeric_summary(bounded.dataframe),
        "evidence_text": "Verified SQLite query.",
        "asset_ids": [output_asset.asset_id],
        "execution_id": evidence.execution_id,
    }


def run_golden_question(
    question_id: str,
    *,
    dataset_paths: dict[str, Path],
    output_dir: Path,
) -> dict[str, Any]:
    question = QUESTION_BY_ID[question_id]
    dataset = DATASET_BY_ID[question.dataset_id]
    workspace = output_dir / "workspaces" / question_id
    started = time.monotonic()
    try:
        if dataset.kind == "file":
            output, evidence_record = _run_file_case(
                question,
                dataset,
                dataset_paths[dataset.dataset_id],
                workspace,
            )
        elif dataset.kind == "sqlite":
            output, evidence_record = _run_sqlite_case(
                question,
                dataset,
                dataset_paths[dataset.dataset_id],
                workspace,
            )
        else:
            raise ValueError(f"Unsupported golden dataset kind: {dataset.kind}")

        actual = _expected_value(output, question)
        numeric_ok = True
        numeric_errors: list[str] = []
        if question.expected_value is not None:
            if actual is None:
                numeric_ok = False
                numeric_errors.append("Expected numeric result was absent")
            else:
                difference = abs(float(actual) - float(question.expected_value))
                numeric_ok = difference <= question.tolerance
                if not numeric_ok:
                    numeric_errors.append(
                        f"Expected {question.expected_value}, got {actual}"
                    )

        limitations = list(dataset.quality_notes)
        if question.required_limitation:
            limitations.append(question.required_limitation)
        report = build_analysis_report(
            task_id=question.question_id,
            user_goal=question.question,
            status="success",
            executive_summary=f"Completed {question.operation} on {dataset.dataset_id}.",
            findings=[
                ReportFinding(
                    finding_id="F001",
                    claim=f"{question.operation} completed for {dataset.dataset_id}.",
                    supported_by=[evidence_record["evidence_id"]],
                    referenced_asset_ids=evidence_record["asset_ids"],
                )
            ],
            limitations=limitations,
        )
        report_validation = validate_analysis_report(
            report,
            evidence_records={evidence_record["evidence_id"]: evidence_record},
            asset_ids=set(evidence_record["asset_ids"]),
        )
        passed = numeric_ok and report_validation.is_valid
        result = {
            "question_id": question.question_id,
            "dataset_id": dataset.dataset_id,
            "question": question.question,
            "operation": question.operation,
            "status": "passed" if passed else "failed",
            "numeric_ok": numeric_ok,
            "numeric_errors": numeric_errors,
            "actual_value": actual,
            "expected_value": question.expected_value,
            "evidence_id": evidence_record["evidence_id"],
            "report_status": report_validation.status,
            "report_errors": report_validation.errors,
            "report_warnings": report_validation.warnings,
            "duration_seconds": time.monotonic() - started,
        }
    except Exception as exc:
        result = {
            "question_id": question.question_id,
            "dataset_id": question.dataset_id,
            "question": question.question,
            "operation": question.operation,
            "status": "failed",
            "numeric_ok": False,
            "numeric_errors": [str(exc)],
            "actual_value": None,
            "expected_value": question.expected_value,
            "evidence_id": None,
            "report_status": "invalid",
            "report_errors": [str(exc)],
            "report_warnings": [],
            "duration_seconds": time.monotonic() - started,
        }
    question_dir = output_dir / "questions" / question_id
    question_dir.mkdir(parents=True, exist_ok=True)
    (question_dir / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


def run_golden_suite(
    *,
    output_dir: Path,
    question_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    dataset_paths = build_golden_datasets(output_dir / "datasets")
    selected = list(question_ids or [item.question_id for item in QUESTIONS])
    results = [run_golden_question(qid, dataset_paths=dataset_paths, output_dir=output_dir) for qid in selected]
    passed = [item for item in results if item["status"] == "passed"]
    numeric_passed = [item for item in results if item["numeric_ok"]]
    evidence_passed = [item for item in results if item["evidence_id"]]
    report_valid = [item for item in results if item["report_status"] in {"valid", "warning"}]
    summary = {
        "suite_version": "phase1-mvp-v1",
        "dataset_count": len(DATASETS),
        "question_count": len(QUESTIONS),
        "executed_count": len(results),
        "passed_count": len(passed),
        "success_rate": len(passed) / len(results) if results else 0.0,
        "numeric_correct_rate": len(numeric_passed) / len(results) if results else 0.0,
        "evidence_complete_rate": len(evidence_passed) / len(results) if results else 0.0,
        "report_valid_rate": len(report_valid) / len(results) if results else 0.0,
        "results": results,
    }
    (output_dir / "golden_results.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    markdown = [
        "# Phase 1 Golden Evaluation",
        "",
        f"- Dataset count: {summary['dataset_count']}",
        f"- Question count: {summary['executed_count']}",
        f"- Passed: {summary['passed_count']}",
        f"- Success rate: {summary['success_rate']:.2%}",
        f"- Numeric correctness: {summary['numeric_correct_rate']:.2%}",
        f"- Evidence completeness: {summary['evidence_complete_rate']:.2%}",
        f"- Report validity: {summary['report_valid_rate']:.2%}",
        "",
    ]
    for result in results:
        markdown.append(
            f"- {result['question_id']}: {result['status']} "
            f"({result['operation']}; numeric={result['numeric_ok']})"
        )
    (output_dir / "golden_report.md").write_text("\n".join(markdown) + "\n", encoding="utf-8")
    return summary

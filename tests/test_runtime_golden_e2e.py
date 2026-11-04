from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from langgraph_langchain.data.assets import DataAsset, hash_file
from langgraph_langchain.data.database import (
    DatabaseQueryValidationError,
    SQLiteConnectionConfig,
    SQLiteDatabase,
    SQLiteDataSource,
)
from langgraph_langchain.evidence import collect_sqlite_query_evidence
from langgraph_langchain.execution.python_task_executor import PythonTaskExecutor
from langgraph_langchain.runtime.analysis_report import (
    ReportFinding,
    build_analysis_report,
    render_analysis_report,
    validate_analysis_report,
)
from langgraph_langchain.runtime.context import AnalysisRuntime
from langgraph_langchain.runtime.general_planner import (
    DeterministicAnalysisPlanner,
)
from langgraph_langchain.schemas import AnalysisTask
from langgraph_langchain.tools.sqlite_sql_tool import SQLiteQueryTool
from tests.fixtures.phase1.golden.database.golden_sqlite import (
    create_golden_sqlite,
)


def _runtime(tmp_path: Path, session_id: str) -> AnalysisRuntime:
    return AnalysisRuntime(
        workspace_dir=tmp_path,
        session_id=session_id,
        question="Run golden analysis",
    )


def _task(session_id: str, asset_id: str, question: str) -> AnalysisTask:
    return AnalysisTask(
        session_id=session_id,
        question=question,
        input_asset_ids=[asset_id],
    )


def _run_python(runtime: AnalysisRuntime, tmp_path: Path, task, code: str):
    request = runtime.build_execution_request(task)
    request.code = code
    return PythonTaskExecutor(workspace_dir=tmp_path).execute(request)


def test_file_golden_chain_csv(tmp_path: Path):
    frame = pd.DataFrame(
        {
            "region": ["North", "North", "South"],
            "amount": [120.5, 80.0, 45.0],
            "order_date": ["2026-01-01", "2026-01-02", "2026-01-03"],
        }
    )
    path = tmp_path / "orders.csv"
    frame.to_csv(path, index=False)
    runtime = _runtime(tmp_path, "file-golden")
    asset = DataAsset.from_dataframe(
        dataframe=frame,
        source_path=path,
        source_type="csv",
    )
    runtime.assets[asset.asset_id] = asset
    runtime.task.input_asset_ids.append(asset.asset_id)

    plan = DeterministicAnalysisPlanner().plan(
        goal="Sum total amount group by region",
        input_asset_ids=[asset.asset_id],
        asset_columns={asset.asset_id: list(frame.columns)},
    )
    assert plan.plan.steps[-1].operation == "group_aggregate"

    task = _task("file-golden", asset.asset_id, "Aggregate file revenue")
    execution = _run_python(
        runtime,
        tmp_path,
        task,
        "frame = runtime_inputs['" + asset.asset_id + "']\n"
        "assert frame.groupby('region')['amount'].sum().to_dict()['North'] == 200.5\n"
        "print('FILE_E2E_OK')",
    )

    assert execution.status == "succeeded"
    assert "FILE_E2E_OK" in execution.stdout_preview
    assert execution.input_asset_ids == [asset.asset_id]


def test_file_golden_chain_parquet(tmp_path: Path):
    frame = pd.DataFrame(
        {
            "category": ["a", "b", "a"],
            "value": [3.0, 4.0, 5.0],
        }
    )
    path = tmp_path / "items.parquet"
    frame.to_parquet(path, index=False)
    runtime = _runtime(tmp_path, "parquet-golden")
    asset = DataAsset.from_dataframe(
        dataframe=frame,
        source_path=path,
        source_type="parquet",
    )
    runtime.assets[asset.asset_id] = asset
    runtime.task.input_asset_ids.append(asset.asset_id)
    task = _task("parquet-golden", asset.asset_id, "Summarize parquet")

    execution = _run_python(
        runtime,
        tmp_path,
        task,
        "frame = runtime_inputs['" + asset.asset_id + "']\n"
        "assert len(frame) == 3\n"
        "print('PARQUET_E2E_OK')",
    )

    assert execution.status == "succeeded"
    assert "PARQUET_E2E_OK" in execution.stdout_preview


def test_excel_golden_chain(tmp_path: Path):
    frame = pd.DataFrame({"region": ["East"], "amount": [99.0]})
    path = tmp_path / "orders.xlsx"
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        frame.to_excel(writer, sheet_name="Orders", index=False)
    runtime = _runtime(tmp_path, "excel-golden")
    asset = DataAsset.from_dataframe(
        dataframe=frame,
        source_path=path,
        source_type="xlsx",
        sheet_name="Orders",
    )
    runtime.assets[asset.asset_id] = asset
    runtime.task.input_asset_ids.append(asset.asset_id)
    task = _task("excel-golden", asset.asset_id, "Read Excel sheet")

    execution = _run_python(
        runtime,
        tmp_path,
        task,
        "frame = runtime_inputs['" + asset.asset_id + "']\n"
        "assert frame['region'].tolist() == ['East']\n"
        "print('EXCEL_E2E_OK')",
    )

    assert execution.status == "succeeded"
    assert "EXCEL_E2E_OK" in execution.stdout_preview


def test_sqlite_golden_chain_sql_tool_snapshot_evidence_report(tmp_path: Path):
    db_path = create_golden_sqlite(tmp_path / "golden.sqlite")
    runtime = _runtime(tmp_path, "sqlite-golden")
    source = SQLiteDataSource(
        SQLiteDatabase(SQLiteConnectionConfig(database_path=db_path))
    )
    tool = SQLiteQueryTool(
        source=source,
        runtime=runtime,
        source_identity=source.source_identity,
    )
    tool_result, bounded, asset = tool.execute(
        sql="""
        SELECT c.region AS region, SUM(o.amount) AS total_amount
        FROM orders AS o
        JOIN customers AS c ON c.customer_id = o.customer_id
        WHERE c.region = ?
        GROUP BY c.region
        """,
        parameters=("North",),
        execution_id="exec_golden_sql",
    )

    assert tool_result.status == "succeeded"
    assert asset.location is None
    assert runtime.task.input_asset_ids == [asset.asset_id]

    evidence = collect_sqlite_query_evidence(
        execution_id="exec_golden_sql",
        result=bounded,
        output_asset=asset,
    )
    resolved = runtime.asset_resolver.resolve(asset.asset_id)
    assert resolved.dataframe["total_amount"].tolist() == [200.5]

    planner = DeterministicAnalysisPlanner()
    plan = planner.plan(
        goal="Sum total amount group by region",
        input_asset_ids=[asset.asset_id],
        asset_columns={asset.asset_id: list(resolved.dataframe.columns)},
    )
    assert plan.plan.steps[-1].operation == "group_aggregate"

    # Delete the source database after registration and planning.  Execution
    # must use the verified snapshot.
    db_path.unlink()
    task = _task("sqlite-golden", asset.asset_id, "Aggregate SQLite snapshot")
    execution = _run_python(
        runtime,
        tmp_path,
        task,
        "frame = runtime_inputs['" + asset.asset_id + "']\n"
        "assert frame['total_amount'].tolist() == [200.5]\n"
        "print('SQLITE_E2E_OK')",
    )

    assert execution.status == "succeeded"
    assert "SQLITE_E2E_OK" in execution.stdout_preview

    report = build_analysis_report(
        task_id="task_sqlite_golden",
        user_goal="Summarize North revenue",
        status="success",
        executive_summary="North revenue was 200.5.",
        findings=[
            ReportFinding(
                finding_id="F001",
                claim="North revenue was 200.5.",
                supported_by=[evidence.evidence_id],
                referenced_asset_ids=[asset.asset_id],
                calculation_ref="sqlite_query:group_aggregate",
            )
        ],
    )
    validation = validate_analysis_report(
        report,
        evidence_records={
            evidence.evidence_id: {
                "validation_status": "verified",
                "truncated": evidence.truncated,
                "numeric_values": {"total_amount": 200.5},
                "evidence_text": "Verified SQLite aggregation.",
            }
        },
        asset_ids={asset.asset_id},
    )
    assert validation.is_valid is True
    assert "200.5" in render_analysis_report(report)


def test_sqlite_golden_truncation_report_warns(tmp_path: Path):
    db_path = create_golden_sqlite(tmp_path / "golden.sqlite")
    runtime = _runtime(tmp_path, "sqlite-truncated")
    source = SQLiteDataSource(
        SQLiteDatabase(SQLiteConnectionConfig(database_path=db_path))
    )
    tool = SQLiteQueryTool(
        source=source,
        runtime=runtime,
        source_identity=source.source_identity,
    )
    _, bounded, asset = tool.execute(
        sql="SELECT order_id, amount FROM orders ORDER BY order_id",
        max_rows=2,
        execution_id="exec_truncated",
    )
    evidence = collect_sqlite_query_evidence(
        execution_id="exec_truncated",
        result=bounded,
        output_asset=asset,
    )
    report = build_analysis_report(
        task_id="task_truncated",
        user_goal="Analyze truncated orders",
        status="partial_success",
        executive_summary="The query returned only the first two orders.",
        findings=[
            ReportFinding(
                finding_id="F001",
                claim="The bounded query returned 2 rows.",
                supported_by=[evidence.evidence_id],
                referenced_asset_ids=[asset.asset_id],
            )
        ],
        limitations=[],
    )
    validation = validate_analysis_report(
        report,
        evidence_records={
            evidence.evidence_id: {
                "validation_status": "verified",
                "truncated": True,
                "numeric_values": {"rows": 2},
                "evidence_text": "Verified bounded query.",
            }
        },
        asset_ids={asset.asset_id},
    )

    assert validation.status == "warning"
    assert any("truncated data" in warning for warning in validation.warnings)


def test_sqlite_golden_failure_does_not_create_asset_or_report(tmp_path: Path):
    db_path = create_golden_sqlite(tmp_path / "golden.sqlite")
    runtime = _runtime(tmp_path, "sqlite-failure")
    source = SQLiteDataSource(
        SQLiteDatabase(SQLiteConnectionConfig(database_path=db_path))
    )
    tool = SQLiteQueryTool(
        source=source,
        runtime=runtime,
        source_identity=source.source_identity,
    )

    with pytest.raises(DatabaseQueryValidationError):
        tool.execute(sql="DELETE FROM orders")

    assert runtime.assets == {}
    assert runtime.task.input_asset_ids == []

    # A failure report must not claim success or reference unknown evidence.
    from langgraph_langchain.runtime.analysis_report import (
        AnalysisReport,
        ReportFinding,
        validate_analysis_report,
    )

    report = AnalysisReport(
        task_id="task_failed",
        user_goal="Run SQL analysis",
        status="failed",
        executive_summary="The controlled SQL execution failed.",
        findings=[
            ReportFinding(
                finding_id="F001",
                claim="Execution failed as expected.",
                supported_by=["evid_failure"],
                referenced_asset_ids=[],
            )
        ],
    )
    validation = validate_analysis_report(
        report,
        evidence_records={},
        asset_ids=set(),
    )
    assert validation.status == "invalid"
    assert any("Unknown evidence reference" in error for error in validation.errors)


def test_missing_evidence_blocks_successful_gate_report(tmp_path: Path):
    from langgraph_langchain.runtime.analysis_report import (
        AnalysisReport,
        ReportFinding,
        validate_analysis_report,
    )

    report = AnalysisReport(
        task_id="task_gate",
        user_goal="Check evidence gate",
        status="success",
        executive_summary="Unsupported conclusion",
        findings=[
            ReportFinding(
                finding_id="F001",
                claim="Unsupported 123.",
                supported_by=["evid_missing"],
            )
        ],
    )
    validation = validate_analysis_report(
        report,
        evidence_records={},
        asset_ids=set(),
    )

    assert validation.status == "invalid"

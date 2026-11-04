from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from langgraph_langchain.data.assets import DataAsset
from langgraph_langchain.data.snapshots import SnapshotStore
from langgraph_langchain.execution.python_task_executor import PythonTaskExecutor
from langgraph_langchain.runtime.asset_resolver import (
    AssetResolutionError,
    AssetResolver,
    AssetUnsupportedError,
)
from langgraph_langchain.runtime.context import AnalysisRuntime
from langgraph_langchain.schemas import AnalysisTask


def _file_asset(
    path: Path,
    frame: pd.DataFrame,
    *,
    source_type: str,
    sheet_name: str | None = None,
) -> DataAsset:
    if source_type == "csv":
        frame.to_csv(path, index=False)
    elif source_type == "parquet":
        frame.to_parquet(path, index=False)
    elif source_type in {"xlsx", "xls"}:
        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            frame.to_excel(
                writer,
                sheet_name=sheet_name or "Sheet1",
                index=False,
            )
    return DataAsset.from_dataframe(
        dataframe=frame,
        source_path=path,
        source_type=source_type,
        sheet_name=sheet_name,
    ).model_copy(update={"asset_id": "file_asset"})


def _resolver(asset: DataAsset, tmp_path: Path) -> AssetResolver:
    return AssetResolver(
        assets={"file_asset": asset},
        snapshot_store=SnapshotStore(tmp_path / "snapshots"),
    )


def test_resolver_loads_csv_file_asset(tmp_path: Path):
    frame = pd.DataFrame(
        {
            "中文列": ["北京", "上海"],
            "日期": [
                pd.Timestamp("2026-01-01"),
                pd.Timestamp("2026-01-02"),
            ],
            "nullable": pd.array([1, pd.NA], dtype="Int64"),
        }
    )
    path = tmp_path / "data.csv"
    asset = _file_asset(path, frame, source_type="csv")
    resolver = _resolver(asset, tmp_path)

    resolved = resolver.resolve("file_asset")

    assert resolved.dataframe["中文列"].tolist() == ["北京", "上海"]
    assert resolved.dataframe["日期"].astype(str).tolist() == [
        "2026-01-01",
        "2026-01-02",
    ]
    nullable = resolved.dataframe["nullable"]
    assert nullable.notna().tolist() == [True, False]
    assert nullable.dropna().tolist() == [1.0]
    assert resolved.source_type == "csv"
    assert resolved.source_location == str(path)
    assert resolved.truncated is False
    assert resolved.provenance["selected_columns"] == [
        "中文列",
        "日期",
        "nullable",
    ]


def test_resolver_loads_excel_sheet(tmp_path: Path):
    frame = pd.DataFrame({"region": ["North", "South"], "value": [10, 20]})
    path = tmp_path / "data.xlsx"
    asset = _file_asset(path, frame, source_type="xlsx", sheet_name="Sales")
    resolver = _resolver(asset, tmp_path)

    resolved = resolver.resolve("file_asset")

    pd.testing.assert_frame_equal(resolved.dataframe, frame)
    assert resolved.sheet_name == "Sales"


def test_resolver_loads_parquet_file_asset(tmp_path: Path):
    frame = pd.DataFrame({"value": [1.5, None], "flag": [True, False]})
    path = tmp_path / "data.parquet"
    asset = _file_asset(path, frame, source_type="parquet")
    resolver = _resolver(asset, tmp_path)

    resolved = resolver.resolve("file_asset")

    pd.testing.assert_frame_equal(resolved.dataframe, frame)
    assert resolved.source_type == "parquet"


def test_missing_or_changed_file_asset_fails(tmp_path: Path):
    frame = pd.DataFrame({"value": [1]})
    path = tmp_path / "data.csv"
    asset = _file_asset(path, frame, source_type="csv")
    resolver = _resolver(asset, tmp_path)

    path.unlink()
    with pytest.raises(AssetResolutionError, match="no longer available"):
        resolver.resolve("file_asset")

    path.write_text("value\n999\n", encoding="utf-8")
    with pytest.raises(
        AssetResolutionError,
        match="changed after registration",
    ):
        resolver.resolve("file_asset")


def test_unsupported_file_format_is_rejected(tmp_path: Path):
    asset = DataAsset.from_dataframe(
        dataframe=pd.DataFrame({"value": [1]}),
        source_path=tmp_path / "data.json",
        source_type="json",
    ).model_copy(update={"asset_id": "file_asset"})
    resolver = _resolver(asset, tmp_path)

    with pytest.raises(
        AssetUnsupportedError,
        match="supported query-result asset",
    ):
        resolver.resolve("file_asset")


def test_runtime_builds_file_request_and_executor_uses_it(tmp_path: Path):
    frame = pd.DataFrame({"category": ["a", "b"], "amount": [10, 20]})
    path = tmp_path / "data.csv"
    frame.to_csv(path, index=False)
    runtime = AnalysisRuntime(
        workspace_dir=tmp_path,
        session_id="file-e2e",
        question="Analyze file asset",
    )
    asset = DataAsset.from_dataframe(
        dataframe=frame,
        source_path=path,
        source_type="csv",
    )
    runtime.assets[asset.asset_id] = asset
    runtime.task.input_asset_ids.append(asset.asset_id)
    task = AnalysisTask(
        session_id="file-e2e",
        question="Summarize file",
        input_asset_ids=[asset.asset_id],
    )

    request = runtime.build_execution_request(task)
    assert list(request.namespace["runtime_inputs"]) == [asset.asset_id]

    request.code = (
        "frame = runtime_inputs['" + asset.asset_id + "']\n"
        "assert frame['amount'].sum() == 30\n"
        "print('FILE_OK')"
    )
    execution = PythonTaskExecutor(workspace_dir=tmp_path).execute(request)

    assert execution.status == "succeeded"
    assert "FILE_OK" in execution.stdout_preview

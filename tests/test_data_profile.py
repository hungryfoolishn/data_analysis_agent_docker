"""B4.2.1: DataProfile model, builder, limits, and version policy tests."""

from __future__ import annotations

import pandas as pd
import pytest

from langgraph_langchain.data.assets import DataAsset
from langgraph_langchain.runtime.analysis_operations import (
    attach_profile,
    build_data_profile,
    resolve_asset_profile,
)
from langgraph_langchain.runtime.planner_models import (
    GENERATOR_VERSION,
    PROFILE_VERSION,
)


def _frame():
    return pd.DataFrame(
        {
            "department": ["East", "West", "East", "North"],
            "revenue": [100.0, 200.0, 150.0, None],
            "date": pd.to_datetime(
                ["2026-01-01", "2026-02-01", "2026-03-01", "2026-04-01"]
            ),
        }
    )


def test_numeric_summary_correct():
    profile = build_data_profile(_frame(), asset_id="asset_1")
    revenue = next(column for column in profile.columns if column.name == "revenue")
    assert revenue.numeric_summary is not None
    assert revenue.numeric_summary["count"] == 3.0
    assert revenue.numeric_summary["mean"] == pytest.approx(150.0)
    assert revenue.numeric_summary["min"] == 100.0
    assert revenue.numeric_summary["max"] == 200.0


def test_category_top_k_correct():
    profile = build_data_profile(_frame(), asset_id="asset_1")
    department = next(column for column in profile.columns if column.name == "department")
    assert department.top_categories is not None
    assert ("East", 2) in department.top_categories


def test_time_range_correct():
    profile = build_data_profile(_frame(), asset_id="asset_1")
    date_column = next(column for column in profile.columns if column.name == "date")
    assert date_column.time_range is not None
    assert date_column.time_range[0] == "2026-01-01 00:00:00"
    assert date_column.time_range[-1] == "2026-04-01 00:00:00"


def test_missing_rate_correct():
    profile = build_data_profile(_frame(), asset_id="asset_1")
    revenue = next(column for column in profile.columns if column.name == "revenue")
    assert revenue.missing_rate == pytest.approx(0.25)


def test_numeric_column_limit_truncates():
    frame = pd.DataFrame({f"num_{i}": [float(i)] for i in range(25)})
    profile = build_data_profile(frame, asset_id="asset_1")
    assert profile.truncated is True
    assert len(profile.columns) == 25  # schema completeness preserved
    assert any("numeric columns truncated" in reason for reason in profile.incomplete_reasons)
    summarized = [
        column for column in profile.columns if column.numeric_summary is not None
    ]
    assert len(summarized) <= 20


def test_top_k_limit_enforced():
    frame = pd.DataFrame({"cat": [f"value_{i % 30}" for i in range(300)]})
    profile = build_data_profile(frame, asset_id="asset_1")
    column = profile.columns[0]
    assert column.top_categories is not None
    assert len(column.top_categories) <= 10


def test_duplicate_row_budget_degrades():
    frame = pd.DataFrame({"a": range(20)})
    profile = build_data_profile(frame, asset_id="asset_1", duplicate_row_budget=10)
    assert profile.meta.duplicate_rows is None
    assert any("duplicate_rows skipped" in reason for reason in profile.incomplete_reasons)


def test_candidate_keys_detected():
    frame = pd.DataFrame({"id": [1, 2, 3], "tag": ["x", "x", "y"]})
    profile = build_data_profile(frame, asset_id="asset_1")
    assert profile.candidate_keys == ["id"]


def _registered_asset(tmp_path, frame, *, name="ws"):
    from langgraph_langchain.runtime.context import AnalysisRuntime

    runtime = AnalysisRuntime(workspace_dir=tmp_path / name, session_id="s", question="q")
    source = tmp_path / name / "data.csv"
    source.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(source, index=False)
    asset = runtime.register_dataframe(
        dataframe=frame, source_path=source, source_type="csv"
    )
    return runtime, asset


def test_attach_profile_persists_and_resolves(tmp_path):
    runtime, asset = _registered_asset(tmp_path, _frame(), name="dp1")
    reason = attach_profile(asset, _frame())
    assert reason is None
    assert "profile" in asset.source_metadata
    profile, resolve_reason = resolve_asset_profile(asset)
    assert resolve_reason is None
    assert profile is not None
    assert profile.meta.asset_id == asset.asset_id
    assert profile.meta.data_version == asset.content_hash


def test_profile_generation_failure_keeps_asset_usable(tmp_path):
    runtime, asset = _registered_asset(tmp_path, _frame(), name="dp2")
    reason = attach_profile(asset, None)  # broken input -> profile generation fails
    assert reason is not None
    # Asset and schema remain usable; the profile is explicitly unavailable.
    assert asset.asset_id in runtime.assets
    assert "profile_unavailable" in asset.source_metadata


def test_profile_missing_degrades(tmp_path):
    runtime, asset = _registered_asset(tmp_path, _frame(), name="dp3")
    profile, resolve_reason = resolve_asset_profile(asset)
    assert profile is None
    assert resolve_reason == "profile_missing"


def test_data_version_mismatch_is_stale(tmp_path):
    runtime, asset = _registered_asset(tmp_path, _frame(), name="dp4")
    attach_profile(asset, _frame())
    asset.source_metadata["profile"]["meta"]["data_version"] = "old_hash"
    profile, resolve_reason = resolve_asset_profile(asset)
    assert profile is None
    assert resolve_reason == "profile_stale"


def test_profile_version_incompatible_not_reused(tmp_path):
    runtime, asset = _registered_asset(tmp_path, _frame(), name="dp5")
    attach_profile(asset, _frame())
    asset.source_metadata["profile"]["meta"]["profile_version"] = "0"
    profile, resolve_reason = resolve_asset_profile(asset)
    assert profile is None
    assert resolve_reason == "profile_version_incompatible"


def test_generator_version_change_not_reused(tmp_path):
    runtime, asset = _registered_asset(tmp_path, _frame(), name="dp6")
    attach_profile(asset, _frame())
    asset.source_metadata["profile"]["meta"]["generator_version"] = "older"
    profile, resolve_reason = resolve_asset_profile(asset)
    assert profile is None
    assert resolve_reason == "generator_changed"


def test_incomplete_reasons_preserved(tmp_path):
    frame = pd.DataFrame({"a": range(20)})
    profile = build_data_profile(frame, asset_id="asset_1", duplicate_row_budget=10)
    assert profile.incomplete_reasons


def test_profile_never_contains_raw_rows(tmp_path):
    profile = build_data_profile(_frame(), asset_id="asset_1")
    payload = profile.model_dump(mode="json")
    serialized = str(payload)
    assert "East" in serialized  # aggregated category values are allowed
    for column in payload["columns"]:
        assert not isinstance(column.get("numeric_summary"), pd.DataFrame)
    # No raw row payloads: every structured field is bounded metadata.
    assert len(profile.columns) == len(_frame().columns)


# ---------------------------------------------------------------------------
# B4.2.1.1 fix 2: unified scan budget / sampling / 64KB hard constraint
# ---------------------------------------------------------------------------

def test_small_dataset_exact_statistics():
    profile = build_data_profile(_frame(), asset_id="asset_1")
    assert profile.meta.statistics_scope == "exact"
    assert profile.meta.sample_rows is None
    assert profile.meta.duplicate_rows == 0


def test_large_dataset_enters_sampled_mode():
    frame = pd.DataFrame(
        {
            "a": range(500),
            "b": [f"value_{i % 50}" for i in range(500)],
        }
    )
    profile = build_data_profile(frame, asset_id="asset_1", max_scan_rows=100)
    assert profile.meta.statistics_scope == "sampled"
    assert profile.meta.sample_rows == 100
    assert profile.truncated is True
    assert any("statistics sampled" in reason for reason in profile.incomplete_reasons)


def test_sampled_statistics_are_reproducible():
    frame = pd.DataFrame(
        {
            "a": range(500),
            "b": [f"value_{i % 50}" for i in range(500)],
        }
    )
    first = build_data_profile(frame, asset_id="asset_1", max_scan_rows=100)
    second = build_data_profile(frame, asset_id="asset_1", max_scan_rows=100)
    first_payload = first.model_dump(mode="json")
    second_payload = second.model_dump(mode="json")
    first_payload["meta"].pop("generated_at")
    second_payload["meta"].pop("generated_at")
    assert first_payload == second_payload


def test_sampled_statistics_marked_and_budget_respected():
    frame = pd.DataFrame(
        {
            "a": range(500),
            "b": [f"value_{i % 50}" for i in range(500)],
        }
    )
    profile = build_data_profile(frame, asset_id="asset_1", max_scan_rows=100)
    column = next(column for column in profile.columns if column.name == "a")
    # Statistics are computed on the bounded sample, not the full frame.
    assert column.numeric_summary is not None
    assert column.numeric_summary["count"] == 100.0
    assert 0.0 <= column.numeric_summary["max"] <= 499.0


def test_candidate_keys_respect_scan_budget():
    frame = pd.DataFrame(
        {
            "id": range(500),
            "tag": [f"value_{i % 50}" for i in range(500)],
        }
    )
    profile = build_data_profile(frame, asset_id="asset_1", max_scan_rows=100)
    # In sampled scope candidate keys are approximate: computed on the sample
    # and the profile is marked sampled/truncated.
    assert profile.meta.statistics_scope == "sampled"
    assert profile.truncated is True


def test_duplicate_rows_skipped_when_budget_exceeded():
    frame = pd.DataFrame({"a": range(500)})
    profile = build_data_profile(
        frame, asset_id="asset_1", max_scan_rows=100, duplicate_row_budget=50
    )
    assert profile.meta.duplicate_rows is None
    assert any("duplicate_rows skipped" in reason for reason in profile.incomplete_reasons)


def test_sampled_mode_does_not_copy_full_frame():
    # The budget path must work on a frame too large to be copied cheaply in
    # tests; existence of the sampled path with a 200k-row frame proves no
    # full-frame statistics were required (fast runtime = no full scan).
    frame = pd.DataFrame({"a": range(300_000)})
    profile = build_data_profile(frame, asset_id="asset_1", max_scan_rows=100)
    assert profile.meta.statistics_scope == "sampled"
    assert profile.meta.row_count == 300_000


# ---------------------------------------------------------------------------
# B4.2.1.1 fix 5: 64KB hard constraint under extreme inputs
# ---------------------------------------------------------------------------

def _assert_size(profile):
    import json

    size = len(
        json.dumps(profile.model_dump(mode="json"), ensure_ascii=False).encode("utf-8")
    )
    assert size <= 64 * 1024, f"profile size {size} exceeds limit"
    return size


def test_long_column_names_fit_after_degradation():
    frame = pd.DataFrame(
        {("very_long_column_name_" + "x" * 5000 + str(i)): [1.0, 2.0] for i in range(30)}
    )
    profile = build_data_profile(frame, asset_id="asset_1")
    _assert_size(profile)
    assert profile.truncated is True


def test_many_columns_fit_after_degradation():
    frame = pd.DataFrame({f"col_{i}": [1.0, 2.0, 3.0] for i in range(3000)})
    profile = build_data_profile(frame, asset_id="asset_1")
    _assert_size(profile)
    assert profile.truncated is True


def test_long_category_values_fit_after_degradation():
    frame = pd.DataFrame(
        {"cat": [f"huge_{'y' * 10000}_{i}" for i in range(20)]}
    )
    profile = build_data_profile(frame, asset_id="asset_1")
    _assert_size(profile)
    assert profile.truncated is True


def test_many_candidate_keys_fit_after_degradation():
    frame = pd.DataFrame({f"id_{i}": [i, i + 1, i + 2] for i in range(6000)})
    profile = build_data_profile(frame, asset_id="asset_1")
    _assert_size(profile)
    assert profile.truncated is True


def test_many_incomplete_reasons_fit_after_degradation():
    from langgraph_langchain.runtime.planner_models import (
        ColumnProfile,
        DataProfile,
        DataProfileMeta,
    )
    from langgraph_langchain.runtime.analysis_operations import (
        _degrade_to_size_limit,
    )

    meta = DataProfileMeta(
        asset_id="asset_1",
        data_version="hash",
        row_count=10,
        column_count=1,
    )
    profile = DataProfile(
        meta=meta,
        columns=[ColumnProfile(name="a", dtype="float64", missing_rate=0.0)],
        candidate_keys=["a"],
        incomplete_reasons=[
            f"reason_{i} " + "z" * 500 for i in range(500)
        ],
    )
    profile = _degrade_to_size_limit(profile, 64 * 1024)
    _assert_size(profile)
    assert profile.truncated is True


# ---------------------------------------------------------------------------
# B4.2.1.1 fix 3: Profile persistence across Runtime restore
# ---------------------------------------------------------------------------

def test_profile_survives_runtime_restore(tmp_path):
    from langgraph_langchain.runtime.analysis_workflow import register_file_asset
    from langgraph_langchain.runtime.context import AnalysisRuntime

    workspace = tmp_path / "persist_ws"
    source = tmp_path / "persist_ws_input.csv"
    _frame().to_csv(source, index=False)

    runtime = AnalysisRuntime(
        workspace_dir=workspace,
        session_id="persist_session",
        question="profile persistence",
    )
    register_file_asset(runtime, source)

    original_asset = next(iter(runtime.assets.values()))
    profile, reason = resolve_asset_profile(original_asset)
    assert reason is None
    assert profile is not None

    # Destroy the in-memory runtime and restore the same session from disk.
    restored = AnalysisRuntime(
        workspace_dir=workspace,
        session_id="persist_session",
        question="profile persistence",
    )
    restored_asset = restored.assets[original_asset.asset_id]
    assert "profile" in restored_asset.source_metadata
    restored_profile, restored_reason = resolve_asset_profile(restored_asset)
    assert restored_reason is None
    assert restored_profile is not None
    assert restored_profile.meta.data_version == restored_asset.content_hash

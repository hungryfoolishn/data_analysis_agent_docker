"""B9.8.2 Stage 4: full Macro Runtime E2E scenario matrix (task §6).

Every scenario executes through the production chain via harness.py — real
resolver, registry, templates, plan builders, MacroRuntimeIntegration,
Runtime V2 scheduler/runner/executor, the real ``run_analysis_operation``
tool, output binding adjudication, EvidenceRegistry and report verification.
Only external HTTP is deterministic-fixture.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from langgraph_langchain.runtime.context import AnalysisRuntime
from langgraph_langchain.runtime.macro.registry import resolve_indicator_asset_key

from tests.integration.macro.harness import build_macro_production_run


def _all_succeeded(run) -> bool:
    return all(task.status == "succeeded" for task in run.runtime.scheduler.tasks)


# ---------------------------------------------------------------------------
# Single-country scenarios
# ---------------------------------------------------------------------------


def test_us_gdp_single_country_success(tmp_path):
    run = build_macro_production_run(tmp_path, "分析美国近五年GDP增长趋势", name="e2e_us")
    assert run.outcome["status"] == "planned"
    assert run.outcome["template_id"] == "gdp_growth"
    assert _all_succeeded(run)
    assert run.verified_evidence()
    report, rendered = run.report
    assert report is not None and report.status == "success"
    # US fred_gdp asset is the single input lineage
    source_assets = [
        asset_id
        for asset_id, asset in run.runtime.assets.items()
        if (asset.source_metadata or {}).get("dataset_id") == "fred_gdp"
    ]
    assert len(source_assets) == 1
    assert source_assets[0] in run.outcome["plan"].input_asset_ids


def test_cn_gdp_single_country_success(tmp_path):
    run = build_macro_production_run(tmp_path, "中国近五年GDP增长趋势", name="e2e_cn")
    assert run.outcome["status"] == "planned"
    assert _all_succeeded(run)
    assert run.verified_evidence()
    report, _ = run.report
    assert report is not None
    source_assets = [
        asset_id
        for asset_id, asset in run.runtime.assets.items()
        if (asset.source_metadata or {}).get("dataset_id") == "nbs_gdp"
    ]
    assert len(source_assets) == 1


# ---------------------------------------------------------------------------
# Cross-country scenarios (final-result verification, not just plans)
# ---------------------------------------------------------------------------


def test_us_cn_cpi_comparison_final_results(tmp_path):
    run = build_macro_production_run(tmp_path, "对比中美CPI同比变化", name="e2e_g15")
    assert run.outcome["status"] == "planned"
    assert _all_succeeded(run)
    comparison = run.outcome["comparison"]
    assert comparison["comparable_concepts"] == ["cpi"]
    per_concept = comparison["per_concept"][0]

    # Two independent country assets with distinct IndicatorAssetKeys.
    country_assets = {}
    for asset_id, asset in run.runtime.assets.items():
        dataset_id = (asset.source_metadata or {}).get("dataset_id")
        if dataset_id in {"fred_cpi", "nbs_cpi"}:
            country_assets[dataset_id] = asset_id
    assert set(country_assets) == {"fred_cpi", "nbs_cpi"}
    catalog = run.integration.catalog
    us_ind = catalog.get("us_cpi_monthly")
    cn_ind = catalog.get("cn_cpi_yoy_annual")
    us_key = resolve_indicator_asset_key(us_ind)
    cn_key = resolve_indicator_asset_key(cn_ind)
    assert us_key != cn_key
    assert us_key.country == "us" and cn_key.country == "cn"

    # Aligned asset: real comparison result on the common period set.
    aligned_id = comparison["aligned_asset_id"]
    assert aligned_id
    resolved = run.runtime.asset_resolver.resolve(aligned_id)
    frame = resolved.dataframe
    period_col = "period" if "period" in frame.columns else frame.columns[0]
    aligned_periods = [str(v) for v in frame[period_col].tolist()]
    assert aligned_periods == per_concept["aligned_periods"]
    assert set(frame.columns) >= {"cpi_us", "cpi_cn"}
    # Values are yoy-rate magnitudes (both positive drift fixtures).
    for column in ("cpi_us", "cpi_cn"):
        assert frame[column].notna().all()
    cn_values = frame["cpi_cn"].tolist()
    assert all(95.0 < value < 105.0 for value in cn_values)  # published-rate range

    # Evidence traces both country source assets and the aligned asset.
    inputs = {
        asset_id
        for evidence in run.verified_evidence()
        for asset_id in evidence.input_asset_ids
    }
    assert country_assets["fred_cpi"] in inputs
    assert country_assets["nbs_cpi"] in inputs
    assert aligned_id in inputs

    # Verified report references every traceable source.
    report, _ = run.report
    assert report is not None and report.status == "success"
    referenced = {
        asset_id
        for finding in report.findings
        for asset_id in finding.referenced_asset_ids
    }
    assert {country_assets["fred_cpi"], country_assets["nbs_cpi"], aligned_id} <= referenced


def test_us_cn_gdp_comparison_blocked_but_executed(tmp_path):
    run = build_macro_production_run(tmp_path, "中美GDP增长对比", name="e2e_g16")
    assert run.outcome["status"] == "planned"
    assert _all_succeeded(run)
    comparison = run.outcome["comparison"]
    assert comparison["comparable_concepts"] == []
    assert comparison["aligned_asset_id"] is None
    per_concept = comparison["per_concept"][0]
    assert any("price basis mismatch" in item for item in per_concept["incompatibilities"])
    # Both countries' data still independently analyzed with verified evidence.
    assert run.verified_evidence()
    datasets = {
        (asset.source_metadata or {}).get("dataset_id")
        for asset in run.runtime.assets.values()
    }
    assert {"fred_gdp", "nbs_gdp"} <= datasets
    report, _ = run.report
    assert report is not None and report.status == "success"


# ---------------------------------------------------------------------------
# Multi-indicator / any-of template scenarios
# ---------------------------------------------------------------------------


def test_us_cpi_ppi_multi_indicator(tmp_path):
    run = build_macro_production_run(tmp_path, "分析美国CPI和PPI同比对比", name="e2e_cpi_ppi")
    assert run.outcome["status"] == "planned"
    assert run.outcome["template_id"] == "inflation"
    # comparison pipeline must NOT trigger (single country)
    assert run.outcome["comparison"] is None
    assert _all_succeeded(run)
    assert run.verified_evidence()
    report, _ = run.report
    assert report is not None


def test_employment_any_of_template(tmp_path):
    run = build_macro_production_run(tmp_path, "美国就业市场分析", name="e2e_emp")
    assert run.outcome["status"] == "planned"
    assert run.outcome["template_id"] == "employment"
    assert _all_succeeded(run)
    assert run.verified_evidence()
    report, _ = run.report
    assert report is not None


def test_trade_any_of_template(tmp_path):
    run = build_macro_production_run(tmp_path, "美国出口贸易近三年趋势", name="e2e_trade")
    assert run.outcome["status"] == "planned"
    assert run.outcome["template_id"] == "trade"
    assert _all_succeeded(run)
    report, _ = run.report
    assert report is not None


# ---------------------------------------------------------------------------
# NBS time-range consistency
# ---------------------------------------------------------------------------


def test_nbs_time_range_request_matches_result(tmp_path):
    run = build_macro_production_run(tmp_path, "中国近两年GDP增长趋势", name="e2e_nbs_range")
    assert run.outcome["status"] == "planned"
    assert _all_succeeded(run)
    cn_asset = next(
        asset
        for asset in run.runtime.assets.values()
        if (asset.source_metadata or {}).get("dataset_id") == "nbs_gdp"
    )
    frame = run.runtime.asset_resolver.resolve(cn_asset.asset_id).dataframe
    periods = {str(value)[:4] for value in frame["period"].tolist()}
    # request window: last two years → start calendar year inclusive
    from datetime import datetime, timezone

    current_year = datetime.now(timezone.utc).year
    allowed = {str(y) for y in range(current_year - 2, current_year + 2)}
    assert periods <= allowed, f"periods outside requested range: {periods}"
    assert periods  # non-empty result within range


# ---------------------------------------------------------------------------
# Snapshot restore keeps asset identity
# ---------------------------------------------------------------------------


def test_snapshot_restore_keeps_asset_identity(tmp_path):
    run = build_macro_production_run(tmp_path, "对比中美CPI同比变化", name="e2e_snap")
    assert _all_succeeded(run)
    asset_ids_before = set(run.runtime.assets)
    aligned_id = run.outcome["comparison"]["aligned_asset_id"]
    frame_before = run.runtime.asset_resolver.resolve(aligned_id).dataframe

    restored = AnalysisRuntime(
        workspace_dir=run.runtime.workspace_dir,
        session_id=run.runtime.session_id,
        question="对比中美CPI同比变化",
    )
    # Task §6 requirement: asset identity (ids + content) survives restore.
    assert set(restored.assets) == asset_ids_before
    frame_after = restored.asset_resolver.resolve(aligned_id).dataframe
    pd.testing.assert_frame_equal(frame_after, frame_before)
    assert len(restored.executions) == len(run.results)


# ---------------------------------------------------------------------------
# Multi-step scheduler progression
# ---------------------------------------------------------------------------


def test_multi_step_scheduler_progression(tmp_path):
    run = build_macro_production_run(tmp_path, "对比中美CPI同比变化", name="e2e_sched")
    steps = run.runtime.run.steps
    assert all(step.status == "succeeded" for step in steps)
    assert len(steps) >= 4
    # executions follow plan order (sequential scheduler)
    plan_step_ids = [step.step_id for step in steps]
    execution_steps = [result.step_id for result in run.results]
    assert execution_steps == plan_step_ids


# ---------------------------------------------------------------------------
# Binding coverage
# ---------------------------------------------------------------------------


def test_output_binding_registered_per_step(tmp_path):
    run = build_macro_production_run(tmp_path, "对比中美CPI同比变化", name="e2e_bind")
    bindings = run.runtime.output_bindings.list_all()
    assert bindings
    outputs_by_step = {
        result.step_id: [asset.asset_id for asset in result.output_assets]
        for result in run.results
        if result.output_assets
    }
    for binding in bindings:
        assert binding.asset_id in outputs_by_step.get(binding.step_id, [])
        assert binding.execution_id in {r.execution_id for r in run.results}
    # every data-producing step has its declared output bound
    assert set(outputs_by_step) <= {binding.step_id for binding in bindings}

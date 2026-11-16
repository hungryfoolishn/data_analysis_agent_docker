"""B9.8.2 Stage 2: cross-country same-concept comparison pipeline tests.

Covers task §4.2 ComparisonSpec, §4.3 dual-country asset resolution, §4.5
alignment correctness (frequency normalization, real-period alignment, CPI
base-period rule, nominal/real GDP rule, missing/duplicate periods) and §4.6
G15/G16 production-entry behavior.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from langgraph_langchain.data.acquisition import AcquisitionService
from langgraph_langchain.data.adapters.public import FREDAdapter, NBSAdapter
from langgraph_langchain.data.adapters.registry import AdapterRegistry
from langgraph_langchain.runtime.context import AnalysisRuntime
from langgraph_langchain.runtime.macro.comparison import (
    ComparisonSpec,
    CrossCountryComparator,
)
from langgraph_langchain.runtime.macro.intent import MacroRuntimeIntegration
from langgraph_langchain.runtime.macro.models import (
    MacroIndicator,
    MacroIndicatorCatalog,
)
from langgraph_langchain.runtime.macro.registry import resolve_indicator_asset_key
from langgraph_langchain.runtime.macro.templates import ResolvedIndicatorAsset

CATALOG_PATH = Path(__file__).parents[1] / "langgraph_langchain" / "runtime" / "macro" / "catalog.json"


# ---------------------------------------------------------------------------
# Synthetic legs in real adapter output formats
# ---------------------------------------------------------------------------


def _indicator(**overrides):
    payload = {
        "indicator_id": "us_cpi_monthly",
        "concept": "cpi",
        "country": "us",
        "source_id": "fred",
        "dataset_id": "fred_cpi",
        "unit": "index_1982_1984_100",
        "frequency": "monthly",
        "seasonal_adjustment": "sa",
        "price_basis": "index",
        "level_kind": "index",
        "base_period": "1982-1984",
        "supported_transforms": ["yoy", "mom", "index_change", "rolling_average"],
    }
    payload.update(overrides)
    return MacroIndicator(**payload)


def _fred_monthly_frame(years, base=100.0):
    rows = []
    value = base
    for year in years:
        for month in range(1, 13):
            value *= 1.002  # ~2.4% annual drift
            rows.append({
                "date": pd.Timestamp(f"{year}-{month:02d}-01"),
                "value": round(value, 3),
                "series_id": "CPIAUCSL",
            })
    return pd.DataFrame(rows)


def _nbs_annual_frame(years, rates):
    rows = []
    for year, rate in zip(years, rates):
        rows.append({"period": str(year), "value": rate, "indicator": "A01030H"})
    return pd.DataFrame(rows)


def _resolved_map(specs):
    """specs: list of (indicator, frame).  Returns resolved dict + resolver."""
    resolved = {}
    frames = {}
    for indicator, frame in specs:
        resolved[indicator.indicator_id] = ResolvedIndicatorAsset(
            indicator=indicator,
            asset_id=f"asset_{indicator.indicator_id}",
            observation_count=len(frame),
        )
        frames[f"asset_{indicator.indicator_id}"] = frame
    return resolved, (lambda asset_id: frames[asset_id])


# ---------------------------------------------------------------------------
# §4.2: ComparisonSpec
# ---------------------------------------------------------------------------


def test_comparison_spec_shape():
    spec = ComparisonSpec(
        comparison_type="cross_country",
        countries=("us", "cn"),
        concepts=("gdp",),
        alignment_policy="common_period",
    )
    assert spec.comparison_type == "cross_country"
    assert spec.countries == ("us", "cn")
    assert spec.concepts == ("gdp",)
    assert spec.alignment_policy == "common_period"
    assert spec.comparison_method == "yoy_rate"


# ---------------------------------------------------------------------------
# §4.3 + §4.6 G15: dual-country CPI comparison on common periods
# ---------------------------------------------------------------------------


def _cpi_legs():
    us = _indicator()
    cn = _indicator(
        indicator_id="cn_cpi_yoy_annual", concept="cpi", country="cn",
        source_id="nbs", dataset_id="nbs_cpi",
        unit="same_period_last_year_100", frequency="annual",
        seasonal_adjustment="unknown", price_basis="index",
        level_kind="growth_rate", base_period="same_period_last_year",
        supported_transforms=["rolling_average", "period_comparison"],
    )
    return us, cn


def test_g15_cpi_comparison_comparable_on_common_years():
    us, cn = _cpi_legs()
    resolved, resolver = _resolved_map([
        (us, _fred_monthly_frame([2023, 2024, 2025])),
        (cn, _nbs_annual_frame([2022, 2023, 2024, 2025], [102.0, 100.2, 100.3, 99.8])),
    ])
    comparator = CrossCountryComparator(resolved, resolver)
    resolution = comparator.resolve(ComparisonSpec(
        comparison_type="cross_country",
        countries=("us", "cn"),
        concepts=("cpi",),
    ))
    assert resolution.missing == []
    comparison = resolution.comparisons[0]
    assert comparison.comparable
    assert {leg.country for leg in comparison.legs} == {"us", "cn"}
    assert comparison.comparison_frequency == "annual"
    # US CPI index legs compare as yoy rates; CN published yoy passes through
    assert any("not re-differentiated" in d for d in comparison.disclosures)
    assert any("aggregated to annual" in d for d in comparison.disclosures)
    assert comparison.aligned_periods == ["2024", "2025"]
    assert set(comparison.rate_columns) == {"cpi_cn", "cpi_us"}
    frame = comparator.build_aligned_frame(resolution)
    assert list(frame.columns) == sorted({"cpi_cn", "cpi_us"})
    assert list(frame.index) == ["2024", "2025"]
    # US 2025 annual yoy ≈ mean of monthly yoy values (positive drift); CN 99.8
    assert frame.loc["2025", "cpi_cn"] == pytest.approx(99.8)
    assert frame.loc["2025", "cpi_us"] > 0


def test_cpi_base_period_difference_does_not_block_rate_comparison():
    """Different base periods block INDEX-level comparison, not yoy rates."""
    us, cn = _cpi_legs()
    assert us.base_period != cn.base_period
    resolved, resolver = _resolved_map([
        (us, _fred_monthly_frame([2022, 2023, 2024])),
        (cn, _nbs_annual_frame([2023, 2024], [102.0, 100.5])),
    ])
    resolution = CrossCountryComparator(resolved, resolver).resolve(ComparisonSpec(
        countries=("us", "cn"), concepts=("cpi",),
    ))
    assert resolution.comparisons[0].comparable


# ---------------------------------------------------------------------------
# §4.5: nominal/real GDP rule (G16)
# ---------------------------------------------------------------------------


def _gdp_legs():
    us = _indicator(
        indicator_id="us_gdp_real_quarterly", concept="gdp",
        source_id="fred", dataset_id="fred_gdp",
        unit="billions_of_chained_2017_dollars", frequency="quarterly",
        seasonal_adjustment="sa", price_basis="real", level_kind="level",
        base_period="2017",
        supported_transforms=["yoy", "qoq", "annualized"],
    )
    cn = _indicator(
        indicator_id="cn_gdp_annual", concept="gdp", country="cn",
        source_id="nbs", dataset_id="nbs_gdp",
        unit="100_million_cny", frequency="annual",
        seasonal_adjustment="unknown", price_basis="current",
        level_kind="level",
        supported_transforms=["index_change", "period_comparison"],
    )
    return us, cn


def _gdp_frames():
    rows = []
    value = 20000.0
    for year in [2022, 2023, 2024, 2025]:
        for quarter in range(1, 5):
            value *= 1.01
            rows.append({
                "date": pd.Timestamp(f"{year}-{(quarter * 3 - 2):02d}-01"),
                "value": round(value, 2),
                "series_id": "GDP",
            })
    us_frame = pd.DataFrame(rows)
    cn_frame = _nbs_annual_frame([2021, 2022, 2023, 2024, 2025],
                                 [1349084, 1204724, 1210207, 1260582, 1349083])
    return us_frame, cn_frame


def test_g16_real_vs_nominal_growth_blocked_with_reason():
    us, cn = _gdp_legs()
    us_frame, cn_frame = _gdp_frames()
    resolved, resolver = _resolved_map([(us, us_frame), (cn, cn_frame)])
    resolution = CrossCountryComparator(resolved, resolver).resolve(ComparisonSpec(
        countries=("us", "cn"), concepts=("gdp",),
    ))
    comparison = resolution.comparisons[0]
    assert not comparison.comparable
    assert any("price basis mismatch" in reason for reason in comparison.incompatibilities)
    assert any("deflator" in reason for reason in comparison.incompatibilities)
    assert resolution.comparable_concepts == []
    assert comparator_frame_is_empty(resolution, resolver)


def comparator_frame_is_empty(resolution, resolver):
    return CrossCountryComparator(*_rebuild(resolution, resolver)).build_aligned_frame(
        resolution
    ).empty


def _rebuild(resolution, resolver):
    legs = {}
    for comparison in resolution.comparisons:
        for leg in comparison.legs:
            legs[leg.indicator.indicator_id] = ResolvedIndicatorAsset(
                indicator=leg.indicator, asset_id=leg.asset_id,
                observation_count=leg.observation_count,
            )
    return legs, resolver


def test_annual_yoy_transform_supported():
    """Annual level series produce previous-year yoy (G16 per-country path)."""
    from langgraph_langchain.runtime.macro.transforms import apply_macro_transform

    frame = _nbs_annual_frame([2021, 2022, 2023], [100.0, 110.0, 121.0])
    result, _warnings = apply_macro_transform(
        frame, transform="yoy", frequency="annual",
    )
    values = result.set_index("period")["value"]
    assert values["2022"] == pytest.approx(10.0)
    assert values["2023"] == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# §4.3: missing country / missing data
# ---------------------------------------------------------------------------


def test_missing_country_leg_reported():
    us, cn = _cpi_legs()
    resolved, resolver = _resolved_map([
        (us, _fred_monthly_frame([2023, 2024, 2025])),
    ])
    resolution = CrossCountryComparator(resolved, resolver).resolve(ComparisonSpec(
        countries=("us", "cn"), concepts=("cpi",),
    ))
    assert resolution.missing == ["cpi:cn"]
    comparison = resolution.comparisons[0]
    assert not comparison.comparable
    assert any("incomplete country set" in reason for reason in comparison.incompatibilities)


def test_missing_all_legs_marks_resolution_blocked():
    resolved, resolver = _resolved_map([])
    resolution = CrossCountryComparator(resolved, resolver).resolve(ComparisonSpec(
        countries=("us", "cn"), concepts=("cpi", "gdp"),
    ))
    assert resolution.blocked
    assert sorted(resolution.missing) == ["cpi:cn", "cpi:us", "gdp:cn", "gdp:us"]


# ---------------------------------------------------------------------------
# §4.5: period alignment edge cases
# ---------------------------------------------------------------------------


def test_missing_periods_inner_join_disclosed():
    """CN series missing 2025 → common periods shrink to the overlap."""
    us, cn = _cpi_legs()
    resolved, resolver = _resolved_map([
        (us, _fred_monthly_frame([2022, 2023, 2024, 2025])),
        (cn, _nbs_annual_frame([2023, 2024], [102.0, 100.5])),  # no 2025
    ])
    resolution = CrossCountryComparator(resolved, resolver).resolve(ComparisonSpec(
        countries=("us", "cn"), concepts=("cpi",),
    ))
    comparison = resolution.comparisons[0]
    assert comparison.comparable
    # US monthly yoy starts 2023 (needs 2022); CN ends 2024 → overlap 2023-2024
    assert comparison.aligned_periods == ["2023", "2024"]
    assert any("inner join on real period keys" in d for d in comparison.disclosures)


def test_insufficient_overlap_blocks_with_disclosure():
    """A single common period cannot support comparison — blocked, disclosed."""
    us, cn = _cpi_legs()
    resolved, resolver = _resolved_map([
        (us, _fred_monthly_frame([2023, 2024, 2025])),   # yoy starts 2024
        (cn, _nbs_annual_frame([2024], [100.5])),         # single year
    ])
    resolution = CrossCountryComparator(resolved, resolver).resolve(ComparisonSpec(
        countries=("us", "cn"), concepts=("cpi",),
    ))
    comparison = resolution.comparisons[0]
    assert not comparison.comparable
    assert any("insufficient common periods" in reason
               for reason in comparison.incompatibilities)


def test_duplicate_periods_collapse_by_mean_with_disclosure():
    us, cn = _cpi_legs()
    us_frame = _fred_monthly_frame([2022, 2023, 2024])
    # inject a duplicated month (different payload ordering, same period)
    extra = us_frame.iloc[[5]].copy()
    extra.loc[extra.index[0], "value"] = us_frame.iloc[5]["value"] + 2.0
    us_frame = pd.concat([us_frame, extra], ignore_index=True)
    resolved, resolver = _resolved_map([
        (us, us_frame),
        (cn, _nbs_annual_frame([2023, 2024], [102.0, 100.5])),
    ])
    resolution = CrossCountryComparator(resolved, resolver).resolve(ComparisonSpec(
        countries=("us", "cn"), concepts=("cpi",),
    ))
    comparison = resolution.comparisons[0]
    assert comparison.comparable
    assert any("duplicate period(s) collapsed by mean" in d
               for d in comparison.disclosures)


# ---------------------------------------------------------------------------
# §4.6: production entry (MacroRuntimeIntegration.run) G15/G16
# ---------------------------------------------------------------------------


def _make_integration(tmp_path, *, name="cmp"):
    runtime = AnalysisRuntime(workspace_dir=tmp_path / name, session_id=name, question="macro cmp")
    fred = FREDAdapter(cache=None)

    def fred_transport(url, params):
        series = url.rsplit("/", 1)[-1]
        if series in {"GDP"}:
            rows = []
            value = 20000.0
            for year in [2022, 2023, 2024, 2025]:
                for quarter in range(1, 5):
                    value *= 1.01
                    rows.append({
                        "date": f"{year}-{(quarter * 3 - 2):02d}-01",
                        "value": f"{value:.2f}",
                        "series_id": "GDP",
                    })
            return {"observations": rows}
        rows = []
        value = 100.0
        for year in [2022, 2023, 2024, 2025]:
            for month in range(1, 13):
                value *= 1.002
                rows.append({
                    "date": f"{year}-{month:02d}-01",
                    "value": f"{value:.3f}",
                    "series_id": "CPIAUCSL",
                })
        return {"observations": rows}

    fred._transport = fred_transport
    fred._credentials = lambda: {"api_key": "test"}
    nbs = NBSAdapter(cache=None)
    nbs._transport = lambda u, p: {
        "returncode": 200,
        "ret": [{"datanodes": [
            {"data": {"code": "TEST", "sj": str(year), "data": 100 + idx}}
            for idx, year in enumerate([2021, 2022, 2023, 2024, 2025])
        ]}],
    }
    registry = AdapterRegistry()
    registry.register(fred)
    registry.register(nbs)
    acquisition = AcquisitionService(
        runtime=runtime, registry=registry, history_dir=tmp_path / name / ".data_acq"
    )
    catalog = MacroIndicatorCatalog.load(CATALOG_PATH)
    return runtime, MacroRuntimeIntegration(catalog=catalog, acquisition=acquisition)


def test_run_g15_cpi_comparison_full_pipeline(tmp_path):
    """G15 through the production entry: dual assets, aligned asset, plan."""
    runtime, integration = _make_integration(tmp_path, name="g15")
    outcome = integration.run("对比中美CPI同比变化")
    assert outcome["status"] == "planned"
    assert outcome["template_id"] == "cross_country_comparison"
    comparison = outcome["comparison"]
    assert comparison is not None
    assert comparison["comparable_concepts"] == ["cpi"]
    per_concept = comparison["per_concept"][0]
    assert sorted(per_concept["countries"]) == ["cn", "us"]
    assert per_concept["comparable"] is True
    assert per_concept["aligned_periods"]
    # aligned asset registered with dual-source lineage
    aligned_asset_id = comparison["aligned_asset_id"]
    assert aligned_asset_id and aligned_asset_id in runtime.assets
    metadata = runtime.assets[aligned_asset_id].source_metadata
    assert len(metadata["input_asset_ids"]) == 2
    assert metadata["alignment_method"] == "yoy_rate_common_period"
    assert set(metadata["comparable_concepts"]) == {"cpi"}
    assert metadata["disclosures"]
    # plan references both country assets and the aligned asset
    plan = outcome["plan"]
    assert len(plan.input_asset_ids) == 3
    operations = {step.operation for step in plan.steps}
    assert "correlation" in operations
    dq_steps = [step for step in plan.steps if step.operation == "data_quality"]
    assert len(dq_steps) == 2  # one per country leg


def test_run_g16_gdp_comparison_blocked_but_per_country_analyzed(tmp_path):
    """G16: real vs nominal blocked for direct comparison; per-country
    growth steps still planned and disclosed."""
    runtime, integration = _make_integration(tmp_path, name="g16")
    outcome = integration.run("中美GDP增长对比")
    assert outcome["status"] == "planned"
    assert outcome["template_id"] == "cross_country_comparison"
    comparison = outcome["comparison"]
    assert comparison is not None
    assert comparison["comparable_concepts"] == []
    assert comparison["aligned_asset_id"] is None
    per_concept = comparison["per_concept"][0]
    assert per_concept["comparable"] is False
    assert any("price basis mismatch" in item for item in per_concept["incompatibilities"])
    plan = outcome["plan"]
    operations = [step.operation for step in plan.steps]
    assert "correlation" not in operations  # blocked comparison never plans a cross step
    assert operations.count("macro_transform") == 2  # per-country yoy rate production
    assert operations.count("trend") == 2
    assert len(plan.input_asset_ids) == 2


def test_run_single_country_not_comparison(tmp_path):
    """Mentioning one country never triggers the comparison pipeline."""
    runtime, integration = _make_integration(tmp_path, name="g01c")
    outcome = integration.run("分析美国近五年GDP增长趋势")
    assert outcome["status"] == "planned"
    assert outcome["template_id"] == "gdp_growth"
    assert outcome["comparison"] is None


def test_run_two_countries_without_comparison_words(tmp_path):
    """Two countries + no comparison words → joint analysis, not comparison."""
    runtime, integration = _make_integration(tmp_path, name="g17c")
    outcome = integration.run("美国和中国的GDP情况")
    assert outcome["status"] == "planned"
    assert outcome["comparison"] is None


def test_aligned_asset_lineage_matches_source_keys(tmp_path):
    """Aligned asset lineage is traceable to per-country IndicatorAssetKeys."""
    runtime, integration = _make_integration(tmp_path, name="g15b")
    outcome = integration.run("对比中美CPI同比变化")
    comparison = outcome["comparison"]
    aligned_id = comparison["aligned_asset_id"]
    metadata = runtime.assets[aligned_id].source_metadata
    input_ids = set(metadata["input_asset_ids"])
    # each country's plan dq step consumes its own asset (no overwrite)
    plan = outcome["plan"]
    dq_inputs = [
        step.input_refs[0]
        for step in plan.steps
        if step.operation == "data_quality"
    ]
    assert set(dq_inputs) == input_ids
    assert len(set(dq_inputs)) == 2

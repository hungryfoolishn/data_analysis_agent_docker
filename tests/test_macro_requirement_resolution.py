"""B9.8.2 Stage 1: unified indicator asset identity resolution tests.

Covers plan §3.6 items 6/8/9/10/11/13 (registry-level coexistence cases are
in test_indicator_asset_registry.py):
- same concept, different unit / price basis (distinct identity, explicit
  comparability failure);
- snapshot roundtrip key stability;
- single-country / dual-country intent resolution;
- clarification when country is ambiguous;
- Template Requirement Resolver / Data Sufficiency consistency (shared
  resolution object, deterministic primary selection — D7 fix).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from langgraph_langchain.runtime.macro.models import MacroIndicator, MacroIndicatorCatalog
from langgraph_langchain.runtime.macro.registry import (
    IndicatorAssetKey,
    IndicatorAssetRegistry,
    resolve_indicator_asset_key,
)
from langgraph_langchain.runtime.macro.templates import (
    ResolvedIndicatorAsset,
    build_template_plan,
    check_data_sufficiency,
    get_template,
    resolve_template_requirements,
)

CATALOG_PATH = Path(__file__).parents[1] / "langgraph_langchain" / "runtime" / "macro" / "catalog.json"


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
    }
    payload.update(overrides)
    return MacroIndicator(**payload)


def _resolved(indicator, *, count=24, asset_id="asset_x"):
    return ResolvedIndicatorAsset(
        indicator=indicator, asset_id=asset_id, observation_count=count
    )


# ---------------------------------------------------------------------------
# §3.6-6: same concept, different unit / price basis
# ---------------------------------------------------------------------------


def test_same_concept_different_price_basis_distinct_keys():
    real = resolve_indicator_asset_key(_indicator(
        indicator_id="us_gdp_real_q", concept="gdp", dataset_id="fred_gdp",
        price_basis="real", unit="billions_of_chained_2017_dollars",
        frequency="quarterly", level_kind="level", base_period="2017",
    ))
    nominal = resolve_indicator_asset_key(_indicator(
        indicator_id="us_gdp_nominal_a", concept="gdp", dataset_id="bea_gdp",
        price_basis="current", unit="billions_of_current_dollars",
        frequency="annual",
    ))
    assert real != nominal
    # identity differs by identity fields, not by data attributes
    assert real.dataset_id != nominal.dataset_id


def test_price_basis_mismatch_detected_by_semantics():
    from langgraph_langchain.runtime.macro.models import MacroTimeSemantics

    real = MacroTimeSemantics(
        frequency="quarterly", seasonal_adjustment="sa",
        price_basis="real", base_period="2017",
    )
    nominal = MacroTimeSemantics(
        frequency="quarterly", seasonal_adjustment="sa",
        price_basis="current",
    )
    reasons = real.comparable_with(nominal)
    assert any("price basis" in reason for reason in reasons)


def test_same_concept_different_unit_coexist_in_registry():
    registry = IndicatorAssetRegistry()
    k1 = resolve_indicator_asset_key(_indicator(
        indicator_id="us_gdp_real_q", concept="gdp", dataset_id="fred_gdp",
        unit="billions_of_chained_2017_dollars", frequency="quarterly",
    ))
    k2 = resolve_indicator_asset_key(_indicator(
        indicator_id="us_gdp_cur_a", concept="gdp", dataset_id="bea_gdp",
        unit="billions_of_current_dollars", frequency="annual",
    ))
    registry.register(k1, "asset_real")
    registry.register(k2, "asset_cur")
    assert registry.resolve(k1).asset_id == "asset_real"
    assert registry.resolve(k2).asset_id == "asset_cur"


# ---------------------------------------------------------------------------
# §3.6-8: snapshot recovery key stability
# ---------------------------------------------------------------------------


def test_catalog_json_roundtrip_keeps_keys_stable():
    catalog = MacroIndicatorCatalog.load(CATALOG_PATH)
    keys_before = {
        iid: resolve_indicator_asset_key(ind)
        for iid, ind in catalog.indicators.items()
    }
    restored = MacroIndicatorCatalog.from_json(catalog.to_json())
    keys_after = {
        iid: resolve_indicator_asset_key(ind)
        for iid, ind in restored.indicators.items()
    }
    assert keys_before == keys_after
    for key in keys_after.values():
        assert hash(key) == hash(keys_before[key.indicator_id])


def test_key_dict_roundtrip_stable():
    key = resolve_indicator_asset_key(_indicator())
    restored = IndicatorAssetKey(**json.loads(json.dumps(key.to_dict())))
    assert restored == key
    assert hash(restored) == hash(key)


# ---------------------------------------------------------------------------
# §3.6-9/10/11: intent country handling
# ---------------------------------------------------------------------------


def _resolve_map(specs):
    out = {}
    for indicator_id, concept, country, dataset_id, source_id, frequency, count in specs:
        ind = _indicator(
            indicator_id=indicator_id, concept=concept, country=country,
            dataset_id=dataset_id, source_id=source_id, frequency=frequency,
        )
        out[indicator_id] = _resolved(ind, count=count)
    return out


def test_single_country_intent_resolution():
    template = get_template("inflation")
    resolved = _resolve_map([
        ("us_cpi_monthly", "cpi", "us", "fred_cpi", "fred", "monthly", 24),
    ])
    resolution = resolve_template_requirements(
        template, resolved, requested_countries=("us",)
    )
    assert resolution.requested_countries == ("us",)
    assert resolution.concept_countries["cpi"] == {"us": ("us_cpi_monthly",)}
    assert resolution.country_incomplete == ()
    assert resolution.sufficiency == "ready"


def test_dual_country_intent_resolution_records_both():
    template = get_template("inflation")
    resolved = _resolve_map([
        ("us_cpi_monthly", "cpi", "us", "fred_cpi", "fred", "monthly", 24),
        ("cn_cpi_yoy_annual", "cpi", "cn", "nbs_cpi", "nbs", "annual", 24),
    ])
    resolution = resolve_template_requirements(
        template, resolved, requested_countries=("us", "cn")
    )
    assert set(resolution.concept_countries["cpi"]) == {"us", "cn"}
    assert resolution.country_incomplete == ()
    assert resolution.sufficiency == "ready"
    # distinct keys per country — US/CN same concept never overwrite
    assert resolution.keys["us_cpi_monthly"].country == "us"
    assert resolution.keys["cn_cpi_yoy_annual"].country == "cn"
    assert resolution.keys["us_cpi_monthly"] != resolution.keys["cn_cpi_yoy_annual"]


def test_dual_country_intent_missing_country_flagged():
    template = get_template("monetary_policy")
    resolved = _resolve_map([
        ("us_fedfunds_monthly", "policy_rate", "us", "fred_fedfunds", "fred", "monthly", 24),
    ])
    resolution = resolve_template_requirements(
        template, resolved, requested_countries=("us", "cn")
    )
    assert resolution.country_incomplete == ("policy_rate:cn",)
    assert resolution.sufficiency == "partially_ready"
    sufficiency, missing = check_data_sufficiency(
        template, resolved, requested_countries=("us", "cn")
    )
    assert sufficiency == "partially_ready"
    assert any("policy_rate:cn" in item for item in missing)


def test_single_country_request_not_flagged_for_other_country():
    template = get_template("monetary_policy")
    resolved = _resolve_map([
        ("us_fedfunds_monthly", "policy_rate", "us", "fred_fedfunds", "fred", "monthly", 24),
    ])
    resolution = resolve_template_requirements(
        template, resolved, requested_countries=("us",)
    )
    assert resolution.country_incomplete == ()
    assert resolution.sufficiency == "ready"


# ---------------------------------------------------------------------------
# §3.6-11: ambiguous country → explicit clarification, never a silent pick
# ---------------------------------------------------------------------------


def _make_integration(tmp_path, *, name="reqres"):
    from langgraph_langchain.data.acquisition import AcquisitionService
    from langgraph_langchain.data.adapters.public import FREDAdapter, NBSAdapter
    from langgraph_langchain.data.adapters.registry import AdapterRegistry
    from langgraph_langchain.runtime.context import AnalysisRuntime
    from langgraph_langchain.runtime.macro.intent import MacroRuntimeIntegration

    runtime = AnalysisRuntime(workspace_dir=tmp_path / name, session_id=name, question="macro")
    fred = FREDAdapter(cache=None)
    fred._transport = lambda u, p: {
        "observations": [
            {"date": f"2025-{m:02d}-01", "value": f"{100 + m}.5", "series_id": "TEST"}
            for m in range(1, 13)
        ]
    }
    fred._credentials = lambda: {"api_key": "test"}
    nbs = NBSAdapter(cache=None)
    nbs._transport = lambda u, p: {
        "returncode": 200,
        "ret": [{"datanodes": [
            {"data": {"code": "TEST", "sj": "2024", "data": 1349084}},
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


def test_unspecified_country_needs_clarification(tmp_path):
    """Concept known but no country expressed → clarification, not a silent pick."""
    _, integration = _make_integration(tmp_path, name="amb_c")
    outcome = integration.run("分析近五年GDP增长趋势")
    assert outcome["status"] == "needs_clarification"
    assert "SEMANTIC_COUNTRY_UNCLEAR" in outcome["missing"]


# ---------------------------------------------------------------------------
# §3.6-13: requirement resolver / data sufficiency / plan builder consistency
# ---------------------------------------------------------------------------


def test_sufficiency_and_plan_share_resolution(tmp_path):
    from langgraph_langchain.runtime.general_planner import validate_general_plan

    template = get_template("inflation")
    resolved = _resolve_map([
        ("us_cpi_monthly", "cpi", "us", "fred_cpi", "fred", "monthly", 24),
        ("cn_cpi_yoy_annual", "cpi", "cn", "nbs_cpi", "nbs", "annual", 24),
    ])
    resolution = resolve_template_requirements(
        template, resolved, requested_countries=("us", "cn")
    )
    sufficiency, missing = check_data_sufficiency(
        template, resolved, requested_countries=("us", "cn")
    )
    assert sufficiency == resolution.sufficiency
    assert missing == list(resolution.missing)
    plan = build_template_plan(
        template, resolved, question="中美CPI对比",
        requested_countries=("us", "cn"),
    )
    errors = validate_general_plan(plan, asset_columns={
        item.asset_id: ["period", "value"] for item in resolved.values()
    })
    assert errors == []
    # data_quality step consumes the deterministic primary asset
    dq_step = plan.steps[0]
    primary_id = resolution.primary_indicator_id()
    assert dq_step.input_refs == [resolution.entries[primary_id].asset_id]


def test_primary_selection_is_deterministic_and_concept_aligned():
    template = get_template("inflation")
    # optional ppi + required cpi; insertion order deliberately ppi-first
    resolved = {
        "us_ppi_monthly": _resolved(_indicator(
            indicator_id="us_ppi_monthly", concept="ppi", dataset_id="fred_ppi",
        )),
        "us_cpi_monthly": _resolved(_indicator(
            indicator_id="us_cpi_monthly", concept="cpi", dataset_id="fred_cpi",
        )),
    }
    resolution = resolve_template_requirements(template, resolved)
    # required concept wins over insertion order / optional concepts
    assert resolution.primary_indicator_id() == "us_cpi_monthly"


def test_primary_selection_multiple_series_same_concept_sorted():
    template = get_template("gdp_growth")
    resolved = _resolve_map([
        ("us_gdp_current_annual", "gdp", "us", "bea_gdp", "bea", "annual", 20),
        ("us_gdp_real_quarterly", "gdp", "us", "fred_gdp", "fred", "quarterly", 20),
    ])
    resolution = resolve_template_requirements(template, resolved)
    assert resolution.primary_indicator_id() == "us_gdp_current_annual"  # sorted
    assert set(resolution.concept_index["gdp"]) == {
        "us_gdp_current_annual", "us_gdp_real_quarterly"
    }


def test_multi_indicator_query_and_filters():
    template = get_template("inflation")
    resolved = _resolve_map([
        ("us_cpi_monthly", "cpi", "us", "fred_cpi", "fred", "monthly", 24),
        ("us_ppi_monthly", "ppi", "us", "fred_ppi", "fred", "monthly", 24),
    ])
    resolution = resolve_template_requirements(template, resolved)
    # cpi resolved; ppi is optional and also indexed
    assert resolution.concept_index["cpi"] == ("us_cpi_monthly",)
    assert resolution.concept_index["ppi"] == ("us_ppi_monthly",)
    # registry concept query returns both, filters narrow correctly
    registry = IndicatorAssetRegistry()
    for indicator_id, item in resolved.items():
        registry.register(
            resolve_indicator_asset_key(item.indicator), item.asset_id
        )
    assert len(registry.resolve_by_concept("cpi")) == 1
    assert len(registry.resolve_by_concept("cpi", country="us")) == 1
    assert len(registry.resolve_by_concept("cpi", country="cn")) == 0

"""B9.1: Macro domain models and indicator catalog tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from langgraph_langchain.runtime.macro.models import (
    MacroIndicator,
    MacroIndicatorCatalog,
    MacroObservation,
    MacroPlan,
    MacroTimeSemantics,
    MacroTransformResult,
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
    }
    payload.update(overrides)
    return MacroIndicator(**payload)


def test_catalog_loads_from_shipped_json():
    catalog = MacroIndicatorCatalog.load(CATALOG_PATH)
    assert catalog.catalog_version == "b9.1.0"
    assert len(catalog.indicators) >= 12


def test_catalog_json_roundtrip():
    catalog = MacroIndicatorCatalog.load(CATALOG_PATH)
    restored = MacroIndicatorCatalog.from_json(catalog.to_json())
    assert restored.catalog_version == catalog.catalog_version
    assert set(restored.indicators) == set(catalog.indicators)


def test_single_indicator_catalog_is_valid():
    # dict keys guarantee indicator_id uniqueness; a single-entry catalog is valid.
    indicator = _indicator()
    catalog = MacroIndicatorCatalog(indicators={indicator.indicator_id: indicator})
    assert catalog.get(indicator.indicator_id) is indicator


def test_duplicate_concept_country_rejected():
    first = _indicator(indicator_id="us_cpi_monthly", concept="cpi", country="us")
    second = _indicator(indicator_id="us_cpi_alt", concept="cpi", country="us", dataset_id="fred_cpi")
    with pytest.raises(Exception):
        MacroIndicatorCatalog(indicators={"a": first, "b": second})


def test_indicator_fields_complete():
    indicator = _indicator()
    for field in (
        "indicator_id", "concept", "country", "source_id", "dataset_id",
        "unit", "frequency", "seasonal_adjustment", "price_basis",
        "level_kind", "revision_policy",
    ):
        assert getattr(indicator, field)


def test_indicator_requires_real_dataset_id():
    with pytest.raises(Exception):
        _indicator(dataset_id="")  # empty dataset_id is invalid


def test_resolve_by_concept_and_country():
    catalog = MacroIndicatorCatalog.load(CATALOG_PATH)
    us_cpi = catalog.resolve(concept="cpi", country="us")
    assert len(us_cpi) == 1
    assert us_cpi[0].indicator_id == "us_cpi_monthly"
    both = catalog.resolve(concept="gdp")
    assert {item.country for item in both} == {"us", "cn"}


def test_filter_by_country_and_source():
    catalog = MacroIndicatorCatalog.load(CATALOG_PATH)
    fred_us = catalog.filter(country="us", source_id="fred")
    assert fred_us
    assert all(item.source_id == "fred" for item in fred_us)


def test_unknown_indicator_fails():
    catalog = MacroIndicatorCatalog.load(CATALOG_PATH)
    with pytest.raises(KeyError):
        catalog.get("does_not_exist")


def test_catalog_version_present():
    catalog = MacroIndicatorCatalog.load(CATALOG_PATH)
    assert catalog.catalog_version


def test_semantics_comparability_rules():
    exact = MacroTimeSemantics(
        frequency="monthly", seasonal_adjustment="sa", price_basis="index", cumulative=False
    )
    same = MacroTimeSemantics(
        frequency="monthly", seasonal_adjustment="sa", price_basis="index", cumulative=False
    )
    different_freq = MacroTimeSemantics(
        frequency="quarterly", seasonal_adjustment="sa", price_basis="index"
    )
    different_basis = MacroTimeSemantics(
        frequency="monthly", seasonal_adjustment="sa", price_basis="current"
    )
    assert exact.comparable_with(same) == []
    reasons = exact.comparable_with(different_freq)
    assert any("frequency" in reason for reason in reasons)
    reasons = exact.comparable_with(different_basis)
    assert any("price basis" in reason for reason in reasons)


def test_observation_references_asset_without_value_store():
    observation = MacroObservation(
        indicator_id="us_cpi_monthly",
        country="us",
        asset_id="asset_123",
        data_version="hash_abc",
        observation_period="2026-03",
        unit="index_1982_1984_100",
    )
    # No raw value payload: observations reference platform assets only.
    assert not hasattr(observation, "value")
    assert observation.data_version == "hash_abc"


def test_transform_result_supports_multi_input_lineage():
    result = MacroTransformResult(
        transform="period_comparison",
        input_asset_ids=["asset_a", "asset_b"],
        input_data_versions=["hash_a", "hash_b"],
        indicator_id="us_cpi_monthly",
        parameters={"base_period": "2026-01"},
        warnings=[],
    )
    assert len(result.input_asset_ids) == 2
    assert len(result.input_data_versions) == 2


def test_macro_plan_readiness_states():
    plan = MacroPlan(template_id="cpi_inflation", question="q")
    assert plan.data_sufficiency == "blocked"
    plan.data_sufficiency = "partially_ready"
    plan.missing = ["ppi"]
    assert plan.missing == ["ppi"]

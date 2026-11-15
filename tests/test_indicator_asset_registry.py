"""B9.8.1: IndicatorAssetKey and Registry tests."""

from __future__ import annotations

import pytest

from langgraph_langchain.runtime.macro.models import MacroIndicator
from langgraph_langchain.runtime.macro.registry import (
    IndicatorAssetKey,
    IndicatorAssetRegistry,
    RegisteredIndicatorAsset,
)


def _indicator(**overrides):
    payload = {
        "indicator_id": "us_cpi_monthly",
        "concept": "cpi",
        "country": "us",
        "source_id": "fred",
        "dataset_id": "fred_cpi",
        "unit": "index",
        "frequency": "monthly",
        "seasonal_adjustment": "sa",
        "price_basis": "index",
        "level_kind": "level",
    }
    payload.update(overrides)
    return MacroIndicator(**payload)


def _key(**overrides):
    return IndicatorAssetKey.from_indicator(_indicator(**overrides))


def test_key_is_frozen_and_hashable():
    key = _key()
    assert hash(key) is not None
    with pytest.raises(Exception):
        key.country = "cn"


def test_key_from_indicator():
    key = _key()
    assert key.indicator_id == "us_cpi_monthly"
    assert key.concept == "cpi"
    assert key.country == "us"
    assert key.frequency == "monthly"
    assert key.source_id == "fred"


def test_same_indicator_same_key():
    assert _key() == _key()
    assert _key(indicator_id="us_cpi_monthly_alt") != _key()


def test_different_country_different_key():
    assert _key(country="us") != _key(country="cn")


def test_different_frequency_different_key():
    assert _key(frequency="monthly") != _key(frequency="quarterly")


def test_different_source_different_key():
    assert _key(source_id="fred") != _key(source_id="bls")


def test_register_and_resolve():
    registry = IndicatorAssetRegistry()
    key = _key()
    entry = registry.register(key, "asset_123", observation_count=10, data_version="hash1")
    assert entry.asset_id == "asset_123"
    resolved = registry.resolve(key)
    assert resolved.asset_id == "asset_123"
    assert resolved.observation_count == 10


def test_register_conflict_raises():
    registry = IndicatorAssetRegistry()
    registry.register(_key(), "asset_1")
    with pytest.raises(ValueError):
        registry.register(_key(), "asset_2")


def test_register_idempotent_same_asset():
    registry = IndicatorAssetRegistry()
    first = registry.register(_key(), "asset_1")
    second = registry.register(_key(), "asset_1")
    assert first is second


def test_us_gdp_and_cn_gdp_coexist():
    registry = IndicatorAssetRegistry()
    us_key = _key(indicator_id="us_gdp", concept="gdp", country="us", dataset_id="fred_gdp", source_id="fred")
    cn_key = _key(indicator_id="cn_gdp", concept="gdp", country="cn", dataset_id="nbs_gdp", source_id="nbs")
    registry.register(us_key, "asset_us")
    registry.register(cn_key, "asset_cn")
    assert registry.resolve(us_key).asset_id == "asset_us"
    assert registry.resolve(cn_key).asset_id == "asset_cn"
    assert len(registry) == 2


def test_us_cpi_and_cn_cpi_coexist():
    registry = IndicatorAssetRegistry()
    registry.register(_key(country="us"), "asset_us_cpi")
    registry.register(_key(country="cn", indicator_id="cn_cpi"), "asset_cn_cpi")
    assert len(registry) == 2
    us = registry.resolve_by_concept("cpi", country="us")
    cn = registry.resolve_by_concept("cpi", country="cn")
    assert len(us) == 1
    assert len(cn) == 1
    assert us[0].asset_id == "asset_us_cpi"
    assert cn[0].asset_id == "asset_cn_cpi"


def test_same_concept_different_frequency_coexist():
    registry = IndicatorAssetRegistry()
    monthly = _key(frequency="monthly")
    quarterly = _key(frequency="quarterly", indicator_id="us_cpi_q")
    registry.register(monthly, "asset_m")
    registry.register(quarterly, "asset_q")
    assert registry.resolve(monthly).asset_id == "asset_m"
    assert registry.resolve(quarterly).asset_id == "asset_q"


def test_same_concept_different_source_coexist():
    registry = IndicatorAssetRegistry()
    fred = _key(source_id="fred")
    bls = _key(source_id="bls", indicator_id="us_cpi_bls")
    registry.register(fred, "asset_fred")
    registry.register(bls, "asset_bls")
    assert registry.resolve(fred).asset_id == "asset_fred"
    assert registry.resolve(bls).asset_id == "asset_bls"


def test_resolve_by_concept_single_match():
    registry = IndicatorAssetRegistry()
    registry.register(_key(), "asset_1")
    unique = registry.resolve_unique_by_concept("cpi")
    assert unique.asset_id == "asset_1"


def test_resolve_by_concept_multiple_match_raises():
    registry = IndicatorAssetRegistry()
    registry.register(_key(), "asset_1")
    registry.register(_key(country="cn", indicator_id="cn_cpi"), "asset_2")
    with pytest.raises(ValueError, match="Ambiguous"):
        registry.resolve_unique_by_concept("cpi")


def test_resolve_by_concept_no_match_raises():
    registry = IndicatorAssetRegistry()
    with pytest.raises(KeyError):
        registry.resolve_unique_by_concept("nonexistent")


def test_resolve_by_indicator_id():
    registry = IndicatorAssetRegistry()
    registry.register(_key(), "asset_1")
    entry = registry.resolve_by_indicator_id("us_cpi_monthly")
    assert entry.asset_id == "asset_1"


def test_retry_run_isolated_registry():
    """New registry per run: no cross-run pollution."""
    run1 = IndicatorAssetRegistry()
    run2 = IndicatorAssetRegistry()
    run1.register(_key(), "asset_run1")
    assert len(run2) == 0  # run2 starts empty
    run2.register(_key(), "asset_run2")
    assert run1.resolve(_key()).asset_id == "asset_run1"
    assert run2.resolve(_key()).asset_id == "asset_run2"


def test_list_assets_sorted():
    registry = IndicatorAssetRegistry()
    registry.register(_key(country="us", indicator_id="us_cpi"), "asset_us")
    registry.register(_key(country="cn", indicator_id="cn_cpi"), "asset_cn")
    assets = registry.list_assets()
    assert len(assets) == 2
    assert assets[0].key.country == "cn"  # sorted by indicator_id
    assert assets[1].key.country == "us"


def test_key_serialization():
    key = _key()
    d = key.to_dict()
    assert d["indicator_id"] == "us_cpi_monthly"
    assert d["concept"] == "cpi"
    restored = IndicatorAssetKey(**d)
    assert restored == key

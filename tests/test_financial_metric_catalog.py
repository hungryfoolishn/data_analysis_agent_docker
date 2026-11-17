"""B10.4: typed Financial indicator catalog."""

from __future__ import annotations

from decimal import Decimal

import pytest

from langgraph_langchain.runtime.financial.catalog import (
    CALCULATED,
    INVALID,
    UNAVAILABLE,
    FinancialIndicatorDefinition,
    FinancialIndicatorError,
    FinancialIndicatorRegistry,
    compute_financial_indicator,
    financial_indicator_registry,
)
from langgraph_langchain.runtime.financial.models import (
    FinancialStatement,
    FinancialUnit,
)


def _definition(**overrides):
    fields = {
        "metric_id": "test_ratio",
        "display_name": "Test Ratio",
        "market_scope": "any",
        "metric_kind": "ratio",
        "statistic_kind": "percent_ratio",
        "unit": "percent",
        "numerator_metric": "gross_profit",
        "denominator_metric": "revenue",
        "dependencies": ("gross_profit", "revenue"),
    }
    fields.update(overrides)
    return FinancialIndicatorDefinition(**fields)


def test_registry_rejects_conflicting_identity():
    registry = FinancialIndicatorRegistry()
    first = _definition()
    registry.register(first)
    assert registry.register(first) is first
    with pytest.raises(FinancialIndicatorError, match="indicator conflict"):
        registry.register(_definition(unit="x"))


def test_market_specific_definition_does_not_collide_with_any():
    registry = FinancialIndicatorRegistry()
    us = _definition(metric_id="special_metric", market_scope="us")
    registry.register(us)
    registry.register(_definition(metric_id="special_metric", market_scope="any"))
    assert registry.get("special_metric", market_scope="us") is us
    assert registry.get("special_metric", market_scope="any").market_scope == "any"


def test_raw_metric_requires_concept_and_statement_type():
    with pytest.raises(FinancialIndicatorError, match="canonical_concept"):
        FinancialIndicatorRegistry().register(
            _definition(
                metric_id="bad_raw",
                metric_kind="raw",
                statistic_kind="absolute_amount",
                unit="CNY",
                numerator_metric=None,
                denominator_metric=None,
                dependencies=(),
            )
        )


def test_indicator_lookup_is_exact_not_fuzzy():
    with pytest.raises(FinancialIndicatorError, match="not found"):
        financial_indicator_registry.get("gross margin")


def test_raw_indicator_computes_from_canonical_value():
    result = compute_financial_indicator(
        financial_indicator_registry.get("revenue"),
        input_values={"value": Decimal("1234.5")},
        lineage={"asset_id": "asset_1"},
    )
    assert result.status == CALCULATED
    assert result.value == Decimal("1234.5")
    assert result.statistic_kind == "absolute_amount"
    assert result.lineage["asset_id"] == "asset_1"


def test_ratio_indicator_preserves_decimal_precision():
    result = compute_financial_indicator(
        financial_indicator_registry.get("gross_margin"),
        input_values={"gross_profit": Decimal("1"), "revenue": Decimal("3")},
    )
    assert result.status == CALCULATED
    assert result.value == Decimal("100") / Decimal("3")


def test_ratio_rejects_zero_denominator():
    result = compute_financial_indicator(
        financial_indicator_registry.get("gross_margin"),
        input_values={"gross_profit": Decimal("10"), "revenue": Decimal("0")},
    )
    assert result.status == INVALID
    assert result.reason == "denominator_zero"


def test_missing_ratio_input_is_unavailable():
    result = compute_financial_indicator(
        financial_indicator_registry.get("gross_margin"),
        input_values={"gross_profit": Decimal("10"), "revenue": None},
    )
    assert result.status == UNAVAILABLE
    assert result.reason == "ratio_input_missing"


def test_growth_requires_explicit_baseline():
    result = compute_financial_indicator(
        financial_indicator_registry.get("revenue_growth"),
        input_values={"current": Decimal("110"), "previous": Decimal("100")},
    )
    assert result.status == CALCULATED
    assert result.value == Decimal("10")
    missing = compute_financial_indicator(
        financial_indicator_registry.get("revenue_growth"),
        input_values={"current": Decimal("110"), "previous": None},
    )
    assert missing.status == UNAVAILABLE
    assert missing.reason == "growth_baseline_missing"


def test_catalog_contains_raw_ratio_and_growth_semantics():
    assert financial_indicator_registry.get("revenue").statistic_kind == "absolute_amount"
    assert financial_indicator_registry.get("eps").statistic_kind == "per_share"
    assert financial_indicator_registry.get("gross_margin").statistic_kind == "percent_ratio"
    assert financial_indicator_registry.get("revenue_growth").statistic_kind == "growth_rate"

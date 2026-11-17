from decimal import Decimal

import pytest
from pydantic import ValidationError

from langgraph_langchain.runtime.financial.models import (
    FinancialLineItem,
    FinancialStatement,
    FinancialUnit,
)
from langgraph_langchain.runtime.financial.normalization import (
    FinancialCurrencyRate,
    FinancialNormalizationError,
    normalize_financial_line_item,
    normalize_financial_statement,
)

FINANCIAL_NORMALIZATION_VERSION = "b10.2.3"


def _amount_unit(scale=Decimal("1"), currency="CNY", kind="absolute_amount"):
    if kind in {"absolute_amount", "per_share"}:
        return FinancialUnit(statistic_kind=kind, numeric_unit=currency, currency=currency, scale=scale)
    numeric_unit = {
        "percent_ratio": "percent",
        "growth_rate": "percent",
        "yoy_index": "percent",
        "times_ratio": "times",
    }.get(kind, "1")
    return FinancialUnit(statistic_kind=kind, numeric_unit=numeric_unit, scale=scale)


def _item(
    value,
    kind="absolute_amount",
    scale=Decimal("1"),
    concept="revenue",
    currency="CNY",
):
    return FinancialLineItem(
        canonical_concept=concept,
        value=value,
        statistic_kind=kind,
        comparison_basis="yoy" if kind == "growth_rate" else None,
        unit=_amount_unit(scale=scale, kind=kind, currency=currency),
    )


def _statement(kind="income", items=()):
    return FinancialStatement(
        entity_id="entity_1",
        statement_type=kind,
        period_type="duration" if kind != "balance" else "instant",
        period_start=None if kind == "balance" else "2024-01-01",
        period_end="2024-12-31" if kind != "balance" else "2024-12-31",
        fiscal_year=2024,
        fiscal_period="FY",
        reporting_basis="consolidated",
        currency="CNY",
        accession_number="ACC-001",
        filing_date="2025-03-01",
        line_items=tuple(items),
    )


def test_scales_cny_amounts_without_floating_point_loss():
    for scale, expected in [
        (Decimal("1"), Decimal("1000.5")),
        (Decimal("1000"), Decimal("1000500")),
        (Decimal("10000"), Decimal("10005000")),
        (Decimal("1000000"), Decimal("1000500000")),
        (Decimal("100000000"), Decimal("100050000000")),
    ]:
        result = normalize_financial_line_item(_item(Decimal("1000.5"), scale=scale))
        assert result.output_line_item.value == expected
        assert result.output_line_item.unit.scale == Decimal("1")
        if scale != Decimal("1"):
            assert "multiply_by_declared_source_scale" in result.lineage["conversion_method"]


def test_decimal_precision_negative_zero_and_missing_are_preserved():
    precise = normalize_financial_line_item(
        _item(Decimal("123.1234567890123456789"), scale=Decimal("1000"))
    )
    assert precise.output_line_item.value == Decimal("123123.4567890123456789")
    negative = normalize_financial_line_item(_item(Decimal("-10")))
    zero = normalize_financial_line_item(_item(Decimal("0")))
    missing = normalize_financial_line_item(_item(None))
    assert negative.output_line_item.value == Decimal("-10")
    assert zero.output_line_item.value == Decimal("0")
    assert missing.output_line_item.value is None


@pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("Infinity")])
def test_non_finite_values_are_rejected(value):
    with pytest.raises(ValidationError):
        _item(value)
    bypassed_item = FinancialLineItem.model_construct(
        canonical_concept="revenue",
        value=value,
        statistic_kind="absolute_amount",
        unit=_amount_unit(),
    )
    with pytest.raises(FinancialNormalizationError, match="finite"):
        normalize_financial_line_item(bypassed_item)


def test_unknown_scale_is_rejected_for_non_monetary_fact():
    item = FinancialLineItem(
        canonical_concept="gross_margin",
        value=Decimal("25"),
        statistic_kind="percent_ratio",
        unit=FinancialUnit(
            statistic_kind="percent_ratio",
            numeric_unit="percent",
            scale=Decimal("1000"),
        ),
    )
    with pytest.raises(FinancialNormalizationError, match="monetary"):
        normalize_financial_line_item(item)


def test_yoy_index_explicitly_becomes_growth_rate():
    result = normalize_financial_line_item(
        _item(Decimal("100.2"), kind="yoy_index", concept="revenue_growth"),
        semantic_targets={"revenue_growth": "growth_rate"},
    )
    assert result.output_line_item.value == Decimal("0.2")
    assert result.output_line_item.statistic_kind == "growth_rate"
    assert result.output_line_item.comparison_basis == "yoy"
    assert result.lineage["conversion_method"] == ["explicit_yoy_index_minus_base_100"]


def test_yoy_index_below_and_at_base_are_converted():
    result = normalize_financial_line_item(
        _item(Decimal("99.8"), kind="yoy_index", concept="revenue_growth"),
        semantic_targets={"revenue_growth": "growth_rate"},
    )
    assert result.output_line_item.value == Decimal("-0.2")
    result = normalize_financial_line_item(
        _item(Decimal("100"), kind="yoy_index", concept="revenue_growth"),
        semantic_targets={"revenue_growth": "growth_rate"},
    )
    assert result.output_line_item.value == Decimal("0")


@pytest.mark.parametrize(
    "kind,value",
    [("growth_rate", Decimal("2.5")), ("percent_ratio", Decimal("25")), ("times_ratio", Decimal("25"))],
)
def test_semantics_are_not_subtracted_or_reinterpreted(kind, value):
    result = normalize_financial_line_item(_item(value, kind=kind, concept="metric"))
    assert result.output_line_item.value == value
    assert result.output_line_item.statistic_kind == kind
    assert result.lineage["conversion_method"] == ["identity"]


def test_unknown_kind_is_not_guessed():
    item = FinancialLineItem(
        canonical_concept="mystery",
        value=Decimal("25"),
        statistic_kind="unknown",
        unit=FinancialUnit(statistic_kind="unknown", numeric_unit="1"),
    )
    result = normalize_financial_line_item(item)
    assert result.output_line_item.statistic_kind == "unknown"
    assert result.warnings[0].startswith("statistic_kind remains 'unknown'")
    with pytest.raises(FinancialNormalizationError, match="unknown statistic"):
        normalize_financial_line_item(item, semantic_targets={"mystery": "growth_rate"})


def test_different_currencies_are_preserved_without_auto_exchange():
    cny = normalize_financial_line_item(_item(Decimal("100")))
    usd = normalize_financial_line_item(_item(Decimal("100"), currency="USD"))
    assert cny.output_line_item.unit.currency == "CNY"
    assert usd.output_line_item.unit.currency == "USD"
    assert cny.output_line_item.unit.currency != usd.output_line_item.unit.currency
    with pytest.raises(FinancialNormalizationError, match="explicit"):
        normalize_financial_line_item(_item(Decimal("100")), target_currency="USD")


def test_explicit_currency_rate_is_applied_and_recorded():
    rate = FinancialCurrencyRate(
        source_currency="CNY",
        target_currency="USD",
        rate=Decimal("0.14"),
        rate_date="2024-12-31",
        rate_source="test-source",
        direction="source_to_target",
        rate_version="v1",
    )
    result = normalize_financial_line_item(
        _item(Decimal("100")), target_currency="USD", currency_rate=rate
    )
    assert result.output_line_item.value == Decimal("14.00")
    assert result.output_line_item.unit.currency == "USD"
    assert result.lineage["output_currency"] == "USD"
    assert "multiply_by_explicit_rate" in result.lineage["conversion_method"]


def test_statement_normalization_preserves_period_and_filing_lineage():
    statement = _statement(
        kind="income",
        items=[
            _item(Decimal("1000"), scale=Decimal("10000")),
            FinancialLineItem(
                canonical_concept="revenue_growth",
                value=Decimal("100.2"),
                statistic_kind="yoy_index",
                unit=FinancialUnit(statistic_kind="yoy_index", numeric_unit="percent"),
            ),
        ],
    )
    result = normalize_financial_statement(
        statement,
        semantic_targets={"revenue_growth": "growth_rate"},
    )
    assert result.input_statement == statement
    assert result.output_statement.period_type == "duration"
    assert result.output_statement.period_start == "2024-01-01"
    assert result.output_statement.period_end == "2024-12-31"
    assert result.output_statement.filing_date == "2025-03-01"
    assert result.output_statement.accession_number == "ACC-001"
    assert result.output_statement.line_items[0].value == Decimal("10000000")
    assert result.output_statement.line_items[1].value == Decimal("0.2")
    assert result.lineage["filing"]["accession_number"] == "ACC-001"
    assert result.lineage["filing"]["statement_version"] == "ACC-001"


def test_instant_and_duration_periods_remain_explicit():
    instant = _statement(kind="balance", items=[_item(Decimal("1"))])
    duration = _statement(kind="cash", items=[_item(Decimal("1"))])
    instant_result = normalize_financial_statement(instant)
    duration_result = normalize_financial_statement(duration)
    assert instant_result.output_statement.period_type == "instant"
    assert instant_result.output_statement.period_start is None
    assert duration_result.output_statement.period_type == "duration"
    assert duration_result.output_statement.period_start == "2024-01-01"
    with pytest.raises(ValidationError):
        FinancialStatement(
            entity_id="entity_1",
            statement_type="income",
            period_type="duration",
            period_start=None,
            period_end="2024-12-31",
            fiscal_year=2024,
            fiscal_period="FY",
            reporting_basis="consolidated",
            currency="CNY",
        )


def test_normalization_is_immutable_deterministic_and_local_versioned():
    item = _item(Decimal("1000"), scale=Decimal("1000"))
    before = item.model_copy(deep=True)
    first = normalize_financial_line_item(item)
    second = normalize_financial_line_item(item)
    assert item == before
    assert first == second
    assert first.lineage["normalization_version"] == FINANCIAL_NORMALIZATION_VERSION
    assert FINANCIAL_NORMALIZATION_VERSION.startswith("b10.2")


def test_statement_rejects_currency_mismatch_without_fx():
    statement = _statement(
        kind="income",
        items=[_item(Decimal("100"), currency="USD")],
    )
    with pytest.raises(FinancialNormalizationError, match="statement currency"):
        normalize_financial_statement(statement)


def test_statement_fx_updates_statement_and_line_item_currency():
    rate = FinancialCurrencyRate(
        source_currency="CNY",
        target_currency="USD",
        rate=Decimal("0.14"),
        rate_date="2024-12-31",
        rate_source="test-source",
        direction="source_to_target",
        rate_version="v1",
    )
    statement = _statement(kind="income", items=[_item(Decimal("100"))])
    result = normalize_financial_statement(
        statement,
        target_currency="USD",
        currency_rate=rate,
    )
    assert result.output_statement.currency == "USD"
    assert result.output_statement.line_items[0].unit.currency == "USD"
    assert result.output_statement.line_items[0].value == Decimal("14.00")


def test_currency_rate_requires_traceability_fields():
    with pytest.raises(FinancialNormalizationError, match="rate_date"):
        FinancialCurrencyRate(
            source_currency="CNY",
            target_currency="USD",
            rate=Decimal("0.14"),
            rate_date="",
            rate_source="test-source",
            direction="source_to_target",
            rate_version="v1",
        )
    with pytest.raises(FinancialNormalizationError, match="rate_source"):
        FinancialCurrencyRate(
            source_currency="CNY",
            target_currency="USD",
            rate=Decimal("0.14"),
            rate_date="2024-12-31",
            rate_source="",
            direction="source_to_target",
            rate_version="v1",
        )
    with pytest.raises(FinancialNormalizationError, match="rate_version"):
        FinancialCurrencyRate(
            source_currency="CNY",
            target_currency="USD",
            rate=Decimal("0.14"),
            rate_date="2024-12-31",
            rate_source="test-source",
            direction="source_to_target",
            rate_version="",
        )


def test_yoy_conversion_records_explicit_base():
    item = FinancialLineItem(
        canonical_concept="revenue_growth",
        value=Decimal("102.5"),
        statistic_kind="yoy_index",
        unit=FinancialUnit(statistic_kind="yoy_index", numeric_unit="percent"),
    )
    result = normalize_financial_line_item(
        item,
        semantic_targets={"revenue_growth": "growth_rate"},
    )
    assert result.lineage["yoy_index_base"] == Decimal("100")

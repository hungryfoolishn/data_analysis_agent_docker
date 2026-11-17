"""B10.2.4: legacy financial model compatibility adapters."""

from __future__ import annotations

from decimal import Decimal

import pytest

from langgraph_langchain.runtime.financial.compat import (
    FINANCIAL_NORMALIZATION_VERSION,
    LegacyCompatibilityError,
    canonical_statement_to_legacy,
    fact_key_from_canonical,
    legacy_statement_to_canonical,
)
from langgraph_langchain.runtime.financial.models import (
    Entity,
    FinancialLineItem,
    FinancialStatement,
    FinancialUnit,
    IncomeStatement,
)


def _legacy_income(**overrides):
    fields = {
        "company_id": "company_acme",
        "period": "2024",
        "currency": "CNY",
        "revenue": 1000.5,
        "net_profit": 120.0,
        "eps": 2.05,
        "source_id": "src_1",
    }
    fields.update(overrides)
    return IncomeStatement(**fields)


def _canonical_entity():
    return Entity(
        entity_id="company_acme",
        legal_name="Acme Co",
        market="cn",
        exchange="sse",
        ticker="600000",
        currency_context="CNY",
    )


def _amount_unit(currency="CNY"):
    return FinancialUnit(
        statistic_kind="absolute_amount",
        numeric_unit=currency,
        currency=currency,
    )


def _duration_statement(**overrides):
    fields = {
        "entity_id": "company_acme",
        "statement_type": "income",
        "period_type": "duration",
        "period_start": "2024-01-01",
        "period_end": "2024-12-31",
        "fiscal_year": 2024,
        "fiscal_period": "FY",
        "reporting_basis": "consolidated",
        "currency": "CNY",
    }
    fields.update(overrides)
    return FinancialStatement(**fields)


def test_legacy_income_to_canonical():
    entity = _canonical_entity()
    stmt = legacy_statement_to_canonical(
        _legacy_income(),
        entity=entity,
        fiscal_year=2024,
        fiscal_period="FY",
        reporting_basis="consolidated",
        currency_override="CNY",
    )
    assert stmt.statement_type == "income"
    assert stmt.period_type == "duration"
    assert stmt.period_start == "2024-01-01"
    assert stmt.period_end == "2024-12-31"
    values = {item.canonical_concept: item for item in stmt.line_items}
    assert values["revenue"].value == Decimal("1000.5")
    assert values["net_profit"].statistic_kind == "absolute_amount"
    assert values["eps"].statistic_kind == "per_share"
    assert values["eps"].unit.unit_denominator == "share"
    assert "gross_profit" in stmt.metadata["missing_fields"]
    assert all(item.value is not None for item in stmt.line_items)


def test_legacy_conversion_requires_explicit_identity():
    with pytest.raises(LegacyCompatibilityError, match="reporting_basis"):
        legacy_statement_to_canonical(
            _legacy_income(),
            entity=_canonical_entity(),
            fiscal_year=2024,
            fiscal_period="FY",
        )
    with pytest.raises(LegacyCompatibilityError, match="fiscal_year"):
        legacy_statement_to_canonical(
            _legacy_income(),
            entity=_canonical_entity(),
            fiscal_year=None,
            fiscal_period="FY",
            reporting_basis="consolidated",
        )


def test_legacy_default_currency_surfaces_warning():
    stmt = legacy_statement_to_canonical(
        _legacy_income(),
        entity=_canonical_entity(),
        fiscal_year=2024,
        fiscal_period="FY",
        reporting_basis="consolidated",
    )
    assert stmt.currency == "CNY"
    warnings = " ".join(stmt.metadata["warnings"])
    assert "default 'CNY'" in warnings and "confirm" in warnings


def test_legacy_period_mismatch_flagged():
    stmt = legacy_statement_to_canonical(
        _legacy_income(period="2024"),
        entity=_canonical_entity(),
        fiscal_year=2025,
        fiscal_period="FY",
        reporting_basis="consolidated",
    )
    assert any("consistent" in warning for warning in stmt.metadata["warnings"])


def test_legacy_unsupported_statement_type_rejected():
    with pytest.raises(LegacyCompatibilityError, match="Unsupported legacy"):
        legacy_statement_to_canonical(
            object(),
            entity=_canonical_entity(),
            fiscal_year=2024,
            fiscal_period="FY",
            reporting_basis="consolidated",
        )


def test_fact_key_from_canonical_preserves_identity():
    canonical = _duration_statement(accession_number="ACC-001")
    item = FinancialLineItem(
        canonical_concept="revenue",
        value=Decimal("1000"),
        statistic_kind="absolute_amount",
        unit=_amount_unit("CNY"),
    )
    key = fact_key_from_canonical(canonical, item, market="cn")
    assert key.market == "cn"
    assert key.entity_id == canonical.entity_id
    assert key.canonical_concept == item.canonical_concept
    assert key.currency == canonical.currency
    assert key.statement_type == canonical.statement_type


def _canonical_income_for_legacy():
    return legacy_statement_to_canonical(
        _legacy_income(),
        entity=_canonical_entity(),
        fiscal_year=2024,
        fiscal_period="FY",
        reporting_basis="consolidated",
        currency_override="CNY",
    )


def test_canonical_to_legacy_lossless_roundtrip():
    original = _legacy_income()
    canonical = _canonical_income_for_legacy()
    restored = canonical_statement_to_legacy(canonical)
    assert isinstance(restored, IncomeStatement)
    assert restored.company_id == original.company_id
    assert restored.period == original.period
    assert restored.revenue == original.revenue
    assert restored.eps == original.eps


def test_canonical_to_legacy_rejects_imprecise_decimal():
    canonical = _canonical_income_for_legacy()
    imprecise = FinancialLineItem(
        canonical_concept="revenue",
        value=Decimal("1000.1234567890123456789"),
        statistic_kind="absolute_amount",
        unit=_amount_unit("CNY"),
        period_start=canonical.period_start,
        period_end=canonical.period_end,
    )
    rebuilt = canonical.model_copy(
        update={
            "line_items": tuple(
                imprecise if item.canonical_concept == "revenue" else item
                for item in canonical.line_items
            )
        }
    )
    with pytest.raises(LegacyCompatibilityError, match="exactly representable"):
        canonical_statement_to_legacy(rebuilt)


def test_canonical_to_legacy_rejects_filing_metadata_loss():
    canonical = _duration_statement(
        accession_number="ACC-001",
        filing_date="2025-03-01",
    )
    with pytest.raises(LegacyCompatibilityError, match="filing metadata"):
        canonical_statement_to_legacy(canonical)


def test_canonical_to_legacy_rejects_amendment():
    canonical = _canonical_income_for_legacy()
    amended = canonical.model_copy(update={"amendment": True})
    with pytest.raises(LegacyCompatibilityError, match="amended"):
        canonical_statement_to_legacy(amended)


def test_normalization_version_is_financial_local():
    from langgraph_langchain.runtime.macro.statistics import STATISTICS_VERSION

    assert FINANCIAL_NORMALIZATION_VERSION != STATISTICS_VERSION
    assert FINANCIAL_NORMALIZATION_VERSION.startswith("b10.2")

"""B10.2.2: canonical financial model tests (task §6.6).

Covers Entity identity, instant/duration statements, Decimal authority,
unit-semantics layering (percent ≠ times, USD/share ≠ CNY/share), filing/
accession/versioning, the identity registry, and the legacy↔canonical
compatibility adapters (lossless-only canonical → legacy).
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
from pydantic import ValidationError

from langgraph_langchain.runtime.financial.models import (
    BalanceSheetStatement,
    Entity,
    FinancialLineItem,
    FinancialStatement,
    FinancialUnit,
)
from langgraph_langchain.runtime.financial.registry import (
    FinancialFactEntry,
    FinancialIdentityError,
    FinancialStatementKey,
    FinancialStatementRegistry,
)

# ---------------------------------------------------------------------------
# Entity identity
# ---------------------------------------------------------------------------


def _entity(**overrides):
    fields = {
        "legal_name": "Acme Holdings Inc",
        "market": "us",
        "exchange": "nasdaq",
        "ticker": "ACME",
        "currency_context": "USD",
    }
    fields.update(overrides)
    return Entity(**fields)


def test_entity_id_deterministic_and_stable():
    first = _entity()
    second = _entity()
    assert first.entity_id == second.entity_id
    assert first.entity_id.startswith("ent_")


def test_same_ticker_different_market_different_entity():
    us = _entity()
    cn = _entity(market="cn", exchange="sse", currency_context="CNY")
    assert us.entity_id != cn.entity_id


def test_entity_without_any_identity_dimension_rejected():
    with pytest.raises(ValidationError, match="legal_name"):
        Entity(market="us", legal_name="", currency_context="USD")


def test_entity_unknown_market_rejected():
    with pytest.raises(ValidationError, match="market"):
        _entity(market="jp")


def test_entity_explicit_external_identifiers_preserved():
    entity = _entity(identifiers={"sec_cik": "0000320193"})
    assert entity.identifiers["sec_cik"] == "0000320193"


def test_entity_currency_context_must_be_iso():
    with pytest.raises(ValidationError, match="ISO 4217"):
        _entity(currency_context="RMB!")


# ---------------------------------------------------------------------------
# Statements: instant / duration
# ---------------------------------------------------------------------------


def _duration_statement(**overrides):
    fields = {
        "entity_id": "ent_x",
        "statement_type": "income",
        "period_type": "duration",
        "period_start": "2024-01-01",
        "period_end": "2024-12-31",
        "fiscal_year": 2024,
        "fiscal_period": "FY",
        "reporting_basis": "consolidated",
        "currency": "USD",
    }
    fields.update(overrides)
    return FinancialStatement(**fields)


def test_duration_statement_requires_period_start():
    assert _duration_statement().period_start == "2024-01-01"
    with pytest.raises(ValidationError, match="period_start"):
        _duration_statement(period_start=None)


def test_instant_statement_rejects_period_start():
    balance = _duration_statement(
        statement_type="balance",
        period_type="instant",
        period_start=None,
        period_end="2024-12-31",
    )
    assert balance.period_start is None
    with pytest.raises(ValidationError, match="instant"):
        _duration_statement(
            statement_type="balance",
            period_type="instant",
            period_start="2024-01-01",
        )


def test_income_statement_cannot_be_instant():
    with pytest.raises(ValidationError, match="duration"):
        _duration_statement(period_type="instant", period_start=None)


def test_unknown_reporting_basis_rejected():
    with pytest.raises(ValidationError):
        _duration_statement(reporting_basis="group_and_parent")


def test_statement_unknown_fiscal_period_rejected():
    with pytest.raises(ValidationError):
        _duration_statement(fiscal_period="5M")


# ---------------------------------------------------------------------------
# Line items: Decimal authority and unit-semantics layering
# ---------------------------------------------------------------------------


def _amount_unit(currency="CNY"):
    return FinancialUnit(
        statistic_kind="absolute_amount", numeric_unit=currency, currency=currency
    )


def test_decimal_authority_precision():
    item = FinancialLineItem(
        canonical_concept="revenue",
        value=Decimal("0.1") + Decimal("0.2"),
        statistic_kind="absolute_amount",
        unit=_amount_unit(),
    )
    assert item.value == Decimal("0.3")  # never 0.30000000000000004


def test_missing_value_is_none_never_zero():
    item = FinancialLineItem(
        canonical_concept="revenue", statistic_kind="absolute_amount", unit=_amount_unit()
    )
    assert item.value is None  # absence is not fabricated to 0


def test_canonical_concept_snake_case_enforced():
    with pytest.raises(ValidationError, match="snake_case"):
        FinancialLineItem(
            canonical_concept="Total Revenue",
            statistic_kind="absolute_amount",
            unit=_amount_unit(),
        )


def test_percent_ratio_and_times_ratio_distinct():
    pct = FinancialUnit(statistic_kind="percent_ratio", numeric_unit="percent")
    times = FinancialUnit(statistic_kind="times_ratio", numeric_unit="times")
    assert pct.statistic_kind != times.statistic_kind
    with pytest.raises(ValidationError, match="times"):
        FinancialUnit(statistic_kind="percent_ratio", numeric_unit="times")
    with pytest.raises(ValidationError, match="percent"):
        FinancialUnit(statistic_kind="times_ratio", numeric_unit="percent")
    # 25% and 25× are different quantities with different kinds
    gross_margin = FinancialLineItem(
        canonical_concept="gross_margin", value=Decimal("25"),
        statistic_kind="percent_ratio", unit=pct,
    )
    current_ratio = FinancialLineItem(
        canonical_concept="current_ratio", value=Decimal("25"),
        statistic_kind="times_ratio", unit=times,
    )
    assert gross_margin.statistic_kind != current_ratio.statistic_kind


def test_per_share_requires_currency_and_share_denominator():
    usd_share = FinancialUnit(
        statistic_kind="per_share", numeric_unit="USD",
        currency="USD", unit_denominator="share",
    )
    item = FinancialLineItem(
        canonical_concept="eps", value=Decimal("2.05"),
        statistic_kind="per_share", unit=usd_share,
    )
    assert item.unit.currency == "USD"
    assert item.unit.unit_denominator == "share"
    # CNY/share is a different unit from USD/share — currency is explicit
    cny_share = FinancialUnit(
        statistic_kind="per_share", numeric_unit="CNY",
        currency="CNY", unit_denominator="share",
    )
    assert cny_share.currency != usd_share.currency
    with pytest.raises(ValidationError, match="per_share"):
        FinancialUnit(statistic_kind="per_share", numeric_unit="USD")
    with pytest.raises(ValidationError, match="per_share"):
        FinancialUnit(
            statistic_kind="per_share", numeric_unit="USD",
            currency="USD", unit_denominator="ton",
        )


def test_currency_only_valid_for_monetary_kinds():
    with pytest.raises(ValidationError, match="currency applies only"):
        FinancialUnit(statistic_kind="percent_ratio", numeric_unit="percent", currency="CNY")


def test_growth_rate_requires_comparison_basis():
    with pytest.raises(ValidationError, match="comparison_basis"):
        FinancialLineItem(
            canonical_concept="revenue_growth",
            value=Decimal("2.5"),
            statistic_kind="growth_rate",
            unit=FinancialUnit(statistic_kind="growth_rate", numeric_unit="percent"),
        )
    item = FinancialLineItem(
        canonical_concept="revenue_growth",
        value=Decimal("2.5"),
        statistic_kind="growth_rate",
        comparison_basis="yoy",
        unit=FinancialUnit(statistic_kind="growth_rate", numeric_unit="percent"),
    )
    assert item.comparison_basis == "yoy"
    # ratio items must NOT carry a comparison basis (percent_ratio ≠ growth_rate)
    with pytest.raises(ValidationError, match="only valid for growth_rate"):
        FinancialLineItem(
            canonical_concept="gross_margin",
            value=Decimal("25"),
            statistic_kind="percent_ratio",
            comparison_basis="yoy",
            unit=FinancialUnit(statistic_kind="percent_ratio", numeric_unit="percent"),
        )


# ---------------------------------------------------------------------------
# Filing / accession / version semantics
# ---------------------------------------------------------------------------


def test_filing_metadata_separate_from_report_period():
    stmt = _duration_statement(
        filing_date="2025-03-01",
        accession_number="0000320193-25-000008",
        amendment=False,
    )
    # report period ≠ filing date
    assert stmt.period_end == "2024-12-31"
    assert stmt.filing_date == "2025-03-01"
    assert stmt.accession_number != stmt.period_end


def test_version_tracks_filing_identity():
    v1 = _duration_statement(accession_number="ACC-001")
    v2 = _duration_statement(accession_number="ACC-002", amendment=True)
    assert v1.version == "ACC-001"
    assert v2.version == "ACC-002"
    assert v2.amendment and not v1.amendment


# ---------------------------------------------------------------------------
# Identity registry: idempotent / conflict / parallel versions
# ---------------------------------------------------------------------------


def _key(**overrides):
    fields = {
        "market": "us",
        "entity_id": "ent_x",
        "canonical_concept": "revenue",
        "statement_type": "income",
        "period_type": "duration",
        "fiscal_year": 2024,
        "fiscal_period": "FY",
        "reporting_basis": "consolidated",
        "currency": "USD",
    }
    fields.update(overrides)
    return FinancialStatementKey(**fields)


def _entry(key=None, accession="ACC-001", content="hash-1", **overrides):
    fields = {
        "key": key or _key(),
        "accession_number": accession,
        "content_version": content,
        "asset_id": f"asset_{accession}_{content}",
    }
    fields.update(overrides)
    return FinancialFactEntry(**fields)


def test_identity_key_roundtrip_serialization():
    key = _key()
    restored = FinancialStatementKey.from_dict(json.loads(json.dumps(key.to_dict())))
    assert restored == key
    assert hash(restored) == hash(key)


def test_identity_key_rejects_mismatched_period_type():
    with pytest.raises(FinancialIdentityError, match="instant"):
        _key(statement_type="balance")  # balance must be instant


def test_identity_key_requires_explicit_market_and_basis():
    with pytest.raises(FinancialIdentityError, match="market"):
        _key(market="jp")
    with pytest.raises(FinancialIdentityError, match="reporting_basis"):
        _key(reporting_basis="default")


def test_registry_idempotent_same_source_and_content():
    registry = FinancialStatementRegistry()
    first = registry.register(_entry())
    second = registry.register(_entry())
    assert first is second
    assert len(registry) == 1


def test_registry_conflict_same_source_different_content():
    registry = FinancialStatementRegistry()
    registry.register(_entry())
    with pytest.raises(FinancialIdentityError, match="conflict"):
        registry.register(_entry(content="hash-2"))


def test_registry_parallel_restatement_versions_retained():
    registry = FinancialStatementRegistry()
    original = registry.register(_entry(accession="ACC-001"))
    restated = registry.register(_entry(accession="ACC-002", amendment=True))
    key = original.key
    assert registry.resolve_latest(key) is restated
    versions = registry.resolve_versions(key)
    assert [item.accession_number for item in versions] == ["ACC-001", "ACC-002"]
    # original version still resolvable — no overwrite
    assert versions[0].content_version == "hash-1"


def test_registry_requires_source_identity():
    registry = FinancialStatementRegistry()
    with pytest.raises(FinancialIdentityError, match="accession"):
        registry.register(_entry(accession=""))


# ---------------------------------------------------------------------------
# JSON / serialization compatibility
# ---------------------------------------------------------------------------


def test_statement_json_roundtrip_preserves_decimal_precision():
    stmt = _duration_statement(
        line_items=(
            FinancialLineItem(
                canonical_concept="revenue",
                value=Decimal("1234567890.123456789"),
                statistic_kind="absolute_amount",
                unit=_amount_unit("USD"),
            ),
        )
    )
    restored = FinancialStatement.model_validate_json(stmt.model_dump_json())
    assert restored.line_items[0].value == Decimal("1234567890.123456789")
    # JSON text carries the exact decimal as a string (no float artifacts)
    assert "1234567890.123456789" in stmt.model_dump_json()




def test_statement_rejects_non_iso_and_invalid_dates():
    with pytest.raises(ValidationError, match="ISO date"):
        _duration_statement(period_start="not-a-date")
    with pytest.raises(ValidationError, match="ISO date"):
        _duration_statement(period_end="2024-12-32")
    with pytest.raises(ValidationError, match="period_start"):
        _duration_statement(period_start="2025-01-01")


def test_statement_line_item_periods_must_match_when_present():
    item = FinancialLineItem(
        canonical_concept="revenue",
        value=Decimal("100"),
        statistic_kind="absolute_amount",
        unit=_amount_unit("USD"),
    )
    statement = _duration_statement(line_items=(item,))
    assert statement.line_items[0].period_start is None
    mismatched = item.model_copy(
        update={"period_start": "2023-01-01", "period_end": "2023-12-31"}
    )
    with pytest.raises(ValidationError, match="period"):
        _duration_statement(line_items=(mismatched,))


def test_line_item_kind_must_match_unit_kind():
    with pytest.raises(ValidationError, match="statistic_kind"):
        FinancialLineItem(
            canonical_concept="revenue",
            value=Decimal("100"),
            statistic_kind="absolute_amount",
            unit=FinancialUnit(
                statistic_kind="percent_ratio", numeric_unit="percent"
            ),
        )


def test_registry_requires_valid_key_and_asset_id():
    registry = FinancialStatementRegistry()
    invalid_key_entry = FinancialFactEntry(
        key=None,
        accession_number="ACC-001",
        content_version="hash-1",
        asset_id="asset_1",
    )
    with pytest.raises(FinancialIdentityError, match="key"):
        registry.register(invalid_key_entry)
    with pytest.raises(FinancialIdentityError, match="asset_id"):
        registry.register(_entry(asset_id=""))


def test_registry_source_version_includes_source_document_id():
    registry = FinancialStatementRegistry()
    first = registry.register(_entry(source_document_id="DOC-1"))
    second = registry.register(_entry(source_document_id="DOC-2"))
    assert first.source_version != second.source_version
    assert len(registry.resolve_versions(first.key)) == 2

"""Legacy ↔ canonical financial model compatibility adapters (B10.2.4).

Direction and authority rules (B10.2 task §6.5/§8):

    Legacy fixed-field models (V9) → compat adapter → canonical models →
    normalization (B10.2.3).  The legacy path NEVER bypasses the canonical
    layer, and the two never share authority: the canonical side is the only
    authority for normalized data.

- legacy → canonical: field mapping through an explicit whitelist
  (concept → statistic kind); nothing is guessed from unit strings.  Facts
  that cannot be mapped safely surface as warnings; missing identity
  dimensions are required explicitly (market / fiscal year / period /
  reporting basis have no legacy fields and are NOT defaulted — defaulting
  reporting_basis to consolidated or currency to a "confirmed" CNY would
  fabricate semantics).
- canonical → legacy: supported ONLY when lossless.  A Decimal value is
  lossless as float only if ``Decimal(str(float(value))) == value``;
  otherwise the conversion is explicitly rejected instead of silently
  rounding.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

from langgraph_langchain.runtime.financial.models import (
    Entity,
    FinancialLineItem,
    FinancialStatement,
    FinancialStatisticKind,
    FinancialUnit,
)
from langgraph_langchain.runtime.financial.normalization import (
    FINANCIAL_NORMALIZATION_VERSION,
)
from langgraph_langchain.runtime.financial.period import PeriodNormalizer
from langgraph_langchain.runtime.financial.registry import (
    FinancialIdentityError,
    FinancialStatementKey,
)

__all__ = [
    "FINANCIAL_NORMALIZATION_VERSION",
    "LegacyCompatibilityError",
    "legacy_statement_to_canonical",
    "canonical_statement_to_legacy",
    "LEGACY_CONCEPT_KINDS",
]

_LEGACY_DEFAULT_CURRENCY = "CNY"


class LegacyCompatibilityError(ValueError):
    """Raised when a legacy conversion cannot be performed faithfully."""


# Explicit whitelist: legacy fixed field → canonical concept → statistic kind.
# eps is the only non-absolute field in the legacy fixed models.
LEGACY_CONCEPT_KINDS: dict[str, FinancialStatisticKind] = {
    "revenue": "absolute_amount",
    "cost_of_revenue": "absolute_amount",
    "gross_profit": "absolute_amount",
    "operating_profit": "absolute_amount",
    "net_profit": "absolute_amount",
    "net_profit_attributable": "absolute_amount",
    "eps": "per_share",
    "total_assets": "absolute_amount",
    "total_liabilities": "absolute_amount",
    "total_equity": "absolute_amount",
    "cash": "absolute_amount",
    "accounts_receivable": "absolute_amount",
    "inventory": "absolute_amount",
    "fixed_assets": "absolute_amount",
    "short_term_debt": "absolute_amount",
    "long_term_debt": "absolute_amount",
    "current_assets": "absolute_amount",
    "current_liabilities": "absolute_amount",
    "operating_cash_flow": "absolute_amount",
    "investing_cash_flow": "absolute_amount",
    "financing_cash_flow": "absolute_amount",
    "capital_expenditure": "absolute_amount",
    "free_cash_flow": "absolute_amount",
}

_STATEMENT_CONCEPTS = {
    "income": (
        "revenue", "cost_of_revenue", "gross_profit", "operating_profit",
        "net_profit", "net_profit_attributable", "eps",
    ),
    "balance": (
        "total_assets", "total_liabilities", "total_equity", "cash",
        "accounts_receivable", "inventory", "fixed_assets", "short_term_debt",
        "long_term_debt", "current_assets", "current_liabilities",
    ),
    "cash": (
        "operating_cash_flow", "investing_cash_flow", "financing_cash_flow",
        "capital_expenditure", "free_cash_flow",
    ),
}

_LEGACY_STATEMENT_TYPES = {"income": "income", "balance": "balance", "cash": "cash"}


def _decimal_from_legacy(value: float) -> Decimal:
    # str() round-trip avoids binary-float artifacts: Decimal(str(0.1)) == 0.1.
    return Decimal(str(value))


def legacy_statement_to_canonical(
    statement,
    *,
    entity: Entity,
    fiscal_year: int,
    fiscal_period: str,
    reporting_basis: Optional[str] = None,
    period_start: Optional[str] = None,
    period_end: Optional[str] = None,
    currency_override: Optional[str] = None,
    statistic_kind_overrides: Optional[dict[str, str]] = None,
    filing_date: Optional[str] = None,
    accession_number: Optional[str] = None,
    source_document_id: Optional[str] = None,
    amendment: bool = False,
) -> FinancialStatement:
    """Convert one legacy fixed-field statement to the canonical layer.

    Explicitly required (no legacy source, never defaulted): entity, market
    (via ``entity.market``), fiscal_year, fiscal_period, reporting_basis —
    each is rejected with ``LegacyCompatibilityError`` when missing instead
    of being silently defaulted (defaulting reporting_basis to consolidated
    or currency to a "confirmed" CNY would fabricate semantics).
    period_start/end are derived from the legacy ``period`` string via the
    existing ``PeriodNormalizer`` (natural-calendar assumption — disclosed;
    B10.3 refines non-natural fiscal years) unless explicitly provided.
    """
    if not reporting_basis:
        raise LegacyCompatibilityError(
            "reporting_basis is required and must be explicit (consolidated "
            "or parent); legacy models carry no such field and defaulting "
            "would fabricate semantics"
        )
    if not fiscal_year or not fiscal_period:
        raise LegacyCompatibilityError(
            "fiscal_year and fiscal_period are required explicitly; the "
            "legacy period string alone is not a canonical period identity"
        )
    statement_type = statement.__class__.__name__
    legacy_kind = {
        "IncomeStatement": "income",
        "BalanceSheetStatement": "balance",
        "CashFlowStatement": "cash",
    }.get(statement_type)
    if legacy_kind is None:
        raise LegacyCompatibilityError(
            f"Unsupported legacy statement type: {statement_type}"
        )
    warnings: list[str] = []

    overrides = dict(statistic_kind_overrides or {})
    if entity.market not in {"us", "cn"}:  # pragma: no cover - Entity validates
        raise LegacyCompatibilityError(f"unsupported market: {entity.market!r}")

    # Currency: explicit override wins; the legacy value is used as declared,
    # with a warning when it equals the legacy default (it may be an
    # unconfirmed default, not a confirmed reporting currency).
    if currency_override is not None:
        currency = currency_override
        warnings.append("currency taken from explicit currency_override")
    else:
        currency = statement.currency
        if currency == _LEGACY_DEFAULT_CURRENCY:
            warnings.append(
                "legacy currency equals the legacy default 'CNY'; confirm it "
                "is a declared reporting currency before cross-currency use"
            )

    # Period derivation through the existing parser (natural calendar).
    period_normalizer = PeriodNormalizer()
    try:
        parsed = period_normalizer.parse(statement.period)
    except Exception as exc:  # PeriodNormalizationError and value errors
        raise LegacyCompatibilityError(
            f"legacy period {statement.period!r} cannot be parsed: {exc}"
        ) from exc
    resolved_end = period_end or parsed.end_date.isoformat()
    if statement.period.upper() not in {str(fiscal_year), f"{fiscal_year}{fiscal_period}"}:
        warnings.append(
            f"declared fiscal_year/fiscal_period ({fiscal_year}/{fiscal_period}) "
            f"must be consistent with legacy period {statement.period!r}"
        )

    if legacy_kind == "balance":
        period_type = "instant"
        resolved_start = None
    else:
        period_type = "duration"
        resolved_start = period_start or parsed.start_date.isoformat()
        if period_start is None:
            warnings.append(
                "period_start derived from the legacy period string via "
                "PeriodNormalizer (natural-calendar assumption; B10.3 "
                "refines non-natural fiscal years)"
            )

    expected_concepts = _STATEMENT_CONCEPTS[legacy_kind]
    missing: list[str] = []
    items: list[FinancialLineItem] = []
    raw_fields = {
        field_name: getattr(statement, field_name)
        for field_name in expected_concepts
        if hasattr(statement, field_name)
    }
    for concept in expected_concepts:
        raw_value = raw_fields.get(concept)
        if raw_value is None:
            missing.append(concept)
            continue
        kind = overrides.get(concept) or LEGACY_CONCEPT_KINDS.get(concept)
        if kind is None:
            warnings.append(
                f"legacy field {concept!r} has no whitelisted statistic kind; "
                "item skipped (would be 'unknown')"
            )
            continue
        if kind not in {"absolute_amount", "per_share"}:
            warnings.append(
                f"override statistic kind {kind!r} for {concept!r} is not "
                "supported for legacy fixed fields (only "
                "absolute_amount/per_share)"
            )
            continue
        unit = FinancialUnit(
            statistic_kind=kind,
            numeric_unit=currency if kind == "absolute_amount" else currency,
            currency=currency,
            unit_denominator="share" if kind == "per_share" else None,
        )
        items.append(
            FinancialLineItem(
                canonical_concept=concept,
                original_concept=concept,
                value=_decimal_from_legacy(raw_value),
                statistic_kind=kind,
                unit=unit,
                period_start=resolved_start,
                period_end=resolved_end,
                source_metadata={
                    "legacy_model": statement_type,
                    "legacy_period": statement.period,
                    "source_id": statement.source_id,
                },
                normalization_metadata={
                    "conversion_method": "legacy_fixed_field_whitelist",
                    "normalization_version": FINANCIAL_NORMALIZATION_VERSION,
                    "warnings": list(warnings[-1:]),
                },
            )
        )
    if missing:
        warnings.append(f"legacy fields missing/None, no item created: {sorted(missing)}")

    try:
        return FinancialStatement(
            entity_id=entity.entity_id,
            statement_type=legacy_kind,
            period_type=period_type,
            period_start=resolved_start,
            period_end=resolved_end,
            fiscal_year=fiscal_year,
            fiscal_period=fiscal_period,
            reporting_basis=reporting_basis,
            currency=currency,
            filing_date=filing_date,
            accession_number=accession_number,
            source_document_id=source_document_id,
            amendment=amendment,
            source_id=statement.source_id,
            version=accession_number or source_document_id or "",
            line_items=tuple(items),
            metadata={
                "compat": "legacy_fixed_field_adapter",
                "warnings": warnings,
                "missing_fields": sorted(missing),
            },
        )
    except Exception as exc:
        raise LegacyCompatibilityError(
            f"canonical statement construction failed: {exc}"
        ) from exc


def canonical_statement_to_legacy(statement: FinancialStatement):
    """Convert canonical → legacy.  Only lossless conversions are allowed.

    Lossless rule for Decimal→float: ``Decimal(str(float(v))) == v``.  Any
    value that would be silently rounded rejects the whole conversion
    instead (B10.2 §8.2-7).
    """
    from langgraph_langchain.runtime.financial.models import (
        BalanceSheetStatement,
        CashFlowStatement,
        IncomeStatement,
    )

    if statement.amendment:
        raise LegacyCompatibilityError(
            "amended statements have no legacy representation; refusing to "
            "convert (legacy fixed fields cannot carry amendment semantics)"
        )
    if (
        statement.filing_date
        or statement.accession_number
        or statement.source_document_id
    ):
        # Metadata that legacy fields cannot represent — a silent drop is a
        # loss.  Callers must accept the disclosure explicitly via params.
        raise LegacyCompatibilityError(
            "canonical statement carries filing/source metadata (filing_date/"
            "accession_number/source_document_id) that legacy models cannot "
            "represent; refusing lossy conversion"
        )

    model_by_type = {
        "income": IncomeStatement,
        "balance": BalanceSheetStatement,
        "cash": CashFlowStatement,
    }
    legacy_model = model_by_type[statement.statement_type]
    allowed_concepts = set(_STATEMENT_CONCEPTS[statement.statement_type])
    seen_concepts = set()
    for item in statement.line_items:
        if item.canonical_concept not in allowed_concepts:
            raise LegacyCompatibilityError(
                "canonical line item "
                f"{item.canonical_concept!r} has no lossless legacy field; "
                "refusing to drop data"
            )
        if item.canonical_concept in seen_concepts:
            raise LegacyCompatibilityError(
                f"canonical line item {item.canonical_concept!r} appears more "
                "than once; legacy fixed fields cannot represent duplicates"
            )
        seen_concepts.add(item.canonical_concept)
        expected_kind = LEGACY_CONCEPT_KINDS[item.canonical_concept]
        if item.statistic_kind != expected_kind:
            raise LegacyCompatibilityError(
                f"concept {item.canonical_concept!r} requires legacy statistic_kind "
                f"{expected_kind!r}, got {item.statistic_kind!r}"
            )
        if item.unit.currency != statement.currency:
            raise LegacyCompatibilityError(
                f"concept {item.canonical_concept!r} currency "
                f"{item.unit.currency!r} does not match statement currency "
                f"{statement.currency!r}"
            )
        if expected_kind == "absolute_amount" and item.unit.numeric_unit != statement.currency:
            raise LegacyCompatibilityError(
                f"concept {item.canonical_concept!r} numeric_unit "
                f"{item.unit.numeric_unit!r} is not the monetary statement currency"
            )
        if expected_kind == "per_share" and item.unit.unit_denominator != "share":
            raise LegacyCompatibilityError(
                f"concept {item.canonical_concept!r} requires per-share denominator 'share'"
            )
    items_by_concept = {item.canonical_concept: item for item in statement.line_items}

    kwargs: dict = {
        "company_id": statement.entity_id,
        "currency": statement.currency,
        "source_id": statement.source_id,
    }
    # Legacy period string: FY serializes as the bare year (V9 normalized form).
    kwargs["period"] = (
        str(statement.fiscal_year)
        if statement.fiscal_period == "FY"
        else f"{statement.fiscal_year}{statement.fiscal_period}"
    )

    for concept in _STATEMENT_CONCEPTS[statement.statement_type]:
        item = items_by_concept.get(concept)
        if item is None:
            kwargs[concept] = None
            continue
        if item.value is None:
            kwargs[concept] = None
            continue
        as_float = float(item.value)
        if Decimal(str(as_float)) != item.value:
            raise LegacyCompatibilityError(
                f"concept {concept!r} value {item.value} is not exactly "
                "representable as float; lossless conversion refused"
            )
        kwargs[concept] = as_float

    return legacy_model(**kwargs)


def fact_key_from_canonical(
    statement: FinancialStatement,
    item: FinancialLineItem,
    *,
    market: str,
) -> FinancialStatementKey:
    """Build the 9-dimension identity key for one canonical line item."""
    if item.statistic_kind == "unknown":
        raise FinancialIdentityError(
            "facts with unknown statistic_kind must not be registered"
        )
    return FinancialStatementKey(
        market=market,
        entity_id=statement.entity_id,
        canonical_concept=item.canonical_concept,
        statement_type=statement.statement_type,
        period_type=statement.period_type,
        fiscal_year=statement.fiscal_year,
        fiscal_period=statement.fiscal_period,
        reporting_basis=statement.reporting_basis,
        currency=statement.currency,
    )
